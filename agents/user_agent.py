"""
Stage 2: User Agent — deterministic pipeline with code-driven topic selection.

Pipeline steps:
1. Fetch site snapshot (code)
2. Fetch existing GitHub Discussions (code)
3. Pick first uncovered topic from TOPIC_ROSTER (code — deterministic)
4. LLM analyzes site for that specific topic (LLM — narrow focus)
5. Safety dedup check (code)
6. Post suggestion to GitHub Discussions (code)

The topic is always chosen by code, never by the LLM.  This avoids the
problem of small models ignoring "banned topics" instructions.

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
import re
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
    post_discussion,
)


log = structlog.get_logger()

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 15


# ---------------------------------------------------------------------------
# Topic roster — code picks the topic, LLM only elaborates
# ---------------------------------------------------------------------------

TOPIC_ROSTER: list[dict] = [
    {
        "id": "seo",
        "name": "SEO и метаданные",
        "prompt_focus": (
            "Проанализируй SEO-оптимизацию сайта: мета-теги (title, description), "
            "структуру заголовков (H1-H6), alt-тексты изображений, URL-структуру, "
            "наличие sitemap.xml и robots.txt. Укажи конкретные страницы с проблемами."
        ),
        "keywords": [
            "seo", "мета", "метаданные", "описание", "title", "description",
            "robots", "sitemap", "заголовок", "h1", "поисковая оптимизация",
        ],
    },
    {
        "id": "navigation",
        "name": "UX и навигация",
        "prompt_focus": (
            "Проанализируй навигацию сайта: главное меню, хлебные крошки, "
            "удобство перемещения между статьями, пагинацию, структуру категорий "
            "и тегов. Укажи конкретные проблемы навигации."
        ),
        "keywords": [
            "навигация", "меню", "ux", "категории", "теги", "пагинация",
            "хлебные крошки", "breadcrumb", "удобство",
        ],
    },
    {
        "id": "mobile",
        "name": "Мобильная адаптивность",
        "prompt_focus": (
            "Проанализируй мобильную адаптивность сайта: отображение на маленьких "
            "экранах, размеры шрифтов и кнопок, удобство чтения статей на телефоне, "
            "адаптивность изображений и таблиц."
        ),
        "keywords": [
            "мобильн", "адаптив", "responsive", "экран", "телефон",
            "смартфон", "touch", "viewport",
        ],
    },
    {
        "id": "performance",
        "name": "Скорость загрузки и производительность",
        "prompt_focus": (
            "Проанализируй производительность сайта: размер страниц, количество "
            "запросов, оптимизацию изображений, использование кэширования, "
            "минификацию CSS/JS, lazy loading."
        ),
        "keywords": [
            "скорость", "производительность", "загрузка", "performance",
            "кэш", "cache", "минификация", "оптимизация", "lazy",
        ],
    },
    {
        "id": "content_structure",
        "name": "Структура контента и архив",
        "prompt_focus": (
            "Проанализируй организацию контента: систему категорий и тегов, "
            "архив статей, связи между похожими статьями, оглавление внутри "
            "длинных статей, серии статей."
        ),
        "keywords": [
            "структура", "контент", "архив", "категория", "тег",
            "оглавление", "серия", "связанные статьи", "table of contents",
        ],
    },
    {
        "id": "images",
        "name": "Изображения и медиа",
        "prompt_focus": (
            "Проанализируй работу с изображениями: наличие alt-текстов, "
            "оптимизацию размеров, использование современных форматов (WebP), "
            "lazy loading, подписи к изображениям, галереи."
        ),
        "keywords": [
            "изображен", "картинк", "фото", "alt", "webp", "галерея",
            "медиа", "image", "lazy loading",
        ],
    },
    {
        "id": "social",
        "name": "Социальные сети и шеринг",
        "prompt_focus": (
            "Проанализируй интеграцию с социальными сетями: Open Graph теги, "
            "Twitter Card теги, кнопки шеринга, превью при расшаривании ссылок, "
            "наличие ссылок на соцсети автора."
        ),
        "keywords": [
            "социальн", "шеринг", "share", "open graph", "og:", "twitter",
            "facebook", "telegram", "соцсет",
        ],
    },
    {
        "id": "rss",
        "name": "RSS и подписка",
        "prompt_focus": (
            "Проанализируй возможности подписки на обновления: наличие и "
            "корректность RSS/Atom фида, удобность обнаружения фида, "
            "email-подписку, уведомления о новых статьях."
        ),
        "keywords": [
            "rss", "atom", "фид", "feed", "подписк", "subscription",
            "email", "уведомлен", "newsletter",
        ],
    },
    {
        "id": "accessibility",
        "name": "Доступность (a11y)",
        "prompt_focus": (
            "Проанализируй доступность сайта: контрастность текста, "
            "навигацию с клавиатуры, ARIA-атрибуты, альтернативные тексты, "
            "семантическую разметку, поддержку скринридеров."
        ),
        "keywords": [
            "доступност", "a11y", "accessibility", "aria", "контраст",
            "клавиатур", "скринридер", "screen reader", "семантич",
        ],
    },
    {
        "id": "internal_links",
        "name": "Внутренняя перелинковка",
        "prompt_focus": (
            "Проанализируй внутреннюю перелинковку: связи между статьями, "
            "битые внутренние ссылки, якорные ссылки, блоки «Похожие статьи», "
            "навигацию «предыдущая/следующая статья»."
        ),
        "keywords": [
            "перелинков", "внутренн", "ссылк", "битые", "якорн",
            "похожие статьи", "related", "internal link",
        ],
    },
]


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
            labels(first: 10) { nodes { name } }
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

Твоя задача — проанализировать сайт ТОЛЬКО по одной конкретной теме и предложить 1 улучшение.

Правила:
1. Пиши ТОЛЬКО на русском языке
2. Анализируй ТОЛЬКО указанную тему — ничего другого
3. Будь конкретным — указывай конкретные страницы, проблемы, решения
4. Фокусируйся на реально полезных улучшениях

Формат ответа — строго JSON:
```json
{{
  "title": "Краткий заголовок предложения (до 80 символов)",
  "body": "Полное описание проблемы и предложение по улучшению в формате Markdown"
}}
```"""),

    ("human", """=== Тема для анализа: {topic_name} ===

{topic_focus}

=== Данные сайта ===
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

=== Дата анализа: {date} ===

Проанализируй сайт ТОЛЬКО по теме «{topic_name}» и предложи 1 конкретное улучшение в JSON формате."""),
])


def _has_label(discussion: dict, label_name: str) -> bool:
    """Check if a discussion has a specific label."""
    labels = discussion.get("labels", {})
    if not labels:
        return False
    nodes = labels.get("nodes", [])
    return any(l.get("name", "").lower() == label_name.lower() for l in nodes)


def run_llm_analysis(config, site_data: dict, topic: dict) -> dict:
    """Run LLM analysis on site data for a specific topic.

    The topic is selected by code (deterministic); the LLM only elaborates.
    Returns dict with title and body.
    """
    log.info("pipeline.llm_analysis",
             model=config.ollama.user_model,
             host=config.ollama.host,
             topic_id=topic["id"],
             topic_name=topic["name"])

    llm = ChatOllama(
        model=config.ollama.user_model,
        base_url=config.ollama.host,
        temperature=config.ollama.temperature,
        num_ctx=config.ollama.num_ctx,
    )

    chain = ANALYSIS_PROMPT | llm | StrOutputParser()

    invoke_args = {
        "topic_name": topic["name"],
        "topic_focus": topic["prompt_focus"],
        "url": site_data["url"],
        "title": site_data["title"],
        "meta_description": site_data["meta_description"],
        "word_count": site_data["word_count"],
        "headings": "\n".join(site_data["headings"]) if site_data["headings"] else "Нет заголовков",
        "content": site_data["content"],
        "links_count": len(site_data["links"]),
        "links": "\n".join(site_data["links"]) if site_data["links"] else "Нет ссылок",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    log.info("pipeline.llm_analysis.invoking")
    start = time.monotonic()
    result_text = chain.invoke(invoke_args)
    elapsed = round(time.monotonic() - start, 1)
    log.info("pipeline.llm_analysis.done",
             elapsed_seconds=elapsed,
             response_length=len(result_text))
    log.debug("pipeline.llm_analysis.raw_response",
              raw=result_text[:500])

    # Parse JSON from LLM response
    suggestion = _parse_llm_json(result_text)

    log.info("pipeline.llm_analysis.parsed",
             has_title=bool(suggestion["title"]),
             has_body=bool(suggestion["body"]),
             title_preview=suggestion["title"][:80])

    return suggestion


def _sanitize_json_string(json_str: str) -> str:
    """Fix common LLM JSON issues: unescaped newlines inside string values."""
    # Replace literal newlines that appear inside JSON string values
    # by processing character-by-character
    result = []
    in_string = False
    escape_next = False
    for ch in json_str:
        if escape_next:
            result.append(ch)
            escape_next = False
            continue
        if ch == '\\':
            result.append(ch)
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue
        if in_string and ch == '\n':
            result.append('\\n')
            continue
        result.append(ch)
    return ''.join(result)


def _parse_llm_json(result_text: str) -> dict:
    """Parse JSON from LLM response with multiple fallback strategies."""

    # Step 1: Extract JSON from markdown code blocks
    json_str = result_text
    if "```json" in json_str:
        json_str = json_str.split("```json")[1].split("```")[0]
    elif "```" in json_str:
        parts = json_str.split("```")
        if len(parts) >= 2:
            json_str = parts[1]

    # Step 2: Try direct json.loads
    try:
        suggestion = json.loads(json_str.strip())
        title = suggestion.get("title", "").strip()
        body = suggestion.get("body", "").strip()
        return {"title": title, "body": body}
    except json.JSONDecodeError:
        log.debug("pipeline.parse.direct_json_failed")

    # Step 3: Sanitize (fix unescaped newlines) and retry json.loads
    try:
        sanitized = _sanitize_json_string(json_str.strip())
        suggestion = json.loads(sanitized)
        title = suggestion.get("title", "").strip()
        body = suggestion.get("body", "").strip()
        log.info("pipeline.parse.sanitized_json_ok")
        return {"title": title, "body": body}
    except json.JSONDecodeError:
        log.debug("pipeline.parse.sanitized_json_failed")

    # Step 4: Regex extraction of "title" and "body" fields
    title_match = re.search(
        r'"title"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', result_text,
    )

    if title_match:
        title = title_match.group(1).replace('\\"', '"').replace('\\n', '\n').strip()

        # Try multiple body extraction strategies
        body = ""

        # 4a: Greedy regex — captures between "body": " and last " before }
        body_match = re.search(
            r'"body"\s*:\s*"(.+)"\s*\}', result_text, re.DOTALL,
        )
        if body_match:
            body = body_match.group(1).replace('\\"', '"').replace('\\n', '\n').strip()

        # 4b: If body is empty, the LLM likely wrote "body": "" then text outside quotes
        #     Extract everything after "body": "" up to closing }
        if not body:
            body_fallback = re.search(
                r'"body"\s*:\s*"*\s*(.*?)\s*"*\s*\}', result_text, re.DOTALL,
            )
            if body_fallback:
                body = body_fallback.group(1).strip().strip('"').strip()

        # 4c: Last resort — take all text after the title, strip JSON artifacts
        if not body:
            after_title = result_text.split(title, 1)[-1] if title in result_text else result_text
            body = re.sub(r'[{}"\\]', '', after_title)
            body = re.sub(r'\b(title|body)\b\s*:\s*', '', body)
            body = re.sub(r'```json\s*', '', body)
            body = re.sub(r'```\s*', '', body)
            body = body.strip(' \n,')

        if title:
            log.info("pipeline.parse.regex_ok", title_preview=title[:80],
                     has_body=bool(body), body_preview=body[:80] if body else "")
            return {"title": title, "body": body}

    # Step 5: Final fallback — extract first meaningful line as title
    log.warning("pipeline.parse.all_strategies_failed",
                raw_response=result_text[:300])
    lines = [
        l.strip() for l in result_text.strip().splitlines()
        if l.strip() and not l.strip().startswith(('{', '}', '"', '```'))
    ]
    fallback_title = lines[0][:80] if lines else "Предложение по улучшению блога"
    # Clean body: remove JSON artifacts
    clean_body = re.sub(r'```json\s*', '', result_text)
    clean_body = re.sub(r'```\s*', '', clean_body)
    clean_body = clean_body.strip()

    return {"title": fallback_title, "body": clean_body}


# ---------------------------------------------------------------------------
# Programmatic deduplication
# ---------------------------------------------------------------------------

def _text_to_trigrams(text: str) -> set[str]:
    """Convert text to a set of character trigrams.

    Uses trigrams instead of whole words to handle Russian morphology:
    'улучшение' and 'улучшению' share most trigrams even though
    they are different word forms.
    """
    cleaned = re.sub(r'[^\w\s]', '', text.lower())
    # Build trigrams from each word (avoids cross-word trigrams)
    trigrams = set()
    for word in cleaned.split():
        if len(word) < 3:
            continue
        for i in range(len(word) - 2):
            trigrams.add(word[i:i + 3])
    return trigrams


def _similarity(set_a: set, set_b: set) -> float:
    """Compute Jaccard similarity between two sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def _is_duplicate(
    title: str,
    body: str,
    discussions: list[dict],
    wontfix_threshold: float = 0.20,
    normal_threshold: float = 0.35,
) -> tuple[bool, str]:
    """Check if a suggestion is too similar to existing discussions.

    Uses character trigrams for comparison (handles Russian morphology).
    Stricter threshold for wontfix discussions (even loosely related = blocked).
    Returns (is_duplicate, reason) tuple.
    """
    suggestion_trigrams = _text_to_trigrams(f"{title} {body}")
    if not suggestion_trigrams:
        return False, ""

    for d in discussions:
        disc_trigrams = _text_to_trigrams(f"{d['title']} {d.get('body', '')}")
        sim = _similarity(suggestion_trigrams, disc_trigrams)

        is_wontfix = _has_label(d, "wontfix")
        threshold = wontfix_threshold if is_wontfix else normal_threshold

        if sim >= threshold:
            reason = (
                f"Similar to #{d['number']} '{d['title'][:60]}' "
                f"(similarity={sim:.2f}, "
                f"threshold={threshold}, "
                f"{'wontfix' if is_wontfix else 'existing'})"
            )
            log.warning("pipeline.dedup.duplicate_detected",
                        suggestion_title=title[:60],
                        existing_number=d["number"],
                        existing_title=d["title"][:60],
                        similarity=round(sim, 3),
                        threshold=threshold,
                        is_wontfix=is_wontfix)
            return True, reason

    log.info("pipeline.dedup.unique",
             suggestion_title=title[:60],
             checked=len(discussions))
    return False, ""


# ---------------------------------------------------------------------------
# Topic selection — deterministic, code-driven
# ---------------------------------------------------------------------------

def _topic_is_covered(
    topic: dict,
    discussions: list[dict],
    wontfix_threshold: float = 0.15,
    normal_threshold: float = 0.25,
) -> bool:
    """Check if a topic from the roster is already covered by existing discussions.

    Compares the topic keywords against each discussion title+body using
    trigram Jaccard similarity.  Returns True if any discussion exceeds the
    threshold for that topic.
    """
    topic_text = " ".join(topic["keywords"]) + " " + topic["name"]
    topic_trigrams = _text_to_trigrams(topic_text)
    if not topic_trigrams:
        return False

    for d in discussions:
        disc_trigrams = _text_to_trigrams(f"{d['title']} {d.get('body', '')}")
        sim = _similarity(topic_trigrams, disc_trigrams)

        is_wontfix = _has_label(d, "wontfix")
        threshold = wontfix_threshold if is_wontfix else normal_threshold

        if sim >= threshold:
            log.info("pipeline.topic_covered",
                     topic_id=topic["id"],
                     topic_name=topic["name"],
                     discussion_number=d["number"],
                     discussion_title=d["title"][:60],
                     similarity=round(sim, 3),
                     threshold=threshold,
                     is_wontfix=is_wontfix)
            return True

    return False


def _pick_topic(
    discussions: list[dict],
    skip_ids: set[str] | None = None,
) -> dict | None:
    """Pick the first uncovered topic from TOPIC_ROSTER.

    Args:
        discussions: Existing GitHub discussions to check against.
        skip_ids: Topic IDs to skip (e.g. already tried and failed dedup).

    Returns:
        The first available topic dict, or None if all are covered.
    """
    skip_ids = skip_ids or set()

    for topic in TOPIC_ROSTER:
        if topic["id"] in skip_ids:
            log.debug("pipeline.topic_skip", topic_id=topic["id"], reason="skip_ids")
            continue

        if _topic_is_covered(topic, discussions):
            continue

        log.info("pipeline.topic_selected",
                 topic_id=topic["id"],
                 topic_name=topic["name"])
        return topic

    log.info("pipeline.all_topics_covered",
             roster_size=len(TOPIC_ROSTER),
             discussions_count=len(discussions),
             skipped=len(skip_ids))
    return None


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------


def run_once(config, dry_run: bool = False):
    """Run the full pipeline once with retries.

    Topic selection is deterministic: code picks the topic from TOPIC_ROSTER,
    the LLM only elaborates on it.  If the LLM's output still matches an
    existing discussion (safety dedup), we skip that topic and try the next.
    """
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

            # Step 4: Pick topic + LLM analysis
            # Code picks the topic; if LLM output is still a duplicate,
            # we mark that topic as tried and pick the next one.
            skip_ids: set[str] = set()

            while True:
                topic = _pick_topic(discussions, skip_ids=skip_ids)

                if topic is None:
                    elapsed = round(time.monotonic() - attempt_start, 1)
                    log.info("pipeline.no_available_topic",
                             roster_size=len(TOPIC_ROSTER),
                             skipped=len(skip_ids),
                             elapsed_seconds=elapsed)
                    return (
                        "All topics from the roster are already covered by "
                        "existing discussions. Nothing to suggest."
                    )

                suggestion = run_llm_analysis(config, site_data, topic)

                if not suggestion["title"] and not suggestion["body"]:
                    log.info("pipeline.no_suggestion", topic_id=topic["id"])
                    skip_ids.add(topic["id"])
                    continue

                # Safety dedup check against existing discussions
                is_dup, dup_reason = _is_duplicate(
                    suggestion["title"],
                    suggestion["body"],
                    discussions,
                )

                if not is_dup:
                    break  # Unique suggestion found

                # Duplicate despite focused prompt — skip topic, try next
                skip_ids.add(topic["id"])
                log.info("pipeline.topic_dedup_skip",
                         topic_id=topic["id"],
                         suggestion_title=suggestion["title"][:80],
                         reason=dup_reason,
                         topics_remaining=len(TOPIC_ROSTER) - len(skip_ids))

            elapsed = round(time.monotonic() - attempt_start, 1)

            # Step 5: Post to GitHub
            if dry_run:
                log.info("pipeline.dry_run.skip_post",
                         title=suggestion["title"][:80],
                         topic_id=topic["id"])
                result = (
                    f"[DRY RUN] Would create discussion:\n"
                    f"Topic: {topic['name']}\n"
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
                     topic_id=topic["id"],
                     topic_name=topic["name"],
                     topics_skipped=len(skip_ids),
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
