package fetcher

import (
	"fmt"
	"strings"
	"time"

	"github.com/gocolly/colly/v2"
	"moto-news/internal/models"
)

type ArticleScraper struct {
	collector *colly.Collector
}

func NewArticleScraper() *ArticleScraper {
	c := colly.NewCollector(
		colly.AllowedDomains("www.rideapart.com", "rideapart.com"),
		colly.MaxDepth(1),
	)

	// Set reasonable limits
	c.Limit(&colly.LimitRule{
		DomainGlob:  "*",
		Parallelism: 1,
		Delay:       2 * time.Second,
	})

	// Set user agent
	c.UserAgent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

	return &ArticleScraper{
		collector: c,
	}
}

// ScrapeArticle fetches the full content of an article from its URL
func (s *ArticleScraper) ScrapeArticle(article *models.Article) error {
	c := s.collector.Clone()

	var content strings.Builder
	var imageURL string
	var tags []string

	// Extract main article content for RideApart
	c.OnHTML("article.article-content, div.article-body, div.content-body", func(e *colly.HTMLElement) {
		// Get paragraphs
		e.ForEach("p", func(_ int, p *colly.HTMLElement) {
			text := strings.TrimSpace(p.Text)
			if text != "" && !isBoilerplate(text) {
				content.WriteString(text)
				content.WriteString("\n\n")
			}
		})
	})

	// Alternative selector for article content
	c.OnHTML("div[class*='article'] p, main p", func(e *colly.HTMLElement) {
		if content.Len() == 0 {
			text := strings.TrimSpace(e.Text)
			if text != "" && len(text) > 50 && !isBoilerplate(text) {
				content.WriteString(text)
				content.WriteString("\n\n")
			}
		}
	})

	// Extract featured image
	c.OnHTML("img.featured-image, img[class*='hero'], article img:first-of-type, meta[property='og:image']", func(e *colly.HTMLElement) {
		if imageURL == "" {
			if e.Name == "meta" {
				imageURL = e.Attr("content")
			} else {
				imageURL = e.Attr("src")
				if imageURL == "" {
					imageURL = e.Attr("data-src")
				}
			}
		}
	})

	// Extract tags
	c.OnHTML("a[href*='/tag/'], a[href*='/category/'], span.tag", func(e *colly.HTMLElement) {
		tag := strings.TrimSpace(e.Text)
		if tag != "" && len(tag) < 50 {
			tags = append(tags, tag)
		}
	})

	// Handle errors
	c.OnError(func(r *colly.Response, err error) {
		fmt.Printf("Scraping error for %s: %v\n", r.Request.URL, err)
	})

	err := c.Visit(article.SourceURL)
	if err != nil {
		return fmt.Errorf("failed to scrape %s: %w", article.SourceURL, err)
	}

	// Update article with scraped content
	if content.Len() > 0 {
		article.Content = strings.TrimSpace(content.String())
	}

	if imageURL != "" && article.ImageURL == "" {
		article.ImageURL = imageURL
	}

	if len(tags) > 0 && len(article.Tags) == 0 {
		article.Tags = uniqueStrings(tags)
	}

	return nil
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
