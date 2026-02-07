package storage

import (
	"database/sql"
	"fmt"
	"time"

	_ "github.com/mattn/go-sqlite3"
	"moto-news/internal/models"
)

type SQLiteStorage struct {
	db *sql.DB
}

func NewSQLiteStorage(dbPath string) (*SQLiteStorage, error) {
	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		return nil, fmt.Errorf("failed to open database: %w", err)
	}

	storage := &SQLiteStorage{db: db}
	if err := storage.migrate(); err != nil {
		return nil, fmt.Errorf("failed to migrate database: %w", err)
	}

	return storage, nil
}

func (s *SQLiteStorage) migrate() error {
	query := `
	CREATE TABLE IF NOT EXISTS articles (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		source_url TEXT UNIQUE NOT NULL,
		source_site TEXT NOT NULL,
		title TEXT NOT NULL,
		title_ru TEXT DEFAULT '',
		description TEXT DEFAULT '',
		content TEXT DEFAULT '',
		content_ru TEXT DEFAULT '',
		author TEXT DEFAULT '',
		category TEXT DEFAULT '',
		tags TEXT DEFAULT '[]',
		image_url TEXT DEFAULT '',
		published_at DATETIME,
		fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
		translated_at DATETIME,
		published_to_mkdocs BOOLEAN DEFAULT FALSE,
		slug TEXT DEFAULT ''
	);

	CREATE INDEX IF NOT EXISTS idx_articles_source_url ON articles(source_url);
	CREATE INDEX IF NOT EXISTS idx_articles_translated ON articles(translated_at);
	CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_to_mkdocs);
	CREATE INDEX IF NOT EXISTS idx_articles_fetched ON articles(fetched_at);
	`
	_, err := s.db.Exec(query)
	return err
}

func (s *SQLiteStorage) Close() error {
	return s.db.Close()
}

// ArticleExists checks if an article with the given URL already exists
func (s *SQLiteStorage) ArticleExists(sourceURL string) (bool, error) {
	var count int
	err := s.db.QueryRow("SELECT COUNT(*) FROM articles WHERE source_url = ?", sourceURL).Scan(&count)
	if err != nil {
		return false, err
	}
	return count > 0, nil
}

// InsertArticle inserts a new article, returns error if URL already exists
func (s *SQLiteStorage) InsertArticle(article *models.Article) error {
	query := `
	INSERT INTO articles (
		source_url, source_site, title, title_ru, description, content, content_ru,
		author, category, tags, image_url, published_at, fetched_at, translated_at,
		published_to_mkdocs, slug
	) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`
	result, err := s.db.Exec(query,
		article.SourceURL,
		article.SourceSite,
		article.Title,
		article.TitleRU,
		article.Description,
		article.Content,
		article.ContentRU,
		article.Author,
		article.Category,
		article.TagsJSON(),
		article.ImageURL,
		article.PublishedAt,
		article.FetchedAt,
		models.PtrToNullTime(article.TranslatedAt),
		article.PublishedToMkDocs,
		article.Slug,
	)
	if err != nil {
		return err
	}
	id, err := result.LastInsertId()
	if err != nil {
		return err
	}
	article.ID = id
	return nil
}

// UpdateArticle updates an existing article
func (s *SQLiteStorage) UpdateArticle(article *models.Article) error {
	query := `
	UPDATE articles SET
		title_ru = ?,
		content_ru = ?,
		translated_at = ?,
		published_to_mkdocs = ?,
		slug = ?,
		content = ?
	WHERE id = ?
	`
	_, err := s.db.Exec(query,
		article.TitleRU,
		article.ContentRU,
		models.PtrToNullTime(article.TranslatedAt),
		article.PublishedToMkDocs,
		article.Slug,
		article.Content,
		article.ID,
	)
	return err
}

// GetArticleByURL retrieves an article by its source URL
func (s *SQLiteStorage) GetArticleByURL(sourceURL string) (*models.Article, error) {
	query := `
	SELECT id, source_url, source_site, title, title_ru, description, content, content_ru,
		author, category, tags, image_url, published_at, fetched_at, translated_at,
		published_to_mkdocs, slug
	FROM articles WHERE source_url = ?
	`
	return s.scanArticle(s.db.QueryRow(query, sourceURL))
}

// GetArticleByID retrieves an article by its ID
func (s *SQLiteStorage) GetArticleByID(id int64) (*models.Article, error) {
	query := `
	SELECT id, source_url, source_site, title, title_ru, description, content, content_ru,
		author, category, tags, image_url, published_at, fetched_at, translated_at,
		published_to_mkdocs, slug
	FROM articles WHERE id = ?
	`
	return s.scanArticle(s.db.QueryRow(query, id))
}

// GetUntranslatedArticles returns articles that need translation
func (s *SQLiteStorage) GetUntranslatedArticles(limit int) ([]*models.Article, error) {
	query := `
	SELECT id, source_url, source_site, title, title_ru, description, content, content_ru,
		author, category, tags, image_url, published_at, fetched_at, translated_at,
		published_to_mkdocs, slug
	FROM articles 
	WHERE content != '' AND content_ru = ''
	ORDER BY published_at DESC
	LIMIT ?
	`
	return s.scanArticles(query, limit)
}

// GetUnpublishedArticles returns translated articles that haven't been published
func (s *SQLiteStorage) GetUnpublishedArticles(limit int) ([]*models.Article, error) {
	query := `
	SELECT id, source_url, source_site, title, title_ru, description, content, content_ru,
		author, category, tags, image_url, published_at, fetched_at, translated_at,
		published_to_mkdocs, slug
	FROM articles 
	WHERE content_ru != '' AND published_to_mkdocs = FALSE
	ORDER BY published_at DESC
	LIMIT ?
	`
	return s.scanArticles(query, limit)
}

// GetRecentArticles returns the most recent articles
func (s *SQLiteStorage) GetRecentArticles(limit int) ([]*models.Article, error) {
	query := `
	SELECT id, source_url, source_site, title, title_ru, description, content, content_ru,
		author, category, tags, image_url, published_at, fetched_at, translated_at,
		published_to_mkdocs, slug
	FROM articles 
	ORDER BY fetched_at DESC
	LIMIT ?
	`
	return s.scanArticles(query, limit)
}

// GetStats returns storage statistics
func (s *SQLiteStorage) GetStats() (total, translated, published int, err error) {
	err = s.db.QueryRow("SELECT COUNT(*) FROM articles").Scan(&total)
	if err != nil {
		return
	}
	err = s.db.QueryRow("SELECT COUNT(*) FROM articles WHERE content_ru != ''").Scan(&translated)
	if err != nil {
		return
	}
	err = s.db.QueryRow("SELECT COUNT(*) FROM articles WHERE published_to_mkdocs = TRUE").Scan(&published)
	return
}

func (s *SQLiteStorage) scanArticle(row *sql.Row) (*models.Article, error) {
	var article models.Article
	var tags string
	var translatedAt sql.NullTime
	var publishedAt time.Time

	err := row.Scan(
		&article.ID,
		&article.SourceURL,
		&article.SourceSite,
		&article.Title,
		&article.TitleRU,
		&article.Description,
		&article.Content,
		&article.ContentRU,
		&article.Author,
		&article.Category,
		&tags,
		&article.ImageURL,
		&publishedAt,
		&article.FetchedAt,
		&translatedAt,
		&article.PublishedToMkDocs,
		&article.Slug,
	)
	if err != nil {
		return nil, err
	}

	article.PublishedAt = publishedAt
	article.TranslatedAt = models.NullTimeToPtr(translatedAt)
	article.ParseTags(tags)

	return &article, nil
}

func (s *SQLiteStorage) scanArticles(query string, args ...interface{}) ([]*models.Article, error) {
	rows, err := s.db.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var articles []*models.Article
	for rows.Next() {
		var article models.Article
		var tags string
		var translatedAt sql.NullTime
		var publishedAt time.Time

		err := rows.Scan(
			&article.ID,
			&article.SourceURL,
			&article.SourceSite,
			&article.Title,
			&article.TitleRU,
			&article.Description,
			&article.Content,
			&article.ContentRU,
			&article.Author,
			&article.Category,
			&tags,
			&article.ImageURL,
			&publishedAt,
			&article.FetchedAt,
			&translatedAt,
			&article.PublishedToMkDocs,
			&article.Slug,
		)
		if err != nil {
			return nil, err
		}

		article.PublishedAt = publishedAt
		article.TranslatedAt = models.NullTimeToPtr(translatedAt)
		article.ParseTags(tags)
		articles = append(articles, &article)
	}

	return articles, rows.Err()
}
