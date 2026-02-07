package translator

import "context"

// Translator is the interface for translation services
type Translator interface {
	// Translate translates text from source to target language
	Translate(ctx context.Context, text string) (string, error)

	// TranslateTitle translates a title (may use different prompt)
	TranslateTitle(ctx context.Context, title string) (string, error)

	// Name returns the translator name
	Name() string
}
