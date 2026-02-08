package main

import (
	"fmt"
	"os"

	"moto-news/internal/config"
	"moto-news/internal/server"
	"moto-news/internal/service"
	"moto-news/internal/storage"

	"github.com/spf13/cobra"
)

var (
	cfgFile string
	cfg     *config.Config
	store   *storage.SQLiteStorage
	svc     *service.Service
)

func main() {
	if err := rootCmd.Execute(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

var rootCmd = &cobra.Command{
	Use:   "aggregator",
	Short: "Moto News Aggregator - парсинг, перевод и публикация мотоновостей",
	Long: `Moto News Aggregator - автоматизированная система для:
- Парсинга новостей с мотоциклетных порталов (RideApart)
- Перевода статей на русский язык через Ollama или LibreTranslate
- Публикации в блог на Hugo (PaperMod)
- Веб-сервер (Gin) для управления через HTTP API`,
	PersistentPreRunE: func(cmd *cobra.Command, args []string) error {
		// Skip init for server command - it does its own setup
		if cmd.Name() == "server" {
			return nil
		}

		var err error
		cfg, err = config.Load(cfgFile)
		if err != nil {
			return fmt.Errorf("failed to load config: %w", err)
		}

		store, err = storage.NewSQLiteStorage(cfg.Database.Path)
		if err != nil {
			return fmt.Errorf("failed to open database: %w", err)
		}

		svc = service.NewService(cfg, store)
		return nil
	},
	PersistentPostRun: func(cmd *cobra.Command, args []string) {
		if store != nil {
			store.Close()
		}
	},
}

var fetchCmd = &cobra.Command{
	Use:   "fetch",
	Short: "Получить новые статьи из RSS фидов",
	RunE: func(cmd *cobra.Command, args []string) error {
		result, err := svc.Fetch()
		if err != nil {
			return err
		}
		fmt.Printf("\nDone! New: %d, Skipped: %d, Errors: %d\n",
			result.NewArticles, result.SkippedArticles, result.Errors)
		return nil
	},
}

var translateCmd = &cobra.Command{
	Use:   "translate",
	Short: "Перевести непереведённые статьи",
	RunE: func(cmd *cobra.Command, args []string) error {
		limit, _ := cmd.Flags().GetInt("limit")
		result, err := svc.Translate(limit)
		if err != nil {
			return err
		}
		fmt.Printf("\nTranslated %d of %d articles (errors: %d)\n",
			result.Translated, result.Total, result.Errors)
		return nil
	},
}

var publishCmd = &cobra.Command{
	Use:   "publish",
	Short: "Опубликовать переведённые статьи в Hugo блог",
	RunE: func(cmd *cobra.Command, args []string) error {
		limit, _ := cmd.Flags().GetInt("limit")
		result, err := svc.Publish(limit)
		if err != nil {
			return err
		}
		fmt.Printf("\nPublished %d of %d articles (errors: %d)\n",
			result.Published, result.Total, result.Errors)
		return nil
	},
}

var runCmd = &cobra.Command{
	Use:   "run",
	Short: "Выполнить полный цикл: fetch -> translate -> publish",
	RunE: func(cmd *cobra.Command, args []string) error {
		fmt.Println("=== Starting full pipeline ===\n")
		result, err := svc.Run()
		if err != nil {
			return err
		}

		fmt.Println("\n=== Pipeline complete ===")
		if result.Fetch != nil {
			fmt.Printf("Fetch:     new=%d, skipped=%d\n", result.Fetch.NewArticles, result.Fetch.SkippedArticles)
		}
		if result.Translate != nil {
			fmt.Printf("Translate: %d of %d\n", result.Translate.Translated, result.Translate.Total)
		}
		if result.Publish != nil {
			fmt.Printf("Publish:   %d of %d\n", result.Publish.Published, result.Publish.Total)
		}
		return nil
	},
}

var statsCmd = &cobra.Command{
	Use:   "stats",
	Short: "Показать статистику базы данных",
	RunE: func(cmd *cobra.Command, args []string) error {
		stats, err := svc.Stats()
		if err != nil {
			return err
		}

		fmt.Println("=== Database Statistics ===")
		fmt.Printf("Total articles:      %d\n", stats.Total)
		fmt.Printf("Translated:          %d\n", stats.Translated)
		fmt.Printf("Published to Hugo:   %d\n", stats.Published)
		fmt.Printf("Pending translation: %d\n", stats.Pending)
		fmt.Printf("Pending publishing:  %d\n", stats.Unpublished)
		return nil
	},
}

var rescrapeCmd = &cobra.Command{
	Use:   "rescrape",
	Short: "Повторно загрузить контент для статей с пустым содержимым",
	RunE: func(cmd *cobra.Command, args []string) error {
		result, err := svc.Rescrape()
		if err != nil {
			return err
		}
		fmt.Printf("\nRe-scraped %d of %d articles (errors: %d)\n",
			result.Rescraped, result.Total, result.Errors)
		return nil
	},
}

var pullCmd = &cobra.Command{
	Use:   "pull",
	Short: "Скачать или обновить блог репозиторий",
	RunE: func(cmd *cobra.Command, args []string) error {
		return svc.Pull()
	},
}

var pushCmd = &cobra.Command{
	Use:   "push",
	Short: "Запушить изменения в репозиторий блога",
	RunE: func(cmd *cobra.Command, args []string) error {
		return svc.Push()
	},
}

var serverCmd = &cobra.Command{
	Use:   "server",
	Short: "Запустить HTTP API сервер (Gin)",
	RunE: func(cmd *cobra.Command, args []string) error {
		var err error
		cfg, err = config.Load(cfgFile)
		if err != nil {
			return fmt.Errorf("failed to load config: %w", err)
		}

		store, err = storage.NewSQLiteStorage(cfg.Database.Path)
		if err != nil {
			return fmt.Errorf("failed to open database: %w", err)
		}
		defer store.Close()

		srv := server.New(cfg, store)
		return srv.Run()
	},
}

func init() {
	rootCmd.PersistentFlags().StringVar(&cfgFile, "config", "", "config file (default: ./config.yaml)")

	translateCmd.Flags().IntP("limit", "l", 10, "maximum number of articles to translate")
	publishCmd.Flags().IntP("limit", "l", 100, "maximum number of articles to publish")

	rootCmd.AddCommand(fetchCmd)
	rootCmd.AddCommand(translateCmd)
	rootCmd.AddCommand(publishCmd)
	rootCmd.AddCommand(runCmd)
	rootCmd.AddCommand(statsCmd)
	rootCmd.AddCommand(rescrapeCmd)
	rootCmd.AddCommand(pullCmd)
	rootCmd.AddCommand(pushCmd)
	rootCmd.AddCommand(serverCmd)
}
