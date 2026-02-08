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

type OllamaTranslator struct {
	host   string
	model  string
	prompt string
	client *http.Client
}

type ollamaRequest struct {
	Model  string `json:"model"`
	Prompt string `json:"prompt"`
	Stream bool   `json:"stream"`
}

type ollamaResponse struct {
	Response string `json:"response"`
	Done     bool   `json:"done"`
}

func NewOllamaTranslator(host, model, prompt string) *OllamaTranslator {
	return &OllamaTranslator{
		host:   strings.TrimSuffix(host, "/"),
		model:  model,
		prompt: prompt,
		client: &http.Client{
			Timeout: 30 * time.Minute, // Long timeout for large models
		},
	}
}

func (t *OllamaTranslator) Name() string {
	return fmt.Sprintf("Ollama (%s)", t.model)
}

func (t *OllamaTranslator) Translate(ctx context.Context, text string) (string, error) {
	fullPrompt := t.prompt + text

	return t.generate(ctx, fullPrompt)
}

func (t *OllamaTranslator) TranslateTitle(ctx context.Context, title string) (string, error) {
	titlePrompt := t.prompt + title
	return t.generate(ctx, titlePrompt)
}

func (t *OllamaTranslator) generate(ctx context.Context, prompt string) (string, error) {
	reqBody := ollamaRequest{
		Model:  t.model,
		Prompt: prompt,
		Stream: false,
	}

	jsonBody, err := json.Marshal(reqBody)
	if err != nil {
		return "", fmt.Errorf("failed to marshal request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, "POST", t.host+"/api/generate", bytes.NewBuffer(jsonBody))
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
		return "", fmt.Errorf("ollama returned status %d: %s", resp.StatusCode, string(body))
	}

	var result ollamaResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", fmt.Errorf("failed to decode response: %w", err)
	}

	return strings.TrimSpace(result.Response), nil
}

// CheckConnection verifies Ollama is running and the model is available
func (t *OllamaTranslator) CheckConnection(ctx context.Context) error {
	req, err := http.NewRequestWithContext(ctx, "GET", t.host+"/api/tags", nil)
	if err != nil {
		return err
	}

	resp, err := t.client.Do(req)
	if err != nil {
		return fmt.Errorf("cannot connect to Ollama at %s: %w", t.host, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("ollama returned status %d", resp.StatusCode)
	}

	return nil
}
