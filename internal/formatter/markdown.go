package formatter

import (
	"fmt"
	"path/filepath"
	"strings"
	"time"

	"moto-news/internal/models"
)

type MarkdownFormatter struct{}

func NewMarkdownFormatter() *MarkdownFormatter {
	return &MarkdownFormatter{}
}

// Format converts an article to Hugo-compatible markdown
func (f *MarkdownFormatter) Format(article *models.Article) string {
	var sb strings.Builder

	// Title
	title := article.TitleRU
	if title == "" {
		title = article.Title
	}
	// Escape quotes in title for YAML
	escapedTitle := strings.ReplaceAll(title, `"`, `\"`)

	// Frontmatter
	sb.WriteString("---\n")
	sb.WriteString(fmt.Sprintf("title: \"%s\"\n", escapedTitle))
	sb.WriteString(fmt.Sprintf("date: %s\n", article.PublishedAt.Format("2006-01-02T15:04:05")))

	// Categories
	sb.WriteString("categories:\n")
	sb.WriteString("  - Новости\n")
	if article.Category != "" {
		sb.WriteString(fmt.Sprintf("  - %s\n", f.translateCategory(article.Category)))
	}

	// Tags
	if len(article.Tags) > 0 {
		sb.WriteString("tags:\n")
		for _, tag := range article.Tags[:min(5, len(article.Tags))] {
			sb.WriteString(fmt.Sprintf("  - %s\n", tag))
		}
	}

	// Source reference
	sb.WriteString(fmt.Sprintf("source: %s\n", article.SourceURL))
	if article.Author != "" {
		sb.WriteString(fmt.Sprintf("author: %s\n", article.Author))
	}

	// Cover image
	if article.ImageURL != "" {
		sb.WriteString("cover:\n")
		sb.WriteString(fmt.Sprintf("  image: \"%s\"\n", article.ImageURL))
		sb.WriteString(fmt.Sprintf("  alt: \"%s\"\n", escapedTitle))
		sb.WriteString("  hidden: false\n")
	}

	sb.WriteString("---\n\n")

	// Content (no # Title — Hugo renders title from frontmatter)
	content := article.ContentRU
	if content == "" {
		content = article.Content
	}
	sb.WriteString(f.formatContent(content))
	sb.WriteString("\n\n")

	// Footer with source
	sb.WriteString("---\n\n")
	sb.WriteString(fmt.Sprintf("*Источник: [%s](%s)*\n", article.SourceSite, article.SourceURL))

	return sb.String()
}

// formatContent cleans and formats the article content
func (f *MarkdownFormatter) formatContent(content string) string {
	// Split into paragraphs
	paragraphs := strings.Split(content, "\n\n")
	var formatted []string

	for _, p := range paragraphs {
		p = strings.TrimSpace(p)
		if p != "" {
			formatted = append(formatted, p)
		}
	}

	return strings.Join(formatted, "\n\n")
}

// GetFilePath returns the file path for an article
func (f *MarkdownFormatter) GetFilePath(article *models.Article, baseDir string) string {
	year := article.PublishedAt.Format("2006")
	month := article.PublishedAt.Format("01")

	slug := article.Slug
	if slug == "" {
		slug = fmt.Sprintf("article-%d", article.ID)
	}

	// For Hugo: posts/YYYY/MM/slug.md (under content directory)
	return filepath.Join(baseDir, "posts", year, month, slug+".md")
}

// translateCategory translates common categories to Russian
func (f *MarkdownFormatter) translateCategory(category string) string {
	translations := map[string]string{
		"news":                      "Новости",
		"reviews":                   "Обзоры",
		"features":                  "Статьи",
		"sportbikes":                "Спортбайки",
		"cruisers":                  "Круизеры",
		"adventure":                 "Эндуро",
		"touring":                   "Туринг",
		"naked":                     "Нейкеды",
		"electric":                  "Электромотоциклы",
		"racing":                    "Гонки",
		"gear":                      "Экипировка",
		"technology":                "Технологии",
		"industry":                  "Индустрия",
		"custom":                    "Кастом",
		"adventure-and-dual-sport":  "Эндуро",
		"touring-and-sport-touring": "Туринг",
		"standard-and-naked":        "Нейкеды",
		"electric-motorcycles":      "Электромотоциклы",
	}

	lower := strings.ToLower(category)
	if translated, ok := translations[lower]; ok {
		return translated
	}
	return category
}

// GenerateIndex generates an index page for a directory
func (f *MarkdownFormatter) GenerateIndex(articles []*models.Article, title string) string {
	var sb strings.Builder

	sb.WriteString(fmt.Sprintf("# %s\n\n", title))

	// Group by month
	byMonth := make(map[string][]*models.Article)
	for _, a := range articles {
		key := a.PublishedAt.Format("2006-01")
		byMonth[key] = append(byMonth[key], a)
	}

	// Sort months (newest first)
	months := make([]string, 0, len(byMonth))
	for m := range byMonth {
		months = append(months, m)
	}
	// Simple reverse sort
	for i, j := 0, len(months)-1; i < j; i, j = i+1, j-1 {
		months[i], months[j] = months[j], months[i]
	}

	for _, month := range months {
		t, _ := time.Parse("2006-01", month)
		sb.WriteString(fmt.Sprintf("## %s\n\n", t.Format("January 2006")))

		for _, a := range byMonth[month] {
			title := a.TitleRU
			if title == "" {
				title = a.Title
			}
			link := fmt.Sprintf("%s/%s/%s.md", a.PublishedAt.Format("2006"), a.PublishedAt.Format("01"), a.Slug)
			sb.WriteString(fmt.Sprintf("- [%s](%s)\n", title, link))
		}
		sb.WriteString("\n")
	}

	return sb.String()
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
