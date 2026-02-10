package publisher

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"moto-news/internal/config"
	"moto-news/internal/formatter"
	"moto-news/internal/models"
)

type HugoPublisher struct {
	config    *config.HugoConfig
	formatter *formatter.MarkdownFormatter
}

func NewHugoPublisher(cfg *config.HugoConfig) *HugoPublisher {
	return &HugoPublisher{
		config:    cfg,
		formatter: formatter.NewMarkdownFormatter(),
	}
}

// Publish publishes an article to the Hugo site
func (p *HugoPublisher) Publish(article *models.Article) error {
	if article == nil {
		return fmt.Errorf("article cannot be nil")
	}

	if err := p.validateConfig(); err != nil {
		return err
	}

	// Get the full content path
	contentPath := filepath.Join(p.config.Path, p.config.ContentDir)

	// Get the file path for this article
	filePath := p.formatter.GetFilePath(article, contentPath)

	// Ensure directory exists
	dir := filepath.Dir(filePath)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return fmt.Errorf("failed to create directory %s: %w", dir, err)
	}

	// Format the article
	content := p.formatter.Format(article)

	// Write the file
	if err := os.WriteFile(filePath, []byte(content), 0644); err != nil {
		return fmt.Errorf("failed to write file %s: %w", filePath, err)
	}

	fmt.Printf("Published: %s\n", filePath)
	return nil
}

// PublishMultiple publishes multiple articles and optionally commits
func (p *HugoPublisher) PublishMultiple(articles []*models.Article) error {
	for _, article := range articles {
		if err := p.Publish(article); err != nil {
			return err
		}
	}

	if p.config.AutoCommit && len(articles) > 0 {
		return p.GitCommit(fmt.Sprintf("Add %d new articles", len(articles)))
	}

	return nil
}

// GitCommit commits changes to git.
// Uses cmd.Dir instead of os.Chdir to avoid race conditions.
func (p *HugoPublisher) GitCommit(message string) error {
	if err := p.validateConfig(); err != nil {
		return err
	}

	dir := p.config.Path

	// Git add
	addCmd := exec.Command("git", "add", "-A")
	addCmd.Dir = dir
	if output, err := addCmd.CombinedOutput(); err != nil {
		return fmt.Errorf("git add failed: %s: %w", string(output), err)
	}

	// Check if there are changes to commit
	statusCmd := exec.Command("git", "status", "--porcelain")
	statusCmd.Dir = dir
	statusOutput, err := statusCmd.Output()
	if err != nil {
		return fmt.Errorf("git status failed: %w", err)
	}

	if len(statusOutput) == 0 {
		fmt.Println("No changes to commit")
		return nil
	}

	// Git commit
	commitCmd := exec.Command("git", "commit", "-m", message)
	commitCmd.Dir = dir
	if output, err := commitCmd.CombinedOutput(); err != nil {
		return fmt.Errorf("git commit failed: %s: %w", string(output), err)
	}

	fmt.Printf("Committed: %s\n", message)
	return nil
}

// GitPull pulls latest changes from remote
func (p *HugoPublisher) GitPull() error {
	if err := p.validateConfig(); err != nil {
		return err
	}

	gitDir := filepath.Join(p.config.Path, ".git")

	// Check if .git directory exists (it's a git repo)
	if _, err := os.Stat(gitDir); os.IsNotExist(err) {
		// Not a git repo - need to clone
		if p.config.GitRepo == "" {
			return fmt.Errorf("git_repo not configured")
		}

		// Remove existing directory if it exists — with safety check
		if _, err := os.Stat(p.config.Path); err == nil {
			if err := p.safeRemoveAll(); err != nil {
				return err
			}
		}

		fmt.Printf("Cloning repository %s...\n", p.config.GitRepo)
		cloneCmd := exec.Command("git", "clone", p.config.GitRepo, p.config.Path)
		if output, err := cloneCmd.CombinedOutput(); err != nil {
			return fmt.Errorf("git clone failed: %s: %w", string(output), err)
		}
		fmt.Println("Repository cloned successfully")
		return nil
	}

	if p.config.GitRemote == "" || p.config.GitBranch == "" {
		return fmt.Errorf("git_remote and git_branch must be configured for pull")
	}

	dir := p.config.Path

	fmt.Println("Pulling latest changes...")
	pullCmd := exec.Command("git", "pull", p.config.GitRemote, p.config.GitBranch)
	pullCmd.Dir = dir
	if output, err := pullCmd.CombinedOutput(); err != nil {
		return fmt.Errorf("git pull failed: %s: %w", string(output), err)
	}

	fmt.Println("Pull complete")
	return nil
}

// GitPush pushes changes to remote
func (p *HugoPublisher) GitPush() error {
	if err := p.validateConfig(); err != nil {
		return err
	}

	if p.config.GitRemote == "" || p.config.GitBranch == "" {
		return fmt.Errorf("git_remote and git_branch must be configured for push")
	}

	dir := p.config.Path

	pushCmd := exec.Command("git", "push", p.config.GitRemote, p.config.GitBranch)
	pushCmd.Dir = dir
	if output, err := pushCmd.CombinedOutput(); err != nil {
		return fmt.Errorf("git push failed: %s: %w", string(output), err)
	}

	fmt.Println("Pushed to remote")
	return nil
}

// GetContentPath returns the full path to the content directory
func (p *HugoPublisher) GetContentPath() string {
	return filepath.Join(p.config.Path, p.config.ContentDir)
}

// validateConfig checks that the Hugo config has a valid path.
func (p *HugoPublisher) validateConfig() error {
	if p.config.Path == "" {
		return fmt.Errorf("hugo.path is not configured")
	}
	return nil
}

// safeRemoveAll removes p.config.Path only if it is not the current directory
// or a parent of it. Prevents accidental deletion of the project root.
func (p *HugoPublisher) safeRemoveAll() error {
	absPath, err := filepath.Abs(p.config.Path)
	if err != nil {
		return fmt.Errorf("failed to resolve blog path: %w", err)
	}

	cwd, err := os.Getwd()
	if err != nil {
		return fmt.Errorf("failed to get working directory: %w", err)
	}

	// Refuse to remove if the target is or contains the current working dir
	if absPath == filepath.Clean(cwd) || strings.HasPrefix(cwd, absPath+string(filepath.Separator)) {
		return fmt.Errorf("refusing to remove %s: it contains or equals the current directory %s", absPath, cwd)
	}

	fmt.Printf("Removing existing non-git directory %s...\n", p.config.Path)
	if err := os.RemoveAll(p.config.Path); err != nil {
		return fmt.Errorf("failed to remove directory: %w", err)
	}
	return nil
}
