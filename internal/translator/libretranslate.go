package translator

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

type LibreTranslateTranslator struct {
	host   string
	client *http.Client
}

type libreTranslateRequest struct {
	Q      string `json:"q"`
	Source string `json:"source"`
	Target string `json:"target"`
	Format string `json:"format"`
}

type libreTranslateResponse struct {
	TranslatedText string `json:"translatedText"`
}

func NewLibreTranslateTranslator(host string) *LibreTranslateTranslator {
	return &LibreTranslateTranslator{
		host: strings.TrimSuffix(host, "/"),
		client: &http.Client{
			Timeout: 2 * time.Minute,
		},
	}
}

func (t *LibreTranslateTranslator) Name() string {
	return "LibreTranslate"
}

func (t *LibreTranslateTranslator) Translate(ctx context.Context, text string) (string, error) {
	return t.translate(ctx, text)
}

func (t *LibreTranslateTranslator) TranslateTitle(ctx context.Context, title string) (string, error) {
	return t.translate(ctx, title)
}

func (t *LibreTranslateTranslator) translate(ctx context.Context, text string) (string, error) {
	reqBody := libreTranslateRequest{
		Q:      text,
		Source: "en",
		Target: "ru",
		Format: "text",
	}

	jsonBody, err := json.Marshal(reqBody)
	if err != nil {
		return "", fmt.Errorf("failed to marshal request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, "POST", t.host+"/translate", bytes.NewBuffer(jsonBody))
	if err != nil {
		return "", fmt.Errorf("failed to create request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := t.client.Do(req)
	if err != nil {
		return "", fmt.Errorf("failed to send request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return "", fmt.Errorf("libretranslate returned status %d: %s", resp.StatusCode, string(body))
	}

	var result libreTranslateResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", fmt.Errorf("failed to decode response: %w", err)
	}

	return strings.TrimSpace(result.TranslatedText), nil
}

// CheckConnection verifies LibreTranslate is running
func (t *LibreTranslateTranslator) CheckConnection(ctx context.Context) error {
	req, err := http.NewRequestWithContext(ctx, "GET", t.host+"/languages", nil)
	if err != nil {
		return err
	}

	resp, err := t.client.Do(req)
	if err != nil {
		return fmt.Errorf("cannot connect to LibreTranslate at %s: %w", t.host, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("libretranslate returned status %d", resp.StatusCode)
	}

	return nil
}
