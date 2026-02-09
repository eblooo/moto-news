"""
Stage 2: User Agent — deterministic pipeline with LLM analysis.

Pipeline steps (code-driven, no ReAct):
1. Fetch site snapshot (code)
2. Analyze site structure (code)
3. Fetch existing GitHub Discussions (code)
4. LLM analyzes data and generates suggestion (LLM)
5. Post suggestion to GitHub Discussions (code)

Usage:
    python user_agent.py [--config agents.yaml] [--once] [--dry-run]

Requirements:
    - Ollama running with llama3.2:3b
    - GITHUB_TOKEN environment variable set
    - pip install -r requirements.txt
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

import structlog
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from config import load_config
from tools.site_reader import fetch_page
from tools.github_discussions import (
    _graphql_query,
    _get_headers,
)


log = structlog.get_logger()

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 15


# ---------------------------------------------------------------------------
# Step 1 & 2: Fetch site data (deterministic, no LLM)
# ---------------------------------------------------------------------------

def fetch_site_data(url: str) -> dict:
    """Fetch site snapshot and structure. Returns dict with site info."""
    log.info("pipeline.fetch_site", url=url)

    page = fetch_page(url)

    log.info("pipeline.fetch_site.done",
             title=page.title[:60],
             word_count=page.word_count,
             links=len(page.links),
             headings=len(page.headings))

    return {
        "url": url,
        "title": page.title,
        "meta_description": page.meta_description,
        "word_count": page.word_count,
        "headings": page.headings[:20],
        "content": page.content[:3000],
        "links": page.links[:20],
    }


# ---------------------------------------------------------------------------
# Step 3: Fetch existing discussions (deterministic, no LLM)
# ---------------------------------------------------------------------------

def fetch_existing_discussions(repo: str, category: str) -> list[dict]:
    """Fetch existing discussions from GitHub to avoid duplicates."""
    log.info("pipeline.fetch_discussions", repo=repo, category=category)

    owner, name = repo.split("/")

    query = """
    query($owner: String!, $name: String!, $limit: Int!) {
      repository(owner: $owner, name: $name) {
        discussions(first: $limit, orderBy: {field: CREATED_AT, direction: DESC}) {
          nodes {
            number
            title
            body
            author { login }
            createdAt
            comments { totalCount }
            category { name }
            url
          }
        }
      }
    }
    """

    try:
        data = _graphql_query(query, {
            "owner": owner,
            "name": name,
            "limit": 20,
        })
        discussions = data["repository"]["discussions"]["nodes"]

        # Filter by category
        if category:
            discussions = [
                d for d in discussions
                if d["category"]["name"].lower() == category.lower()
            ]

        log.info("pipeline.fetch_discussions.done",
                 total=len(discussions), category=category)

        return discussions

    except Exception as e:
        log.warning("pipeline.fetch_discussions.error", error=str(e))
        return []


# ---------------------------------------------------------------------------
# Step 4: LLM analysis (the only LLM call)
# ---------------------------------------------------------------------------

ANALYSIS_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """Ты — AI-аналитик мотоциклетного блога blog.alimov.top.
Блог построен на Hugo (тема PaperMod) и содержит автоматически переведённые с английского статьи о мотоциклах.

Твоя задача — проанализировать данные о сайте и предложить 1-2 конкретных улучшения.

Правила:
1. Пиши ТОЛЬКО на русском языке
2. Будь конкретным — указывай конкретные страницы, проблемы, решения
3. НЕ ДУБЛИРУЙ уже существующие предложения (список ниже)
4. Фокусируйся на реально полезных улучшениях

Темы для анализа:
- Качество перевода (ошибки, неточности, смешение языков)
- UX/навигация сайта
- SEO (мета-теги, заголовки, описания)
- Структура контента (категории, теги)
- Битые ссылки или изображения
- Визуальное оформление

Формат ответа — строго JSON:
```json
{{
  "title": "Краткий заголовок предложения (до 80 символов)",
  "body": "Полное описание проблемы и предложение по улучшению в формате Markdown"
}}
```

Если нет стоящих предложений, верни:
```json
{{
  "title": "",
  "body": ""
}}
```"""),

    ("human", """=== Данные сайта ===
URL: {url}
Заголовок: {title}
Мета-описание: {meta_description}
Количество слов: {word_count}

--- Заголовки на странице ---
{headings}

--- Контент (первые 3000 символов) ---
{content}

--- Внутренние ссылки ({links_count}) ---
{links}

=== Уже существующие обсуждения (НЕ дублировать!) ===
{existing_discussions}

=== Дата анализа: {date} ===

Проанализируй и предложи 1 улучшение в JSON формате."""),
])


def run_llm_analysis(config, site_data: dict, discussions: list[dict]) -> dict:
    """Run LLM analysis on site data. Returns dict with title and body."""
    log.info("pipeline.llm_analysis",
             model=config.ollama.user_model,
             host=config.ollama.host)

    llm = ChatOllama(
        model=config.ollama.user_model,
        base_url=config.ollama.host,
        temperature=config.ollama.temperature,
        num_ctx=config.ollama.num_ctx,
    )

    chain = ANALYSIS_PROMPT | llm | StrOutputParser()

    # Format existing discussions for context
    existing = ""
    if discussions:
        for d in discussions:
            existing += f"- #{d['number']}: {d['title']}\n"
            if d.get("body"):
                existing += f"  {d['body'][:150]}...\n"
    else:
        existing = "Пока нет обсуждений."

    invoke_args = {
        "url": site_data["url"],
        "title": site_data["title"],
        "meta_description": site_data["meta_description"],
        "word_count": site_data["word_count"],
        "headings": "\n".join(site_data["headings"]) if site_data["headings"] else "Нет заголовков",
        "content": site_data["content"],
        "links_count": len(site_data["links"]),
        "links": "\n".join(site_data["links"]) if site_data["links"] else "Нет ссылок",
        "existing_discussions": existing,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    log.info("pipeline.llm_analysis.invoking")
    start = time.monotonic()
    result_text = chain.invoke(invoke_args)
    elapsed = round(time.monotonic() - start, 1)
    log.info("pipeline.llm_analysis.done",
             elapsed_seconds=elapsed,
             response_length=len(result_text))

    # Parse JSON from LLM response
    json_str = result_text
    if "```json" in json_str:
        json_str = json_str.split("```json")[1].split("```")[0]
    elif "```" in json_str:
        parts = json_str.split("```")
        if len(parts) >= 2:
            json_str = parts[1]

    try:
        suggestion = json.loads(json_str.strip())
        title = suggestion.get("title", "").strip()
        body = suggestion.get("body", "").strip()

        log.info("pipeline.llm_analysis.parsed",
                 has_title=bool(title), has_body=bool(body),
                 title_preview=title[:80])

        return {"title": title, "body": body}

    except json.JSONDecodeError as e:
        log.warning("pipeline.llm_analysis.json_parse_failed",
                    error=str(e),
                    raw_response=result_text[:300])
        # Fallback: use raw text as body
        return {
            "title": "Предложение по улучшению блога",
            "body": result_text.strip(),
        }


# ---------------------------------------------------------------------------
# Step 5: Post to GitHub Discussions (deterministic, no LLM)
# ---------------------------------------------------------------------------

def post_discussion(repo: str, title: str, body: str, category: str) -> str:
    """Create a new GitHub Discussion. Returns URL or error message."""
    log.info("pipeline.post_discussion",
             repo=repo, category=category,
             title=title[:80], body_length=len(body))

    if not title or not body:
        log.info("pipeline.post_discussion.skip_empty")
        return "Skipped: empty title or body"

    owner, name = repo.split("/")

    # Get category ID
    cat_query = """
    query($owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) {
        discussionCategories(first: 20) {
          nodes { id name }
        }
      }
    }
    """

    data = _graphql_query(cat_query, {"owner": owner, "name": name})
    categories = data["repository"]["discussionCategories"]["nodes"]

    category_id = None
    for cat in categories:
        if cat["name"].lower() == category.lower():
            category_id = cat["id"]
            break

    if not category_id:
        available = [c["name"] for c in categories]
        log.error("pipeline.post_discussion.category_not_found",
                  category=category, available=available)
        return f"Category '{category}' not found. Available: {available}"

    log.info("pipeline.post_discussion.category_found",
             category=category, category_id=category_id)

    # Get repo ID
    repo_query = """
    query($owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) { id }
    }
    """
    repo_data = _graphql_query(repo_query, {"owner": owner, "name": name})
    repo_id = repo_data["repository"]["id"]

    # Create discussion
    mutation = """
    mutation($repoId: ID!, $categoryId: ID!, $title: String!, $body: String!) {
      createDiscussion(input: {
        repositoryId: $repoId,
        categoryId: $categoryId,
        title: $title,
        body: $body
      }) {
        discussion { id number url }
      }
    }
    """

    log.info("pipeline.post_discussion.creating")
    result = _graphql_query(mutation, {
        "repoId": repo_id,
        "categoryId": category_id,
        "title": title,
        "body": body,
    })

    disc = result["createDiscussion"]["discussion"]
    url = disc["url"]
    log.info("pipeline.post_discussion.success",
             number=disc["number"], url=url)
    return url


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------

def run_once(config, dry_run: bool = False):
    """Run the full pipeline once with retries."""
    start_time = time.monotonic()

    log.info("pipeline.start",
             model=config.ollama.user_model,
             host=config.ollama.host,
             site=config.site.url,
             github_repo=config.github.repo,
             github_token_set=bool(config.github.token),
             discussions_category=config.github.discussions_category,
             dry_run=dry_run)

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        attempt_start = time.monotonic()
        try:
            log.info("pipeline.attempt", attempt=attempt, max_retries=MAX_RETRIES)

            # Step 1-2: Fetch site data
            site_data = fetch_site_data(config.site.url)

            # Step 3: Fetch existing discussions
            discussions = fetch_existing_discussions(
                config.github.repo,
                config.github.discussions_category,
            )

            # Step 4: LLM analysis
            suggestion = run_llm_analysis(config, site_data, discussions)

            elapsed = round(time.monotonic() - attempt_start, 1)

            if not suggestion["title"] and not suggestion["body"]:
                log.info("pipeline.no_suggestion",
                         elapsed_seconds=elapsed)
                return "LLM did not produce any suggestions this run."

            # Step 5: Post to GitHub
            if dry_run:
                log.info("pipeline.dry_run.skip_post",
                         title=suggestion["title"][:80])
                result = (
                    f"[DRY RUN] Would create discussion:\n"
                    f"Title: {suggestion['title']}\n"
                    f"Body:\n{suggestion['body']}"
                )
            else:
                url = post_discussion(
                    config.github.repo,
                    suggestion["title"],
                    suggestion["body"],
                    config.github.discussions_category,
                )
                result = f"Discussion posted: {url}"

            total_elapsed = round(time.monotonic() - start_time, 1)
            log.info("pipeline.finished",
                     status="success",
                     attempt=attempt,
                     elapsed_seconds=elapsed,
                     total_elapsed_seconds=total_elapsed)

            return result

        except Exception as e:
            last_error = e
            elapsed = round(time.monotonic() - attempt_start, 1)
            log.warning("pipeline.attempt_failed",
                        attempt=attempt,
                        max_retries=MAX_RETRIES,
                        error=str(e),
                        error_type=type(e).__name__,
                        elapsed_seconds=elapsed)
            if attempt < MAX_RETRIES:
                delay = RETRY_DELAY_SECONDS * attempt
                log.info("pipeline.retrying",
                         next_attempt=attempt + 1,
                         delay_seconds=delay)
                time.sleep(delay)

    total_elapsed = round(time.monotonic() - start_time, 1)
    log.error("pipeline.all_retries_failed",
              max_retries=MAX_RETRIES,
              error=str(last_error),
              error_type=type(last_error).__name__,
              total_elapsed_seconds=total_elapsed)
    return f"Error after {MAX_RETRIES} retries: {last_error}"


def run_periodic(config, dry_run: bool = False):
    """Run the pipeline periodically."""
    interval = config.schedule_interval_minutes * 60

    log.info("pipeline.periodic_start",
             interval_minutes=config.schedule_interval_minutes,
             dry_run=dry_run)

    run_count = 0
    while True:
        run_count += 1
        log.info("pipeline.periodic_run", run_number=run_count)
        try:
            result = run_once(config, dry_run)
            print("\n" + "=" * 50)
            print(result)
            print("=" * 50 + "\n")
        except Exception as e:
            log.error("pipeline.periodic_error",
                      run_number=run_count, error=str(e))

        log.info("pipeline.sleeping",
                 next_run_in_minutes=config.schedule_interval_minutes)
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="User Agent for blog.alimov.top")
    parser.add_argument("--config", default=None, help="Path to agents config YAML")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--dry-run", action="store_true", help="Don't post to GitHub")
    args = parser.parse_args()

    log.info("pipeline.starting",
             config_path=args.config,
             once=args.once,
             dry_run=args.dry_run,
             pid=os.getpid())

    config = load_config(args.config)
    log.info("pipeline.config_loaded",
             site_url=config.site.url,
             github_repo=config.github.repo,
             discussions_category=config.github.discussions_category,
             ollama_host=config.ollama.host,
             ollama_model=config.ollama.user_model,
             github_token_set=bool(config.github.token))

    if not config.github.token and not args.dry_run:
        log.error("pipeline.no_github_token",
                  hint="export GITHUB_TOKEN=ghp_xxxxxxxxxxxx or use --dry-run")
        sys.exit(1)

    if args.once or args.dry_run:
        result = run_once(config, args.dry_run)
        print("\n" + "=" * 50)
        print(result)
        print("=" * 50)
        log.info("pipeline.exit", mode="once")
    else:
        run_periodic(config, dry_run=False)


if __name__ == "__main__":
    main()
