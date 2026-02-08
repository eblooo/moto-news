package config

import (
	"os"
	"path/filepath"

	"github.com/spf13/viper"
)

type Config struct {
	Sources    []SourceConfig   `mapstructure:"sources"`
	Translator TranslatorConfig `mapstructure:"translator"`
	Hugo       HugoConfig       `mapstructure:"hugo"`
	Schedule   ScheduleConfig   `mapstructure:"schedule"`
	Database   DatabaseConfig   `mapstructure:"database"`
	Server     ServerConfig     `mapstructure:"server"`
}

type SourceConfig struct {
	Name    string   `mapstructure:"name"`
	Feeds   []string `mapstructure:"feeds"`
	Enabled bool     `mapstructure:"enabled"`
}

type TranslatorConfig struct {
	Provider       string               `mapstructure:"provider"`
	Ollama         OllamaConfig         `mapstructure:"ollama"`
	DeepL          DeepLConfig          `mapstructure:"deepl"`
	LibreTranslate LibreTranslateConfig `mapstructure:"libretranslate"`
}

type OllamaConfig struct {
	Model       string  `mapstructure:"model"`
	Host        string  `mapstructure:"host"`
	Prompt      string  `mapstructure:"prompt"`
	TitlePrompt string  `mapstructure:"title_prompt"`
	Temperature float64 `mapstructure:"temperature"`
	TopP        float64 `mapstructure:"top_p"`
	NumCtx      int     `mapstructure:"num_ctx"`
}

type DeepLConfig struct {
	APIKey string `mapstructure:"api_key"`
	Free   bool   `mapstructure:"free"`
}

type LibreTranslateConfig struct {
	Host string `mapstructure:"host"`
}

type HugoConfig struct {
	Path       string `mapstructure:"path"`
	ContentDir string `mapstructure:"content_dir"`
	AutoCommit bool   `mapstructure:"auto_commit"`
	GitRemote  string `mapstructure:"git_remote"`
	GitBranch  string `mapstructure:"git_branch"`
	GitRepo    string `mapstructure:"git_repo"`
}

type ScheduleConfig struct {
	FetchInterval  string `mapstructure:"fetch_interval"`
	TranslateBatch int    `mapstructure:"translate_batch"`
}

type DatabaseConfig struct {
	Path string `mapstructure:"path"`
}

type ServerConfig struct {
	Host string `mapstructure:"host"`
	Port int    `mapstructure:"port"`
}

func Load(configPath string) (*Config, error) {
	if configPath != "" {
		viper.SetConfigFile(configPath)
	} else {
		viper.SetConfigName("config")
		viper.SetConfigType("yaml")
		viper.AddConfigPath(".")
		viper.AddConfigPath("$HOME/.moto-news")
	}

	// Set defaults
	viper.SetDefault("translator.provider", "ollama")
	viper.SetDefault("translator.ollama.model", "gemma2:9b")
	viper.SetDefault("translator.ollama.host", "http://localhost:11434")
	viper.SetDefault("translator.ollama.temperature", 0.15)
	viper.SetDefault("translator.ollama.top_p", 0.9)
	viper.SetDefault("translator.ollama.num_ctx", 8192)
	viper.SetDefault("translator.deepl.free", true)
	viper.SetDefault("translator.libretranslate.host", "http://localhost:5000")
	viper.SetDefault("hugo.path", "./blog")
	viper.SetDefault("hugo.content_dir", "content")
	viper.SetDefault("hugo.auto_commit", true)
	viper.SetDefault("hugo.git_remote", "origin")
	viper.SetDefault("hugo.git_branch", "main")
	viper.SetDefault("schedule.fetch_interval", "6h")
	viper.SetDefault("schedule.translate_batch", 10)
	viper.SetDefault("database.path", "./moto-news.db")
	viper.SetDefault("server.host", "0.0.0.0")
	viper.SetDefault("server.port", 8080)

	// Default sources
	viper.SetDefault("sources", []map[string]interface{}{
		{
			"name": "rideapart",
			"feeds": []string{
				"https://www.rideapart.com/rss/news/all/",
				"https://www.rideapart.com/rss/reviews/all/",
				"https://www.rideapart.com/rss/features/all/",
			},
			"enabled": true,
		},
	})

	if err := viper.ReadInConfig(); err != nil {
		if _, ok := err.(viper.ConfigFileNotFoundError); !ok {
			return nil, err
		}
		// Config file not found, use defaults
	}

	var cfg Config
	if err := viper.Unmarshal(&cfg); err != nil {
		return nil, err
	}

	// Resolve relative paths
	if !filepath.IsAbs(cfg.Database.Path) {
		cwd, _ := os.Getwd()
		cfg.Database.Path = filepath.Join(cwd, cfg.Database.Path)
	}

	if !filepath.IsAbs(cfg.Hugo.Path) {
		cwd, _ := os.Getwd()
		cfg.Hugo.Path = filepath.Join(cwd, cfg.Hugo.Path)
	}

	return &cfg, nil
}
