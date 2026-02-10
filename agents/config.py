"""
Configuration for AI agents.
Loads settings from environment variables and/or config file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class LLMConfig:
    """Provider-agnostic LLM config used by user_agent and site_assessor."""
    provider: str = "openrouter"           # "openrouter" or "ollama"
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    user_model: str = "meta-llama/llama-3.3-70b-instruct:free"
    coder_model: str = "meta-llama/llama-3.3-70b-instruct:free"
    temperature: float = 0.35


@dataclass
class OllamaConfig:
    """Ollama-specific config — still used by admin_agent."""
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
    llm: LLMConfig = field(default_factory=LLMConfig)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    github: GitHubConfig = field(default_factory=GitHubConfig)
    site: SiteConfig = field(default_factory=SiteConfig)
    schedule_interval_minutes: int = 60
    log_level: str = "INFO"


def load_config(config_path: Optional[str] = None) -> AgentConfig:
    """Load configuration from YAML file and environment variables."""
    cfg = AgentConfig()

    # Try loading from file
    if config_path and Path(config_path).exists():
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        if "llm" in data:
            for k, v in data["llm"].items():
                if hasattr(cfg.llm, k):
                    setattr(cfg.llm, k, v)

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

    # Override with environment variables — LLM
    cfg.llm.provider = os.getenv("LLM_PROVIDER", cfg.llm.provider)
    cfg.llm.openrouter_api_key = os.getenv("OPENROUTER_API_KEY", cfg.llm.openrouter_api_key)
    cfg.llm.user_model = os.getenv("OPENROUTER_MODEL", cfg.llm.user_model)
    cfg.llm.coder_model = os.getenv("OPENROUTER_CODER_MODEL", cfg.llm.coder_model)

    # Override with environment variables — Ollama (admin_agent)
    cfg.ollama.host = os.getenv("OLLAMA_HOST", cfg.ollama.host)
    cfg.ollama.user_model = os.getenv("OLLAMA_USER_MODEL", cfg.ollama.user_model)
    cfg.ollama.admin_model = os.getenv("OLLAMA_ADMIN_MODEL", cfg.ollama.admin_model)

    # Override with environment variables — other
    cfg.github.token = os.getenv("GITHUB_TOKEN", cfg.github.token)
    cfg.github.repo = os.getenv("GITHUB_REPO", cfg.github.repo)
    cfg.site.url = os.getenv("SITE_URL", cfg.site.url)
    cfg.site.repo_path = os.getenv("BLOG_REPO_PATH", cfg.site.repo_path)
    cfg.log_level = os.getenv("LOG_LEVEL", cfg.log_level)

    return cfg


def create_llm(config: AgentConfig, role: str = "user"):
    """Create an LLM instance based on config.llm.provider.

    Args:
        config: Loaded agent config.
        role:   "user" picks config.llm.user_model,
                "coder" picks config.llm.coder_model,
                "admin" always uses Ollama (config.ollama.admin_model).

    Returns:
        A LangChain BaseChatModel instance (ChatOpenAI or ChatOllama).
    """
    # admin_agent always uses local Ollama
    if role == "admin":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=config.ollama.admin_model,
            base_url=config.ollama.host,
            temperature=config.ollama.temperature,
            num_ctx=config.ollama.num_ctx,
        )

    if config.llm.provider == "openrouter":
        from langchain_openai import ChatOpenAI

        model = config.llm.user_model if role == "user" else config.llm.coder_model
        return ChatOpenAI(
            model=model,
            api_key=config.llm.openrouter_api_key,
            base_url=config.llm.openrouter_base_url,
            temperature=config.llm.temperature,
        )
    else:
        # Fallback to Ollama for user/coder roles too
        from langchain_ollama import ChatOllama

        model = config.ollama.user_model if role == "user" else config.ollama.coder_model
        return ChatOllama(
            model=model,
            base_url=config.ollama.host,
            temperature=config.ollama.temperature,
            num_ctx=config.ollama.num_ctx,
        )
