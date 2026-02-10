package fetcher

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strings"
	"time"

	"github.com/PuerkitoBio/goquery"
	"moto-news/internal/models"
)

type ArticleScraper struct {
	client *http.Client
}

func NewArticleScraper() *ArticleScraper {
	return &ArticleScraper{
		client: &http.Client{
			Timeout: 30 * time.Second,
		},
	}
}

// jsonLDArticle represents the JSON-LD structured data on article pages
type jsonLDArticle struct {
	Type           string      `json:"@type"`
	Headline       string      `json:"headline"`
	ArticleBody    string      `json:"articleBody"`
	ArticleSection string      `json:"articleSection"`
	DatePublished  string      `json:"datePublished"`
	Image          interface{} `json:"image"`
	Keywords       interface{} `json:"keywords"`
	Author         interface{} `json:"author"`
}

// ScrapeArticle fetches the full content of an article from its URL
func (s *ArticleScraper) ScrapeArticle(article *models.Article) error {
	if article == nil || article.SourceURL == "" {
		return fmt.Errorf("article has no source URL")
	}

	req, err := http.NewRequest("GET", article.SourceURL, nil)
	if err != nil {
		return fmt.Errorf("failed to create request for %s: %w", article.SourceURL, err)
	}

	req.Header.Set("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
	req.Header.Set("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
	req.Header.Set("Accept-Language", "en-US,en;q=0.5")

	resp, err := s.client.Do(req)
	if err != nil {
		return fmt.Errorf("failed to fetch %s: %w", article.SourceURL, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		// Drain body to allow connection reuse
		io.Copy(io.Discard, resp.Body)
		return fmt.Errorf("unexpected status %d for %s", resp.StatusCode, article.SourceURL)
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("failed to read body from %s: %w", article.SourceURL, err)
	}

	htmlStr := string(body)

	// Strategy 1: Extract from JSON-LD structured data (most reliable)
	content, imageURL, category, tags := s.extractFromJSONLD(htmlStr)

	// Strategy 2: Fallback to HTML scraping if JSON-LD didn't work
	if content == "" {
		var htmlCategory string
		content, imageURL, htmlCategory, tags = s.extractFromHTML(htmlStr)
		if category == "" {
			category = htmlCategory
		}
	}

	// Update article with scraped content
	if content != "" {
		article.Content = strings.TrimSpace(content)
	}

	if imageURL != "" && article.ImageURL == "" {
		article.ImageURL = imageURL
	}

	if category != "" && article.Category == "" {
		article.Category = category
	}

	if len(tags) > 0 {
		article.Tags = uniqueStrings(tags)
	}

	return nil
}

// extractFromJSONLD extracts article content from JSON-LD structured data
func (s *ArticleScraper) extractFromJSONLD(html string) (content, imageURL, category string, tags []string) {
	// Find all JSON-LD blocks
	re := regexp.MustCompile(`(?s)<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>`)
	matches := re.FindAllStringSubmatch(html, -1)

	for _, match := range matches {
		if len(match) < 2 {
			continue
		}

		var data jsonLDArticle
		if err := json.Unmarshal([]byte(match[1]), &data); err != nil {
			continue
		}

		// Check if this is an Article type with body content
		if data.ArticleBody == "" {
			continue
		}

		// Clean the article body - remove related articles section
		content = s.cleanArticleBody(data.ArticleBody)

		// Extract category from articleSection
		category = data.ArticleSection

		// Extract image URL
		switch img := data.Image.(type) {
		case string:
			imageURL = img
		case []interface{}:
			if len(img) > 0 {
				if imgStr, ok := img[0].(string); ok {
					imageURL = imgStr
				}
			}
		}

		// Extract keywords/tags — filter out generic site-wide categories
		switch kw := data.Keywords.(type) {
		case []interface{}:
			for _, k := range kw {
				if kStr, ok := k.(string); ok && !isGenericCategory(kStr) {
					tags = append(tags, kStr)
				}
			}
		case string:
			for _, k := range strings.Split(kw, ",") {
				k = strings.TrimSpace(k)
				if k != "" && !isGenericCategory(k) {
					tags = append(tags, k)
				}
			}
		}

		break
	}

	return
}

// extractFromHTML extracts article content by parsing HTML (fallback)
func (s *ArticleScraper) extractFromHTML(htmlStr string) (content, imageURL, category string, tags []string) {
	doc, err := goquery.NewDocumentFromReader(strings.NewReader(htmlStr))
	if err != nil {
		return
	}

	var paragraphs []string

	// Primary selector: div.postBody (RideApart)
	doc.Find("div.postBody").Each(func(i int, sel *goquery.Selection) {
		sel.Find("p").Each(func(j int, p *goquery.Selection) {
			text := strings.TrimSpace(p.Text())
			if text != "" && !isBoilerplate(text) {
				paragraphs = append(paragraphs, text)
			}
		})
	})

	// Alternative selectors
	if len(paragraphs) == 0 {
		selectors := []string{
			"article.article-content",
			"div.article-body",
			"div.content-body",
			"div[class*='article'] p",
			"main p",
		}
		for _, selector := range selectors {
			doc.Find(selector).Each(func(i int, sel *goquery.Selection) {
				if strings.Contains(selector, " p") {
					// Selector already includes p
					text := strings.TrimSpace(sel.Text())
					if text != "" && len(text) > 50 && !isBoilerplate(text) {
						paragraphs = append(paragraphs, text)
					}
				} else {
					sel.Find("p").Each(func(j int, p *goquery.Selection) {
						text := strings.TrimSpace(p.Text())
						if text != "" && !isBoilerplate(text) {
							paragraphs = append(paragraphs, text)
						}
					})
				}
			})
			if len(paragraphs) > 0 {
				break
			}
		}
	}

	if len(paragraphs) > 0 {
		content = strings.Join(paragraphs, "\n\n")
	}

	// Extract featured image
	doc.Find("meta[property='og:image']").Each(func(i int, sel *goquery.Selection) {
		if imageURL == "" {
			if val, exists := sel.Attr("content"); exists {
				imageURL = val
			}
		}
	})

	// Extract tags
	doc.Find("a[href*='/tag/'], a[href*='/category/'], span.tag").Each(func(i int, sel *goquery.Selection) {
		tag := strings.TrimSpace(sel.Text())
		if tag != "" && len(tag) < 50 {
			tags = append(tags, tag)
		}
	})

	return
}

// cleanArticleBody removes trailing related article text and cleans up the body
func (s *ArticleScraper) cleanArticleBody(body string) string {
	// Split by newlines
	paragraphs := strings.Split(body, "\n")
	var cleaned []string

	for _, p := range paragraphs {
		p = strings.TrimSpace(p)
		if p == "" {
			continue
		}
		if isBoilerplate(p) {
			continue
		}
		// Skip common section headers that indicate the end of article content
		lower := strings.ToLower(p)
		if lower == "more fun off road" || lower == "recommended for you" ||
			strings.HasPrefix(lower, "more ") && len(p) < 50 {
			continue
		}
		// Skip list items like "- The RideApart Team"
		if strings.HasPrefix(p, "- The ") && len(p) < 50 {
			continue
		}
		cleaned = append(cleaned, p)
	}

	// Remove trailing very short paragraphs (likely related article titles)
	// Work backwards from the end
	for len(cleaned) > 1 {
		last := cleaned[len(cleaned)-1]
		// Related article titles are usually short standalone lines without periods
		if len(last) < 120 && !strings.Contains(last, ".") {
			cleaned = cleaned[:len(cleaned)-1]
		} else {
			break
		}
	}

	return strings.Join(cleaned, "\n\n")
}

// isGenericCategory returns true if the keyword is a generic site-wide category
func isGenericCategory(kw string) bool {
	generic := map[string]bool{
		"electric motorcycles": true,
		"industry":            true,
		"adventure & dual-sport": true,
		"racing":              true,
		"gear news":           true,
		"technology":          true,
		"reviews":             true,
		"hunting":             true,
		"gear":                true,
		"products & services": true,
		"positions":           true,
		"experiences":         true,
		"travel":              true,
		"rants":               true,
		"explainers":          true,
		"data deep dives":     true,
		"standard & naked":    true,
		"off road":            true,
		"pwcs":                true,
		"real racers":         true,
		"news":                true,
		"motogp":              true,
		"utv":                 true,
		"motorcycle culture":  true,
		"recalls":             true,
	}
	return generic[strings.ToLower(kw)]
}

// isBoilerplate checks if text is likely boilerplate content
func isBoilerplate(text string) bool {
	boilerplates := []string{
		"subscribe",
		"newsletter",
		"sign up",
		"follow us",
		"share this",
		"advertisement",
		"sponsored",
		"cookie",
		"privacy policy",
		"terms of service",
		"all rights reserved",
		"for more info",
		"stay informed",
		"we want your opinion",
		"what would you like to see on",
		"the rideapart team",
		"got a tip for us",
	}

	lower := strings.ToLower(text)
	for _, bp := range boilerplates {
		if strings.Contains(lower, bp) && len(text) < 200 {
			return true
		}
	}
	return false
}

// uniqueStrings returns unique strings from a slice
func uniqueStrings(input []string) []string {
	seen := make(map[string]bool)
	var result []string
	for _, s := range input {
		if !seen[s] {
			seen[s] = true
			result = append(result, s)
		}
	}
	return result
}
