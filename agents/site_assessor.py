"""
Stage 1: Site Assessment Prototype
Analyzes blog.alimov.top and generates improvement suggestions.

Usage:
    python site_assessor.py [--config agents.yaml] [--url https://blog.alimov.top]
    python site_assessor.py --post-discussion --config agents.yaml

Requirements:
    - OPENROUTER_API_KEY environment variable (default provider)
      OR Ollama running locally (set LLM_PROVIDER=ollama)
    - pip install -r requirements.txt
    - GITHUB_TOKEN env var (for source code access and --post-discussion)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from typing import Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from config import load_config, create_llm
from tools.site_reader import build_site_report, fetch_source_context
from tools.github_discussions import post_discussion


def create_assessment_chain(config):
    """Create LangChain chain for site assessment."""
    llm = create_llm(config, role="coder")

    prompt = ChatPromptTemplate.from_messages([
        ("system", """Ты эксперт по Hugo, веб-разработке и UX.
Ты анализируешь мотоциклетный блог на базе Hugo (тема PaperMod).
Тебе предоставлены полные технические данные: HTML-контент, HTTP-заголовки, метаданные,
структурированные данные (OG, JSON-LD), sitemap, robots.txt, Google PageSpeed Insights,
примеры статей, а также исходный код проекта (Hugo конфиг, шаблоны, конфиг агрегатора).

Правила ответа:
- Отвечай ТОЛЬКО на русском языке
- Давай конкретные, реализуемые предложения с ссылками на конкретные данные из отчёта
- Учитывай, что это автоматически генерируемый контент (переводы статей)
- Фокусируйся на улучшении опыта читателей
- Предлагай улучшения, которые можно реализовать программно
- Используй данные из исходного кода для конкретных рекомендаций (какие файлы менять, какие настройки)"""),

        ("human", """Проанализируй состояние мотоциклетного блога.

URL: {url}
Дата анализа: {date}

=== Данные со страницы ===
Заголовок: {title}
Мета-описание: {meta_description}
Количество слов на главной: {word_count}
Всего страниц в sitemap: {sitemap_page_count}

--- Структура (заголовки) ---
{headings}

--- Контент (первые 3000 символов) ---
{content}

--- Внутренние ссылки ({links_count} всего) ---
{links}

--- Примеры статей ---
{articles}

--- Структурированные данные (OG, Twitter, JSON-LD, RSS) ---
{structured_data}

--- HTTP-заголовки ответа ---
{http_headers}

--- robots.txt ---
{robots_txt}

--- Google PageSpeed Insights ---
{pagespeed}

--- Исходный код проекта ---
{source_context}

=== Задание ===
На основе ВСЕХ предоставленных данных:

1. Оцени текущее состояние блога (что хорошо, что плохо) — 3-5 пунктов
2. Предложи 5-7 конкретных улучшений:
   - Улучшения UX и навигации
   - Возможности обратной связи от читателей
   - SEO и метаданные
   - Контент и структура статей
   - Техническая оптимизация (PageSpeed, заголовки безопасности)
3. Укажи приоритеты (высокий / средний / низкий) для каждого улучшения

Формат ответа: структурированный Markdown."""),
    ])

    chain = prompt | llm | StrOutputParser()
    return chain


def run_assessment(url: str, config_path: Optional[str] = None) -> str:
    """Run site assessment and return the analysis."""
    cfg = load_config(config_path)

    print(f"[{datetime.now().isoformat()}] Starting site assessment for {url}")
    print(f"  Provider: {cfg.llm.provider}")
    print(f"  Model: {cfg.llm.coder_model}")
    print()

    # Fetch full site data (with retries for transient DNS failures in K8s)
    print("  Fetching site data (full report)...")
    report = None
    for fetch_attempt in range(1, 4):
        try:
            report = build_site_report(url, max_articles=2, include_pagespeed=True)
            break
        except Exception as e:
            print(f"  Fetch attempt {fetch_attempt}/3 failed: {e}")
            if fetch_attempt < 3:
                time.sleep(10)
            else:
                return f"Error fetching site after 3 attempts: {e}"

    hp = report.homepage
    print(f"  Title: {hp.title}")
    print(f"  Word count: {hp.word_count}")
    print(f"  Links: {len(hp.links)}")
    print(f"  Headings: {len(hp.headings)}")
    print(f"  Articles fetched: {len(report.articles)}")
    print(f"  Sitemap pages: {len(report.sitemap_urls)}")
    print(f"  PageSpeed: {'yes' if report.pagespeed else 'no'}")

    # --- Format article summaries ---
    article_summaries = ""
    for art in report.articles:
        article_summaries += (
            f"\n### {art.title}\n"
            f"URL: {art.url}\n"
            f"Слов: {art.word_count}, Заголовков: {len(art.headings)}\n"
            f"Контент: {art.content[:1000]}...\n"
        )

    # --- Format structured data ---
    sd = report.structured_data
    sd_text = ""
    if sd.og_tags:
        sd_text += "Open Graph теги:\n"
        for k, v in sd.og_tags.items():
            sd_text += f"  {k}: {v}\n"
    else:
        sd_text += "Open Graph теги: НЕ НАЙДЕНЫ\n"
    if sd.twitter_tags:
        sd_text += "Twitter Card теги:\n"
        for k, v in sd.twitter_tags.items():
            sd_text += f"  {k}: {v}\n"
    else:
        sd_text += "Twitter Card теги: НЕ НАЙДЕНЫ\n"
    if sd.json_ld:
        sd_text += f"JSON-LD разметка: {len(sd.json_ld)} блок(ов)\n"
    else:
        sd_text += "JSON-LD разметка: НЕ НАЙДЕНА\n"
    sd_text += f"Canonical: {sd.canonical or 'НЕ ЗАДАН'}\n"
    sd_text += f"RSS фид: {sd.rss_feed or 'НЕ НАЙДЕН'}\n"
    sd_text += f"Язык (html lang): {sd.lang or 'НЕ ЗАДАН'}\n"

    # --- Format HTTP headers ---
    h = report.headers
    headers_text = (
        f"Server: {h.server or 'не указан'}\n"
        f"Cache-Control: {h.cache_control or 'не задан'}\n"
        f"Content-Encoding: {h.content_encoding or 'нет сжатия'}\n"
        f"Strict-Transport-Security: {h.strict_transport_security or 'не задан'}\n"
        f"X-Frame-Options: {h.x_frame_options or 'не задан'}\n"
        f"Content-Security-Policy: {h.content_security_policy or 'не задан'}\n"
    )

    # --- Format PageSpeed ---
    pagespeed_text = ""
    if report.pagespeed:
        ps = report.pagespeed
        if ps.get("scores"):
            pagespeed_text += "Lighthouse оценки (мобильная версия):\n"
            for name, score in ps["scores"].items():
                pagespeed_text += f"  {name}: {score}/100\n"
        if ps.get("metrics"):
            pagespeed_text += "Ключевые метрики:\n"
            for name, value in ps["metrics"].items():
                pagespeed_text += f"  {name}: {value}\n"
        if ps.get("failed_audits"):
            pagespeed_text += "Проблемы (не прошедшие проверки):\n"
            for item in ps["failed_audits"][:10]:
                pagespeed_text += f"  {item}\n"
    else:
        pagespeed_text = "PageSpeed данные недоступны\n"

    # --- Source code context ---
    print("  Fetching source code context...")
    src = fetch_source_context(
        blog_repo="KlimDos/my-blog",
        aggregator_repo="eblooo/moto-news",
    )
    source_text = ""
    if src.hugo_config:
        source_text += f"=== Hugo конфиг сайта ===\n{src.hugo_config[:2000]}\n\n"
    if src.content_tree:
        source_text += f"=== Структура контента (файлы) ===\n{src.content_tree}\n\n"
    if src.sample_articles:
        source_text += "=== Примеры исходников статей (markdown + frontmatter) ===\n"
        for art_src in src.sample_articles:
            source_text += f"{art_src}\n\n"
    if src.category_map:
        if "translateCategory" in src.category_map:
            start = src.category_map.find("func (f *MarkdownFormatter) translateCategory")
            if start >= 0:
                end = src.category_map.find("\n}", start)
                if end >= 0:
                    source_text += (
                        f"=== Маппинг категорий (из исходного кода) ===\n"
                        f"{src.category_map[start:end + 2]}\n\n"
                    )
        else:
            source_text += f"=== Форматтер статей ===\n{src.category_map[:2000]}\n\n"
    if src.aggregator_config:
        source_text += (
            f"=== Конфиг агрегатора (RSS-источники, перевод) ===\n"
            f"{src.aggregator_config[:3000]}\n\n"
        )
    if not source_text:
        source_text = "Исходный код недоступен (нет GITHUB_TOKEN или ошибка API)\n"

    print(f"  Source context: {len(source_text)} chars")
    print()

    # Run LLM analysis with retries
    print("  Running LLM analysis...")
    chain = create_assessment_chain(cfg)

    invoke_args = {
        "url": url,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "title": hp.title,
        "meta_description": hp.meta_description,
        "word_count": hp.word_count,
        "headings": "\n".join(hp.headings[:20]) if hp.headings else "Нет заголовков",
        "content": hp.content[:3000],
        "links_count": len(hp.links),
        "links": "\n".join(hp.links[:15]),
        "sitemap_page_count": len(report.sitemap_urls),
        "articles": article_summaries or "Статьи не загружены",
        "structured_data": sd_text,
        "http_headers": headers_text,
        "robots_txt": report.robots_txt[:500] if report.robots_txt else "Не найден",
        "pagespeed": pagespeed_text,
        "source_context": source_text,
    }

    max_retries = 3
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            result = chain.invoke(invoke_args)
            print("  Analysis complete!")
            return result
        except Exception as e:
            last_error = e
            print(f"  Attempt {attempt}/{max_retries} failed: {e}")
            if attempt < max_retries:
                delay = 15 * attempt
                print(f"  Retrying in {delay}s...")
                time.sleep(delay)

    return f"Error after {max_retries} retries: {last_error}"


def main():
    parser = argparse.ArgumentParser(description="Site Assessment Agent")
    parser.add_argument("--config", default=None, help="Path to agents config YAML")
    parser.add_argument("--url", default="https://blog.alimov.top", help="Site URL to analyze")
    parser.add_argument("--output", default=None, help="Save output to file")
    parser.add_argument("--post-discussion", action="store_true",
                        help="Post the report as a GitHub Discussion")
    args = parser.parse_args()

    cfg = load_config(args.config)

    if args.post_discussion and not cfg.github.token:
        print("Error: GITHUB_TOKEN is required when --post-discussion is used.")
        print("  export GITHUB_TOKEN=ghp_xxxxxxxxxxxx")
        sys.exit(1)

    result = run_assessment(args.url, args.config)

    print("\n" + "=" * 60)
    print("SITE ASSESSMENT REPORT")
    print("=" * 60)
    print(result)

    if args.output:
        with open(args.output, "w") as f:
            f.write(f"# Site Assessment Report\n\n")
            f.write(f"**URL:** {args.url}\n")
            f.write(f"**Date:** {datetime.now().isoformat()}\n\n")
            f.write(result)
        print(f"\nReport saved to {args.output}")

    if args.post_discussion:
        # Don't post error reports as discussions
        if result.startswith("Error"):
            print(f"\nSkipping discussion post: assessment failed ({result[:80]})")
        else:
            today = datetime.now().strftime("%Y-%m-%d")
            title = f"Оценка сайта — {today}"
            body = (
                f"# Оценка сайта\n\n"
                f"**URL:** {args.url}\n"
                f"**Дата:** {today}\n\n"
                f"{result}"
            )

            print(f"\nPosting discussion: {title}")
            url = post_discussion(
                repo=cfg.github.repo,
                title=title,
                body=body,
                category=cfg.github.discussions_category,
            )
            print(f"Discussion posted: {url}")


if __name__ == "__main__":
    main()
