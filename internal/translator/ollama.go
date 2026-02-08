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
	host        string
	model       string
	prompt      string
	titlePrompt string
	temperature float64
	topP        float64
	numCtx      int
	client      *http.Client
}

// --- Chat API types ---

type chatMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type ollamaChatRequest struct {
	Model    string           `json:"model"`
	Messages []chatMessage    `json:"messages"`
	Stream   bool             `json:"stream"`
	Options  *ollamaOptions   `json:"options,omitempty"`
}

type ollamaOptions struct {
	Temperature float64 `json:"temperature,omitempty"`
	TopP        float64 `json:"top_p,omitempty"`
	NumCtx      int     `json:"num_ctx,omitempty"`
}

type ollamaChatResponse struct {
	Message chatMessage `json:"message"`
	Done    bool        `json:"done"`
}

func NewOllamaTranslator(host, model, prompt, titlePrompt string, temperature, topP float64, numCtx int) *OllamaTranslator {
	return &OllamaTranslator{
		host:        strings.TrimSuffix(host, "/"),
		model:       model,
		prompt:      prompt,
		titlePrompt: titlePrompt,
		temperature: temperature,
		topP:        topP,
		numCtx:      numCtx,
		client: &http.Client{
			Timeout: 30 * time.Minute, // Long timeout for large models on CPU
		},
	}
}

func (t *OllamaTranslator) Name() string {
	return fmt.Sprintf("Ollama (%s)", t.model)
}

// Translate translates article content using the main system prompt
func (t *OllamaTranslator) Translate(ctx context.Context, text string) (string, error) {
	return t.chat(ctx, t.prompt, text)
}

// TranslateTitle translates an article title using a dedicated title prompt
func (t *OllamaTranslator) TranslateTitle(ctx context.Context, title string) (string, error) {
	systemPrompt := t.titlePrompt
	if systemPrompt == "" {
		systemPrompt = t.prompt
	}
	return t.chat(ctx, systemPrompt, title)
}

// chat sends a request to Ollama /api/chat with system + user messages
func (t *OllamaTranslator) chat(ctx context.Context, systemPrompt, userContent string) (string, error) {
	messages := []chatMessage{
		{Role: "system", Content: systemPrompt},
		{Role: "user", Content: userContent},
	}

	reqBody := ollamaChatRequest{
		Model:    t.model,
		Messages: messages,
		Stream:   false,
		Options: &ollamaOptions{
			Temperature: t.temperature,
			TopP:        t.topP,
			NumCtx:      t.numCtx,
		},
	}

	jsonBody, err := json.Marshal(reqBody)
	if err != nil {
		return "", fmt.Errorf("failed to marshal request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, "POST", t.host+"/api/chat", bytes.NewBuffer(jsonBody))
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

	var result ollamaChatResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", fmt.Errorf("failed to decode response: %w", err)
	}

	return strings.TrimSpace(result.Message.Content), nil
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
