package translator

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

// DeepLTranslator uses the DeepL API for high-quality EN->RU translation.
// Free tier: 500,000 characters/month.
// Set API key via config or DEEPL_API_KEY env var.
type DeepLTranslator struct {
	apiKey string
	host   string
	client *http.Client
}

type deeplRequest struct {
	Text       []string `json:"text"`
	TargetLang string   `json:"target_lang"`
	SourceLang string   `json:"source_lang,omitempty"`
}

type deeplResponse struct {
	Translations []deeplTranslation `json:"translations"`
}

type deeplTranslation struct {
	DetectedSourceLanguage string `json:"detected_source_language"`
	Text                   string `json:"text"`
}

// NewDeepLTranslator creates a DeepL translator.
// apiKey can be empty — will fall back to DEEPL_API_KEY env var.
// free=true uses the free API endpoint (api-free.deepl.com).
func NewDeepLTranslator(apiKey string, free bool) *DeepLTranslator {
	if apiKey == "" {
		apiKey = os.Getenv("DEEPL_API_KEY")
	}

	host := "https://api.deepl.com"
	if free {
		host = "https://api-free.deepl.com"
	}

	return &DeepLTranslator{
		apiKey: apiKey,
		host:   host,
		client: &http.Client{
			Timeout: 30 * time.Second,
		},
	}
}

func (t *DeepLTranslator) Name() string {
	return "DeepL"
}

// IsAvailable returns true if the API key is configured
func (t *DeepLTranslator) IsAvailable() bool {
	return t.apiKey != ""
}

// Translate translates article content EN -> RU
func (t *DeepLTranslator) Translate(ctx context.Context, text string) (string, error) {
	return t.translate(ctx, text)
}

// TranslateTitle translates a title EN -> RU
func (t *DeepLTranslator) TranslateTitle(ctx context.Context, title string) (string, error) {
	return t.translate(ctx, title)
}

func (t *DeepLTranslator) translate(ctx context.Context, text string) (string, error) {
	if !t.IsAvailable() {
		return "", fmt.Errorf("DeepL API key not configured (set DEEPL_API_KEY env var or deepl.api_key in config)")
	}

	reqBody := deeplRequest{
		Text:       []string{text},
		TargetLang: "RU",
		SourceLang: "EN",
	}

	jsonBody, err := json.Marshal(reqBody)
	if err != nil {
		return "", fmt.Errorf("failed to marshal request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, "POST", t.host+"/v2/translate", bytes.NewBuffer(jsonBody))
	if err != nil {
		return "", fmt.Errorf("failed to create request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "DeepL-Auth-Key "+t.apiKey)

	resp, err := t.client.Do(req)
	if err != nil {
		return "", fmt.Errorf("DeepL request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		switch resp.StatusCode {
		case 403:
			return "", fmt.Errorf("DeepL: invalid API key")
		case 456:
			return "", fmt.Errorf("DeepL: quota exceeded (free tier: 500K chars/month)")
		default:
			return "", fmt.Errorf("DeepL returned status %d: %s", resp.StatusCode, string(body))
		}
	}

	var result deeplResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", fmt.Errorf("failed to decode DeepL response: %w", err)
	}

	if len(result.Translations) == 0 {
		return "", fmt.Errorf("DeepL returned empty translations")
	}

	return strings.TrimSpace(result.Translations[0].Text), nil
}

// CheckConnection verifies the DeepL API is reachable and the key is valid
func (t *DeepLTranslator) CheckConnection(ctx context.Context) error {
	if !t.IsAvailable() {
		return fmt.Errorf("DeepL API key not configured")
	}

	req, err := http.NewRequestWithContext(ctx, "GET", t.host+"/v2/usage", nil)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "DeepL-Auth-Key "+t.apiKey)

	resp, err := t.client.Do(req)
	if err != nil {
		return fmt.Errorf("cannot connect to DeepL API: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("DeepL returned status %d", resp.StatusCode)
	}

	return nil
}
