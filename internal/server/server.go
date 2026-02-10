package server

import (
	"fmt"
	"net/http"
	"strconv"

	"github.com/gin-gonic/gin"
	"moto-news/internal/config"
	"moto-news/internal/service"
	"moto-news/internal/storage"
)

// Server is the Gin HTTP server
type Server struct {
	cfg     *config.Config
	store   *storage.SQLiteStorage
	svc     *service.Service
	router  *gin.Engine
}

// New creates a new server instance
func New(cfg *config.Config, store *storage.SQLiteStorage) *Server {
	svc := service.NewService(cfg, store)

	gin.SetMode(gin.ReleaseMode)
	router := gin.Default()

	s := &Server{
		cfg:    cfg,
		store:  store,
		svc:    svc,
		router: router,
	}

	s.setupRoutes()
	return s
}

// Run starts the HTTP server
func (s *Server) Run() error {
	addr := fmt.Sprintf("%s:%d", s.cfg.Server.Host, s.cfg.Server.Port)
	fmt.Printf("Starting server on %s\n", addr)
	fmt.Println("Endpoints:")
	fmt.Println("  POST /api/fetch       - Fetch new articles from RSS feeds")
	fmt.Println("  POST /api/translate   - Translate untranslated articles (?limit=10)")
	fmt.Println("  POST /api/publish     - Publish translated articles (?limit=100)")
	fmt.Println("  POST /api/run         - Full pipeline: fetch -> translate -> publish")
	fmt.Println("  POST /api/rescrape    - Re-scrape articles with empty content")
	fmt.Println("  POST /api/pull        - Pull/update blog repository")
	fmt.Println("  POST /api/push        - Push changes to blog repository")
	fmt.Println("  GET  /api/stats       - Database statistics")
	fmt.Println("  GET  /api/articles    - List recent articles (?limit=20)")
	fmt.Println("  GET  /api/article/:id - Get single article by ID")
	return s.router.Run(addr)
}

func (s *Server) setupRoutes() {
	api := s.router.Group("/api")
	{
		// Actions
		api.POST("/fetch", s.handleFetch)
		api.POST("/translate", s.handleTranslate)
		api.POST("/publish", s.handlePublish)
		api.POST("/run", s.handleRun)
		api.POST("/rescrape", s.handleRescrape)
		api.POST("/pull", s.handlePull)
		api.POST("/push", s.handlePush)

		// Queries
		api.GET("/stats", s.handleStats)
		api.GET("/articles", s.handleArticles)
		api.GET("/article/:id", s.handleArticle)
	}

	// Health check
	s.router.GET("/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "ok"})
	})
}

func (s *Server) handleFetch(c *gin.Context) {
	result, err := s.svc.Fetch()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{
			"success": false,
			"error":   err.Error(),
		})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"success": true,
		"message": fmt.Sprintf("Fetched %d new articles, skipped %d", result.NewArticles, result.SkippedArticles),
		"data":    result,
	})
}

func (s *Server) handleTranslate(c *gin.Context) {
	limit := 10
	if l := c.Query("limit"); l != "" {
		if parsed, err := strconv.Atoi(l); err == nil && parsed > 0 && parsed <= 500 {
			limit = parsed
		}
	}

	result, err := s.svc.Translate(limit)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{
			"success": false,
			"error":   err.Error(),
		})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"success": true,
		"message": fmt.Sprintf("Translated %d of %d articles", result.Translated, result.Total),
		"data":    result,
	})
}

func (s *Server) handlePublish(c *gin.Context) {
	limit := 100
	if l := c.Query("limit"); l != "" {
		if parsed, err := strconv.Atoi(l); err == nil && parsed > 0 && parsed <= 500 {
			limit = parsed
		}
	}

	result, err := s.svc.Publish(limit)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{
			"success": false,
			"error":   err.Error(),
		})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"success": true,
		"message": fmt.Sprintf("Published %d of %d articles", result.Published, result.Total),
		"data":    result,
	})
}

func (s *Server) handleRun(c *gin.Context) {
	result, err := s.svc.Run()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{
			"success": false,
			"error":   err.Error(),
		})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"success": true,
		"message": "Pipeline completed",
		"data":    result,
	})
}

func (s *Server) handleRescrape(c *gin.Context) {
	result, err := s.svc.Rescrape()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{
			"success": false,
			"error":   err.Error(),
		})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"success": true,
		"message": fmt.Sprintf("Re-scraped %d of %d articles", result.Rescraped, result.Total),
		"data":    result,
	})
}

func (s *Server) handlePull(c *gin.Context) {
	if err := s.svc.Pull(); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{
			"success": false,
			"error":   err.Error(),
		})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"success": true,
		"message": "Repository pulled successfully",
	})
}

func (s *Server) handlePush(c *gin.Context) {
	if err := s.svc.Push(); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{
			"success": false,
			"error":   err.Error(),
		})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"success": true,
		"message": "Changes pushed successfully",
	})
}

func (s *Server) handleStats(c *gin.Context) {
	stats, err := s.svc.Stats()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{
			"success": false,
			"error":   err.Error(),
		})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"success": true,
		"data":    stats,
	})
}

func (s *Server) handleArticles(c *gin.Context) {
	limit := 20
	if l := c.Query("limit"); l != "" {
		if parsed, err := strconv.Atoi(l); err == nil && parsed > 0 && parsed <= 500 {
			limit = parsed
		}
	}

	articles, err := s.store.GetRecentArticles(limit)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{
			"success": false,
			"error":   err.Error(),
		})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"success": true,
		"data":    articles,
		"count":   len(articles),
	})
}

func (s *Server) handleArticle(c *gin.Context) {
	idStr := c.Param("id")
	id, err := strconv.ParseInt(idStr, 10, 64)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{
			"success": false,
			"error":   "invalid article id",
		})
		return
	}

	article, err := s.store.GetArticleByID(id)
	if err != nil {
		c.JSON(http.StatusNotFound, gin.H{
			"success": false,
			"error":   "article not found",
		})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"success": true,
		"data":    article,
	})
}
