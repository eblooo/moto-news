package models

import (
	"database/sql"
	"encoding/json"
	"time"
)

type Article struct {
	ID                int64      `json:"id"`
	SourceURL         string     `json:"source_url"`
	SourceSite        string     `json:"source_site"`
	Title             string     `json:"title"`
	TitleRU           string     `json:"title_ru"`
	Description       string     `json:"description"`
	Content           string     `json:"content"`
	ContentRU         string     `json:"content_ru"`
	Author            string     `json:"author"`
	Category          string     `json:"category"`
	Tags              []string   `json:"tags"`
	ImageURL          string     `json:"image_url"`   // featured (first) image
	ImageURLs         []string   `json:"image_urls"` // all images from article (first = featured)
	PublishedAt       time.Time  `json:"published_at"`
	FetchedAt         time.Time  `json:"fetched_at"`
	TranslatedAt      *time.Time `json:"translated_at"`
	PublishedToHugo bool       `json:"published_to_hugo"`
	Slug              string     `json:"slug"`
}

// TagsJSON returns tags as JSON string for database storage
func (a *Article) TagsJSON() string {
	if len(a.Tags) == 0 {
		return "[]"
	}
	b, err := json.Marshal(a.Tags)
	if err != nil {
		// Fallback to empty array on marshal failure
		return "[]"
	}
	return string(b)
}

// ParseTags parses JSON string to tags slice
func (a *Article) ParseTags(jsonStr string) {
	if jsonStr == "" || jsonStr == "[]" {
		a.Tags = []string{}
		return
	}
	if err := json.Unmarshal([]byte(jsonStr), &a.Tags); err != nil {
		a.Tags = []string{}
	}
}

// ImageURLsJSON returns image URLs as JSON array for database storage
func (a *Article) ImageURLsJSON() string {
	if len(a.ImageURLs) == 0 {
		return "[]"
	}
	b, err := json.Marshal(a.ImageURLs)
	if err != nil {
		return "[]"
	}
	return string(b)
}

// ParseImageURLs parses JSON string to image URLs slice
func (a *Article) ParseImageURLs(jsonStr string) {
	if jsonStr == "" || jsonStr == "[]" {
		a.ImageURLs = []string{}
		return
	}
	if err := json.Unmarshal([]byte(jsonStr), &a.ImageURLs); err != nil {
		a.ImageURLs = []string{}
	}
}

// NullTimeToPtr converts sql.NullTime to *time.Time
func NullTimeToPtr(nt sql.NullTime) *time.Time {
	if nt.Valid {
		return &nt.Time
	}
	return nil
}

// PtrToNullTime converts *time.Time to sql.NullTime
func PtrToNullTime(t *time.Time) sql.NullTime {
	if t != nil {
		return sql.NullTime{Time: *t, Valid: true}
	}
	return sql.NullTime{Valid: false}
}

// IsTranslated returns true if article has been translated
func (a *Article) IsTranslated() bool {
	return a.TranslatedAt != nil && a.ContentRU != ""
}

// IsPublished returns true if article has been published to Hugo blog
func (a *Article) IsPublished() bool {
	return a.PublishedToHugo
}

// NeedsTranslation returns true if article needs translation
func (a *Article) NeedsTranslation() bool {
	return a.Content != "" && a.ContentRU == ""
}

// NeedsPublishing returns true if article needs to be published
func (a *Article) NeedsPublishing() bool {
	return a.IsTranslated() && !a.IsPublished()
}
