package fetcher

import (
	"fmt"
	"time"

	"github.com/gosimple/slug"
	"github.com/mmcdole/gofeed"
	"moto-news/internal/models"
)

type RSSFetcher struct {
	parser *gofeed.Parser
}

func NewRSSFetcher() *RSSFetcher {
	return &RSSFetcher{
		parser: gofeed.NewParser(),
	}
}

// FetchFeed fetches articles from an RSS feed URL
func (f *RSSFetcher) FetchFeed(feedURL string, sourceSite string) ([]*models.Article, error) {
	feed, err := f.parser.ParseURL(feedURL)
	if err != nil {
		return nil, fmt.Errorf("failed to parse feed %s: %w", feedURL, err)
	}

	var articles []*models.Article
	for _, item := range feed.Items {
		article := f.itemToArticle(item, sourceSite)
		articles = append(articles, article)
	}

	return articles, nil
}

func (f *RSSFetcher) itemToArticle(item *gofeed.Item, sourceSite string) *models.Article {
	article := &models.Article{
		SourceURL:   item.Link,
		SourceSite:  sourceSite,
		Title:       item.Title,
		Description: item.Description,
		FetchedAt:   time.Now(),
	}

	// Parse published date
	if item.PublishedParsed != nil {
		article.PublishedAt = *item.PublishedParsed
	} else if item.UpdatedParsed != nil {
		article.PublishedAt = *item.UpdatedParsed
	} else {
		article.PublishedAt = time.Now()
	}

	// Extract author
	if len(item.Authors) > 0 {
		article.Author = item.Authors[0].Name
	} else if item.Author != nil {
		article.Author = item.Author.Name
	}

	// Extract category
	if len(item.Categories) > 0 {
		article.Category = item.Categories[0]
		article.Tags = item.Categories
	}

	// Extract image from enclosures or media
	if item.Image != nil {
		article.ImageURL = item.Image.URL
	} else if len(item.Enclosures) > 0 {
		for _, enc := range item.Enclosures {
			if enc.Type == "image/jpeg" || enc.Type == "image/png" || enc.Type == "image/webp" {
				article.ImageURL = enc.URL
				break
			}
		}
	}

	// Generate slug from title
	article.Slug = slug.Make(item.Title)
	if len(article.Slug) > 80 {
		article.Slug = article.Slug[:80]
	}

	return article
}

// FetchMultipleFeeds fetches articles from multiple feed URLs
func (f *RSSFetcher) FetchMultipleFeeds(feedURLs []string, sourceSite string) ([]*models.Article, error) {
	var allArticles []*models.Article

	for _, feedURL := range feedURLs {
		articles, err := f.FetchFeed(feedURL, sourceSite)
		if err != nil {
			// Log error but continue with other feeds
			fmt.Printf("Warning: failed to fetch %s: %v\n", feedURL, err)
			continue
		}
		allArticles = append(allArticles, articles...)
	}

	return allArticles, nil
}
