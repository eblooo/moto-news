package service

import (
	"context"
	"fmt"
	"time"

	"moto-news/internal/config"
	"moto-news/internal/fetcher"
	"moto-news/internal/models"
	"moto-news/internal/publisher"
	"moto-news/internal/storage"
	"moto-news/internal/translator"
)

// Result holds the outcome of an operation
type Result struct {
	Success bool   `json:"success"`
	Message string `json:"message"`
	Data    any    `json:"data,omitempty"`
}

// FetchResult holds fetch operation results
type FetchResult struct {
	NewArticles     int `json:"new_articles"`
	SkippedArticles int `json:"skipped_articles"`
	Errors          int `json:"errors"`
}

// TranslateResult holds translate operation results
type TranslateResult struct {
	Translated int `json:"translated"`
	Total      int `json:"total"`
	Errors     int `json:"errors"`
}

// PublishResult holds publish operation results
type PublishResult struct {
	Published int `json:"published"`
	Total     int `json:"total"`
	Errors    int `json:"errors"`
}

// RescrapeResult holds rescrape operation results
type RescrapeResult struct {
	Rescraped int `json:"rescraped"`
	Total     int `json:"total"`
	Errors    int `json:"errors"`
}

// StatsResult holds stats
type StatsResult struct {
	Total      int `json:"total"`
	Translated int `json:"translated"`
	Published  int `json:"published"`
	Pending    int `json:"pending_translation"`
	Unpublished int `json:"pending_publishing"`
}

// PipelineResult holds results from a full pipeline run
type PipelineResult struct {
	Fetch     *FetchResult     `json:"fetch"`
	Translate *TranslateResult `json:"translate"`
	Publish   *PublishResult   `json:"publish"`
}

// Service provides all business logic operations
type Service struct {
	cfg   *config.Config
	store *storage.SQLiteStorage
}

// NewService creates a new service instance
func NewService(cfg *config.Config, store *storage.SQLiteStorage) *Service {
	return &Service{
		cfg:   cfg,
		store: store,
	}
}

// Fetch fetches new articles from RSS feeds
func (s *Service) Fetch() (*FetchResult, error) {
	rssFetcher := fetcher.NewRSSFetcher()
	scraper := fetcher.NewArticleScraper()

	result := &FetchResult{}

	for _, source := range s.cfg.Sources {
		if !source.Enabled {
			continue
		}

		articles, err := rssFetcher.FetchMultipleFeeds(source.Feeds, source.Name)
		if err != nil {
			fmt.Printf("Warning: error fetching %s: %v\n", source.Name, err)
			result.Errors++
			continue
		}

		fmt.Printf("Found %d articles in feed\n", len(articles))
		for i, article := range articles {
			exists, err := s.store.ArticleExists(article.SourceURL)
			if err != nil {
				fmt.Printf("  ✗ Error checking article: %v\n", err)
				result.Errors++
				continue
			}

			if exists {
				result.SkippedArticles++
				continue
			}

			fmt.Printf("  [%d/%d] Scraping: %s\n", i+1, len(articles), article.Title)
			if err := scraper.ScrapeArticle(article); err != nil {
				fmt.Printf("    ✗ Warning: failed to scrape: %v\n", err)
			}

			if err := s.store.InsertArticle(article); err != nil {
				fmt.Printf("    ✗ Error saving article: %v\n", err)
				result.Errors++
				continue
			}

			result.NewArticles++
			fmt.Printf("    ✓ Saved\n")

			time.Sleep(1 * time.Second)
		}
	}

	fmt.Printf("\nDone! New: %d, Skipped: %d, Errors: %d\n", result.NewArticles, result.SkippedArticles, result.Errors)

	return result, nil
}

// Translate translates untranslated articles
func (s *Service) Translate(limit int) (*TranslateResult, error) {
	articles, err := s.store.GetUntranslatedArticles(limit)
	if err != nil {
		return nil, fmt.Errorf("failed to get articles: %w", err)
	}

	result := &TranslateResult{
		Total: len(articles),
	}

	if len(articles) == 0 {
		return result, nil
	}

	trans, err := s.createTranslator()
	if err != nil {
		return nil, err
	}

	fmt.Printf("Using translator: %s\n", trans.Name())
	fmt.Printf("Articles to translate: %d\n\n", len(articles))

	ctx := context.Background()
	totalStart := time.Now()

	// Collect translated articles for batch publish
	var translatedArticles []*models.Article

	for i, article := range articles {
		articleStart := time.Now()
		fmt.Printf("[%d/%d] Translating: %s\n", i+1, len(articles), article.Title)

		titleRU, err := trans.TranslateTitle(ctx, article.Title)
		if err != nil {
			fmt.Printf("  ✗ Error translating title: %v\n", err)
			result.Errors++
			continue
		}
		article.TitleRU = titleRU

		if article.Content != "" {
			contentRU, err := trans.Translate(ctx, article.Content)
			if err != nil {
				fmt.Printf("  ✗ Error translating content: %v\n", err)
				result.Errors++
				continue
			}
			article.ContentRU = contentRU
		}

		now := time.Now()
		article.TranslatedAt = &now

		if err := s.store.UpdateArticle(article); err != nil {
			fmt.Printf("  ✗ Error saving translation: %v\n", err)
			result.Errors++
			continue
		}

		elapsed := time.Since(articleStart).Round(time.Second)
		result.Translated++
		fmt.Printf("  ✓ Перевод: %s (%s)\n", article.TitleRU, elapsed)

		translatedArticles = append(translatedArticles, article)
	}

	totalElapsed := time.Since(totalStart).Round(time.Second)
	fmt.Printf("\nTranslated %d of %d articles (errors: %d) in %s\n",
		result.Translated, result.Total, result.Errors, totalElapsed)

	// Publish all translated articles
	if len(translatedArticles) > 0 {
		ghPub := publisher.NewGitHubPublisher(&s.cfg.Hugo)
		if ghPub.IsAvailable() {
			// Batch push via GitHub API (single commit)
			fmt.Printf("\nPublishing %d articles via GitHub API...\n", len(translatedArticles))
			if err := ghPub.PublishMultiple(translatedArticles); err != nil {
				fmt.Printf("  ✗ GitHub publish error: %v\n", err)
			} else {
				for _, a := range translatedArticles {
					a.PublishedToHugo = true
					s.store.UpdateArticle(a)
				}
				fmt.Printf("  ✓ Published %d articles to GitHub\n", len(translatedArticles))
			}
		} else {
			// Fallback to local file + git
			fmt.Println("\nGITHUB_TOKEN not set, using local git publisher...")
			pub := publisher.NewHugoPublisher(&s.cfg.Hugo)
			published := 0
			for _, article := range translatedArticles {
				if err := pub.Publish(article); err != nil {
					fmt.Printf("  ✗ Error publishing: %v\n", err)
				} else {
					article.PublishedToHugo = true
					s.store.UpdateArticle(article)
					published++
				}
			}
			if s.cfg.Hugo.AutoCommit && published > 0 {
				if err := pub.GitCommit(fmt.Sprintf("Add %d new articles", published)); err != nil {
					fmt.Printf("Warning: git commit failed: %v\n", err)
				}
			}
		}
	}

	return result, nil
}

// Publish publishes translated articles to Hugo blog
func (s *Service) Publish(limit int) (*PublishResult, error) {
	articles, err := s.store.GetUnpublishedArticles(limit)
	if err != nil {
		return nil, fmt.Errorf("failed to get articles: %w", err)
	}

	result := &PublishResult{
		Total: len(articles),
	}

	if len(articles) == 0 {
		return result, nil
	}

	fmt.Printf("Articles to publish: %d\n\n", len(articles))

	ghPub := publisher.NewGitHubPublisher(&s.cfg.Hugo)
	if ghPub.IsAvailable() {
		// Batch push via GitHub API
		fmt.Println("Publishing via GitHub API...")
		if err := ghPub.PublishMultiple(articles); err != nil {
			fmt.Printf("  ✗ GitHub publish error: %v\n", err)
			result.Errors = len(articles)
			return result, nil
		}
		for _, a := range articles {
			a.PublishedToHugo = true
			s.store.UpdateArticle(a)
			result.Published++
		}
		fmt.Printf("  ✓ Published %d articles to GitHub\n", result.Published)
	} else {
		// Fallback to local git
		fmt.Println("GITHUB_TOKEN not set, using local git publisher...")
		pub := publisher.NewHugoPublisher(&s.cfg.Hugo)

		for i, article := range articles {
			fmt.Printf("[%d/%d] Publishing: %s\n", i+1, len(articles), article.TitleRU)
			if err := pub.Publish(article); err != nil {
				fmt.Printf("  ✗ Error: %v\n", err)
				result.Errors++
				continue
			}

			article.PublishedToHugo = true
			if err := s.store.UpdateArticle(article); err != nil {
				fmt.Printf("  ✗ Error updating status: %v\n", err)
				result.Errors++
				continue
			}

			result.Published++
			fmt.Printf("  ✓ Published\n")
		}

		if s.cfg.Hugo.AutoCommit && result.Published > 0 {
			if err := pub.GitCommit(fmt.Sprintf("Add %d new articles", result.Published)); err != nil {
				fmt.Printf("Warning: git commit failed: %v\n", err)
			}
		}
	}

	fmt.Printf("\nPublished %d of %d articles (errors: %d)\n", result.Published, result.Total, result.Errors)
	return result, nil
}

// Run executes the full pipeline: fetch -> translate -> publish
func (s *Service) Run() (*PipelineResult, error) {
	result := &PipelineResult{}

	fmt.Println("=== Step 1: Fetching new articles ===")
	fetchResult, err := s.Fetch()
	if err != nil {
		fmt.Printf("Fetch error: %v\n", err)
	}
	result.Fetch = fetchResult

	fmt.Println("\n=== Step 2: Translating articles ===")
	translateResult, err := s.Translate(s.cfg.Schedule.TranslateBatch)
	if err != nil {
		fmt.Printf("Translate error: %v\n", err)
	}
	result.Translate = translateResult

	fmt.Println("\n=== Step 3: Publishing to Hugo ===")
	publishResult, err := s.Publish(100)
	if err != nil {
		fmt.Printf("Publish error: %v\n", err)
	}
	result.Publish = publishResult

	return result, nil
}

// Stats returns database statistics
func (s *Service) Stats() (*StatsResult, error) {
	total, translated, published, err := s.store.GetStats()
	if err != nil {
		return nil, fmt.Errorf("failed to get stats: %w", err)
	}

	return &StatsResult{
		Total:       total,
		Translated:  translated,
		Published:   published,
		Pending:     total - translated,
		Unpublished: translated - published,
	}, nil
}

// Pull pulls/updates blog repository
func (s *Service) Pull() error {
	pub := publisher.NewHugoPublisher(&s.cfg.Hugo)
	return pub.GitPull()
}

// Push pushes changes to blog repository
func (s *Service) Push() error {
	pub := publisher.NewHugoPublisher(&s.cfg.Hugo)
	return pub.GitPush()
}

// Rescrape re-scrapes articles that have empty content
func (s *Service) Rescrape() (*RescrapeResult, error) {
	articles, err := s.store.GetArticlesWithEmptyContent()
	if err != nil {
		return nil, fmt.Errorf("failed to get articles: %w", err)
	}

	result := &RescrapeResult{
		Total: len(articles),
	}

	if len(articles) == 0 {
		return result, nil
	}

	scraper := fetcher.NewArticleScraper()

	for _, article := range articles {
		fmt.Printf("  Re-scraping: %s\n", article.Title)
		if err := scraper.ScrapeArticle(article); err != nil {
			fmt.Printf("  Warning: failed to scrape: %v\n", err)
			result.Errors++
			continue
		}

		if article.Content == "" {
			fmt.Printf("  Still empty after re-scrape: %s\n", article.Title)
			result.Errors++
			continue
		}

		if err := s.store.UpdateArticle(article); err != nil {
			fmt.Printf("  Error saving article: %v\n", err)
			result.Errors++
			continue
		}

		result.Rescraped++
		fmt.Printf("  Re-scraped: %s (content: %d chars)\n", article.Title, len(article.Content))

		time.Sleep(1 * time.Second)
	}

	return result, nil
}

// Articles returns recent articles
func (s *Service) Articles(limit int) ([]*interface{}, error) {
	articles, err := s.store.GetRecentArticles(limit)
	if err != nil {
		return nil, err
	}

	// Convert to a simpler format for JSON
	var result []*interface{}
	for _, a := range articles {
		item := interface{}(a)
		result = append(result, &item)
	}
	return result, nil
}

func (s *Service) createTranslator() (translator.Translator, error) {
	switch s.cfg.Translator.Provider {
	case "ollama":
		return translator.NewOllamaTranslator(
			s.cfg.Translator.Ollama.Host,
			s.cfg.Translator.Ollama.Model,
			s.cfg.Translator.Ollama.Prompt,
		), nil
	case "libretranslate":
		return translator.NewLibreTranslateTranslator(s.cfg.Translator.LibreTranslate.Host), nil
	default:
		return nil, fmt.Errorf("unknown translator provider: %s", s.cfg.Translator.Provider)
	}
}
