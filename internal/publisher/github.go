package publisher

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"moto-news/internal/config"
	"moto-news/internal/formatter"
	"moto-news/internal/models"
)

// GitHubPublisher publishes articles directly via GitHub Contents API.
// No git clone/push needed — just HTTP requests.
// Automatically triggers GitHub Actions workflow on push.
type GitHubPublisher struct {
	config    *config.HugoConfig
	formatter *formatter.MarkdownFormatter
	token     string
	owner     string
	repo      string
	branch    string
	client    *http.Client
}

// NewGitHubPublisher creates a publisher that uses GitHub API.
// Token is read from GITHUB_TOKEN env var.
// Repo is parsed from git_repo config (https://github.com/owner/repo.git).
func NewGitHubPublisher(cfg *config.HugoConfig) *GitHubPublisher {
	token := os.Getenv("GITHUB_TOKEN")
	owner, repo := parseGitHubRepo(cfg.GitRepo)

	branch := cfg.GitBranch
	if branch == "" {
		branch = "main"
	}

	return &GitHubPublisher{
		config:    cfg,
		formatter: formatter.NewMarkdownFormatter(),
		token:     token,
		owner:     owner,
		repo:      repo,
		branch:    branch,
		client:    &http.Client{Timeout: 30 * time.Second},
	}
}

// IsAvailable returns true if GitHub token is configured
func (p *GitHubPublisher) IsAvailable() bool {
	return p.token != "" && p.owner != "" && p.repo != ""
}

// Publish formats an article and pushes it to GitHub via API
func (p *GitHubPublisher) Publish(article *models.Article) error {
	if !p.IsAvailable() {
		return fmt.Errorf("GitHub publisher not configured (GITHUB_TOKEN not set)")
	}

	// Format the article to markdown
	content := p.formatter.Format(article)

	// Build the file path (e.g. content/posts/2026/02/slug.md)
	filePath := p.formatter.GetFilePath(article, p.config.ContentDir)

	// Push to GitHub
	message := fmt.Sprintf("Add article: %s", article.TitleRU)
	if article.TitleRU == "" {
		message = fmt.Sprintf("Add article: %s", article.Title)
	}

	if err := p.putFile(filePath, content, message); err != nil {
		return fmt.Errorf("failed to push %s: %w", filePath, err)
	}

	fmt.Printf("Published to GitHub: %s\n", filePath)
	return nil
}

// PublishMultiple publishes multiple articles in a single commit using Git Trees API
func (p *GitHubPublisher) PublishMultiple(articles []*models.Article) error {
	if !p.IsAvailable() {
		return fmt.Errorf("GitHub publisher not configured (GITHUB_TOKEN not set)")
	}

	if len(articles) == 0 {
		return nil
	}

	// Collect files
	var files []treeFile
	for _, article := range articles {
		content := p.formatter.Format(article)
		filePath := p.formatter.GetFilePath(article, p.config.ContentDir)
		files = append(files, treeFile{path: filePath, content: content})
	}

	message := fmt.Sprintf("Add %d new articles", len(articles))
	return p.commitMultipleFiles(files, message)
}

// --- GitHub API types ---

type contentsRequest struct {
	Message string `json:"message"`
	Content string `json:"content"`
	Branch  string `json:"branch"`
	SHA     string `json:"sha,omitempty"`
}

type contentsResponse struct {
	SHA string `json:"sha"`
}

type treeFile struct {
	path    string
	content string
}

type refResponse struct {
	Object struct {
		SHA string `json:"sha"`
	} `json:"object"`
}

type commitResponse struct {
	SHA  string `json:"sha"`
	Tree struct {
		SHA string `json:"sha"`
	} `json:"tree"`
}

type treeEntry struct {
	Path    string `json:"path"`
	Mode    string `json:"mode"`
	Type    string `json:"type"`
	Content string `json:"content"`
}

type createTreeRequest struct {
	BaseTree string      `json:"base_tree"`
	Tree     []treeEntry `json:"tree"`
}

type createTreeResponse struct {
	SHA string `json:"sha"`
}

type createCommitRequest struct {
	Message string   `json:"message"`
	Tree    string   `json:"tree"`
	Parents []string `json:"parents"`
}

type createCommitResponse struct {
	SHA string `json:"sha"`
}

type updateRefRequest struct {
	SHA string `json:"sha"`
}

// --- GitHub API methods ---

func (p *GitHubPublisher) apiURL(path string) string {
	return fmt.Sprintf("https://api.github.com/repos/%s/%s%s", p.owner, p.repo, path)
}

func (p *GitHubPublisher) doRequest(method, url string, body interface{}) ([]byte, error) {
	var bodyReader io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		bodyReader = bytes.NewReader(data)
	}

	req, err := http.NewRequest(method, url, bodyReader)
	if err != nil {
		return nil, err
	}

	req.Header.Set("Authorization", "Bearer "+p.token)
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("X-GitHub-Api-Version", "2022-11-28")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}

	resp, err := p.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("GitHub API error %d: %s", resp.StatusCode, string(respBody[:min(500, len(respBody))]))
	}

	return respBody, nil
}

// putFile creates or updates a single file via Contents API
func (p *GitHubPublisher) putFile(filePath, content, message string) error {
	url := p.apiURL("/contents/" + filePath)

	// Check if file exists (to get SHA for update)
	var existingSHA string
	data, err := p.doRequest("GET", url+"?ref="+p.branch, nil)
	if err == nil {
		var existing contentsResponse
		if json.Unmarshal(data, &existing) == nil {
			existingSHA = existing.SHA
		}
	}

	req := contentsRequest{
		Message: message,
		Content: base64.StdEncoding.EncodeToString([]byte(content)),
		Branch:  p.branch,
	}
	if existingSHA != "" {
		req.SHA = existingSHA
	}

	_, err = p.doRequest("PUT", url, req)
	return err
}

// commitMultipleFiles creates a single commit with multiple files using Git Trees API
func (p *GitHubPublisher) commitMultipleFiles(files []treeFile, message string) error {
	// 1. Get latest commit SHA on branch
	refData, err := p.doRequest("GET", p.apiURL("/git/ref/heads/"+p.branch), nil)
	if err != nil {
		return fmt.Errorf("get ref: %w", err)
	}
	var ref refResponse
	if err := json.Unmarshal(refData, &ref); err != nil {
		return fmt.Errorf("parse ref: %w", err)
	}
	latestCommitSHA := ref.Object.SHA

	// 2. Get the tree SHA of that commit
	commitData, err := p.doRequest("GET", p.apiURL("/git/commits/"+latestCommitSHA), nil)
	if err != nil {
		return fmt.Errorf("get commit: %w", err)
	}
	var commit commitResponse
	if err := json.Unmarshal(commitData, &commit); err != nil {
		return fmt.Errorf("parse commit: %w", err)
	}
	baseTreeSHA := commit.Tree.SHA

	// 3. Create new tree with all files
	var entries []treeEntry
	for _, f := range files {
		entries = append(entries, treeEntry{
			Path:    f.path,
			Mode:    "100644",
			Type:    "blob",
			Content: f.content,
		})
	}

	treeReq := createTreeRequest{
		BaseTree: baseTreeSHA,
		Tree:     entries,
	}
	treeData, err := p.doRequest("POST", p.apiURL("/git/trees"), treeReq)
	if err != nil {
		return fmt.Errorf("create tree: %w", err)
	}
	var newTree createTreeResponse
	if err := json.Unmarshal(treeData, &newTree); err != nil {
		return fmt.Errorf("parse tree: %w", err)
	}

	// 4. Create commit
	commitReq := createCommitRequest{
		Message: message,
		Tree:    newTree.SHA,
		Parents: []string{latestCommitSHA},
	}
	newCommitData, err := p.doRequest("POST", p.apiURL("/git/commits"), commitReq)
	if err != nil {
		return fmt.Errorf("create commit: %w", err)
	}
	var newCommit createCommitResponse
	if err := json.Unmarshal(newCommitData, &newCommit); err != nil {
		return fmt.Errorf("parse commit: %w", err)
	}

	// 5. Update branch ref
	updateReq := updateRefRequest{SHA: newCommit.SHA}
	_, err = p.doRequest("PATCH", p.apiURL("/git/refs/heads/"+p.branch), updateReq)
	if err != nil {
		return fmt.Errorf("update ref: %w", err)
	}

	fmt.Printf("Committed %d files to GitHub (%s/%s@%s)\n", len(files), p.owner, p.repo, p.branch)
	return nil
}

// parseGitHubRepo extracts owner and repo from a GitHub URL
func parseGitHubRepo(gitRepo string) (owner, repo string) {
	// Handle: https://github.com/owner/repo.git
	//         git@github.com:owner/repo.git
	//         owner/repo
	s := gitRepo
	s = strings.TrimSuffix(s, ".git")
	s = strings.TrimPrefix(s, "https://github.com/")
	s = strings.TrimPrefix(s, "http://github.com/")
	s = strings.TrimPrefix(s, "git@github.com:")

	parts := strings.SplitN(s, "/", 2)
	if len(parts) == 2 {
		return parts[0], parts[1]
	}
	return "", ""
}

