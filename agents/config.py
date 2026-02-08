"""
Configuration for AI agents.
Loads settings from environment variables and/or config file.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class OllamaConfig:
    host: str = "http://localhost:11434"
    user_model: str = "llama3.2:3b"          # Fast model for user-agent
    admin_model: str = "deepseek-r1:8b"      # Strong reasoning for admin-agent
    coder_model: str = "qwen2.5-coder:7b"    # For code-related tasks
    temperature: float = 0.35
    num_ctx: int = 8192


@dataclass
class GitHubConfig:
    token: str = ""
    repo: str = "KlimDos/my-blog"
    discussions_category: str = "For Developers"


@dataclass
class SiteConfig:
    url: str = "https://blog.alimov.top"
    repo_path: str = ""  # Local path to blog repo


@dataclass
class AgentConfig:
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    github: GitHubConfig = field(default_factory=GitHubConfig)
    site: SiteConfig = field(default_factory=SiteConfig)
    schedule_interval_minutes: int = 60
    log_level: str = "INFO"


def load_config(config_path: str | None = None) -> AgentConfig:
    """Load configuration from YAML file and environment variables."""
    cfg = AgentConfig()

    # Try loading from file
    if config_path and Path(config_path).exists():
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        if "ollama" in data:
            for k, v in data["ollama"].items():
                if hasattr(cfg.ollama, k):
                    setattr(cfg.ollama, k, v)

        if "github" in data:
            for k, v in data["github"].items():
                if hasattr(cfg.github, k):
                    setattr(cfg.github, k, v)

        if "site" in data:
            for k, v in data["site"].items():
                if hasattr(cfg.site, k):
                    setattr(cfg.site, k, v)

        if "schedule_interval_minutes" in data:
            cfg.schedule_interval_minutes = data["schedule_interval_minutes"]

    # Override with environment variables
    cfg.ollama.host = os.getenv("OLLAMA_HOST", cfg.ollama.host)
    cfg.ollama.user_model = os.getenv("OLLAMA_USER_MODEL", cfg.ollama.user_model)
    cfg.ollama.admin_model = os.getenv("OLLAMA_ADMIN_MODEL", cfg.ollama.admin_model)
    cfg.github.token = os.getenv("GITHUB_TOKEN", cfg.github.token)
    cfg.github.repo = os.getenv("GITHUB_REPO", cfg.github.repo)
    cfg.site.url = os.getenv("SITE_URL", cfg.site.url)
    cfg.site.repo_path = os.getenv("BLOG_REPO_PATH", cfg.site.repo_path)
    cfg.log_level = os.getenv("LOG_LEVEL", cfg.log_level)

    return cfg
