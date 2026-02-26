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

// OpenRouterTranslator uses OpenRouter's chat API for EN->RU translation.
// Set API key via config or OPENROUTER_API_KEY env var.
type OpenRouterTranslator struct {
	baseURL     string
	model       string
	apiKey      string
	prompt      string
	titlePrompt string
	temperature float64
	client      *http.Client
}

// OpenRouter request/response (OpenAI-compatible chat completions)
type openRouterMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type openRouterRequest struct {
	Model       string             `json:"model"`
	Messages    []openRouterMessage `json:"messages"`
	Temperature float64            `json:"temperature,omitempty"`
}

type openRouterResponse struct {
	Choices []struct {
		Message openRouterMessage `json:"message"`
	} `json:"choices"`
}

func NewOpenRouterTranslator(baseURL, model, apiKey, prompt, titlePrompt string, temperature float64) *OpenRouterTranslator {
	if apiKey == "" {
		apiKey = os.Getenv("OPENROUTER_API_KEY")
	}
	if baseURL == "" {
		baseURL = "https://openrouter.ai/api/v1"
	}
	baseURL = strings.TrimSuffix(baseURL, "/")
	return &OpenRouterTranslator{
		baseURL:     baseURL,
		model:       model,
		apiKey:      apiKey,
		prompt:      prompt,
		titlePrompt: titlePrompt,
		temperature: temperature,
		client: &http.Client{
			Timeout: 3 * time.Minute,
		},
	}
}

func (t *OpenRouterTranslator) Name() string {
	return fmt.Sprintf("OpenRouter (%s)", t.model)
}

func (t *OpenRouterTranslator) IsAvailable() bool {
	return t.apiKey != ""
}

func (t *OpenRouterTranslator) Translate(ctx context.Context, text string) (string, error) {
	return t.chat(ctx, t.prompt, text)
}

func (t *OpenRouterTranslator) TranslateTitle(ctx context.Context, title string) (string, error) {
	systemPrompt := t.titlePrompt
	if systemPrompt == "" {
		systemPrompt = t.prompt
	}
	return t.chat(ctx, systemPrompt, title)
}

func (t *OpenRouterTranslator) chat(ctx context.Context, systemPrompt, userContent string) (string, error) {
	if !t.IsAvailable() {
		return "", fmt.Errorf("OpenRouter API key not configured (set OPENROUTER_API_KEY env var or openrouter.api_key in config)")
	}

	messages := []openRouterMessage{
		{Role: "system", Content: systemPrompt},
		{Role: "user", Content: userContent},
	}

	reqBody := openRouterRequest{
		Model:       t.model,
		Messages:    messages,
		Temperature: t.temperature,
	}

	jsonBody, err := json.Marshal(reqBody)
	if err != nil {
		return "", fmt.Errorf("failed to marshal request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, "POST", t.baseURL+"/chat/completions", bytes.NewBuffer(jsonBody))
	if err != nil {
		return "", fmt.Errorf("failed to create request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+t.apiKey)

	resp, err := t.client.Do(req)
	if err != nil {
		return "", fmt.Errorf("OpenRouter request failed: %w", err)
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("OpenRouter returned status %d: %s", resp.StatusCode, string(body))
	}

	var result openRouterResponse
	if err := json.Unmarshal(body, &result); err != nil {
		return "", fmt.Errorf("failed to decode response: %w", err)
	}

	if len(result.Choices) == 0 {
		return "", fmt.Errorf("OpenRouter returned no choices")
	}

	content := strings.TrimSpace(result.Choices[0].Message.Content)
	if content == "" && strings.TrimSpace(userContent) != "" {
		return "", fmt.Errorf("OpenRouter returned empty translation for non-empty input")
	}
	return content, nil
}

// CheckConnection verifies the API key and endpoint are reachable
func (t *OpenRouterTranslator) CheckConnection(ctx context.Context) error {
	if !t.IsAvailable() {
		return fmt.Errorf("OpenRouter API key not configured")
	}
	req, err := http.NewRequestWithContext(ctx, "GET", t.baseURL+"/models", nil)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+t.apiKey)
	resp, err := t.client.Do(req)
	if err != nil {
		return fmt.Errorf("cannot connect to OpenRouter: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("OpenRouter returned status %d: %s", resp.StatusCode, string(body))
	}
	io.Copy(io.Discard, resp.Body)
	return nil
}
