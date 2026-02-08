"""
Stage 2: User Agent — ReAct agent with site reading capabilities.

Periodically:
1. Reads the blog site
2. Analyzes content quality and finds issues
3. Posts improvement suggestions to GitHub Discussions

Usage:
    python user_agent.py [--config agents.yaml] [--once] [--dry-run]

Requirements:
    - Ollama running with llama3.2:3b
    - GITHUB_TOKEN environment variable set
    - pip install -r requirements.txt
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

import structlog
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from config import load_config
from tools.site_reader import get_site_snapshot, get_page_content, analyze_site_structure
from tools.github_discussions import (
    list_discussions,
    get_discussion_comments,
    create_discussion_comment,
    create_discussion,
)


log = structlog.get_logger()


SYSTEM_PROMPT = """Ты — AI-помощник для мотоциклетного блога blog.alimov.top.
Блог построен на Material for MkDocs и содержит автоматически переведённые с английского статьи о мотоциклах.

Твоя роль — User-агент:
- Ты ЧИТАЕШЬ сайт и анализируешь контент
- Ты ПИШЕШЬ конструктивные предложения в GitHub Discussions (категория "For Developers")
- Ты НЕ МОЖЕШЬ вносить изменения в код или репозиторий напрямую

Правила:
1. Пиши ТОЛЬКО на русском языке
2. Будь конструктивным — предлагай конкретные улучшения
3. Не дублируй уже существующие предложения в Discussions
4. Указывай конкретные страницы/статьи, к которым относится предложение
5. Каждое предложение должно содержать:
   - Описание проблемы или возможности улучшения
   - Конкретное предложение по исправлению
   - Ожидаемый результат
6. Темы для анализа:
   - Качество перевода (ошибки, неточности)
   - UX/навигация сайта
   - SEO (мета-теги, заголовки, описания)
   - Визуальное оформление
   - Структура контента
   - Битые ссылки или изображения
   - Категории и теги
"""


def create_user_agent(config):
    """Create a ReAct agent with site reading and GitHub tools."""
    llm = ChatOllama(
        model=config.ollama.user_model,
        base_url=config.ollama.host,
        temperature=config.ollama.temperature,
        num_ctx=config.ollama.num_ctx,
    )

    tools = [
        get_site_snapshot,
        get_page_content,
        analyze_site_structure,
        list_discussions,
        get_discussion_comments,
        create_discussion_comment,
        create_discussion,
    ]

    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt=SYSTEM_PROMPT,
    )

    return agent


def run_once(config, dry_run: bool = False):
    """Run the user agent once."""
    log.info("user_agent.run_start",
             model=config.ollama.user_model,
             host=config.ollama.host,
             site=config.site.url)

    agent = create_user_agent(config)

    task = f"""Выполни следующие шаги:

1. Используй инструмент get_site_snapshot чтобы получить текущее состояние блога {config.site.url}
2. Используй analyze_site_structure чтобы понять структуру сайта
3. Проверь существующие обсуждения в GitHub Discussions через list_discussions (repo: {config.github.repo})
4. На основе анализа сайта и уже существующих обсуждений:
   - Найди 1-2 проблемы или возможности для улучшения, которые ещё НЕ обсуждались
   - Сформулируй конструктивные предложения

Текущая дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}
"""

    if dry_run:
        task += """

ВАЖНО: Это тестовый запуск (dry-run).
НЕ создавай комментарии или обсуждения в GitHub.
Вместо этого просто опиши, что ты бы предложил."""
    else:
        task += """

5. Если нашёл что-то стоящее — создай новое обсуждение или добавь комментарий.
   - Используй create_discussion для нового предложения
   - Или create_discussion_comment для дополнения существующего
"""

    messages = [HumanMessage(content=task)]

    log.info("user_agent.invoking_agent")

    try:
        result = agent.invoke({"messages": messages})
        final_message = result["messages"][-1].content
        log.info("user_agent.completed", result_length=len(final_message))
        return final_message
    except Exception as e:
        log.error("user_agent.error", error=str(e))
        return f"Error: {e}"


def run_periodic(config, dry_run: bool = False):
    """Run the user agent periodically."""
    interval = config.schedule_interval_minutes * 60

    log.info("user_agent.periodic_start",
             interval_minutes=config.schedule_interval_minutes)

    while True:
        try:
            result = run_once(config, dry_run)
            print("\n" + "=" * 50)
            print(result)
            print("=" * 50 + "\n")
        except Exception as e:
            log.error("user_agent.periodic_error", error=str(e))

        log.info("user_agent.sleeping",
                 next_run_in_minutes=config.schedule_interval_minutes)
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="User Agent for blog.alimov.top")
    parser.add_argument("--config", default=None, help="Path to agents config YAML")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--dry-run", action="store_true", help="Don't post to GitHub")
    args = parser.parse_args()

    config = load_config(args.config)

    if not config.github.token and not args.dry_run:
        print("WARNING: GITHUB_TOKEN not set. Use --dry-run for testing.")
        print("  export GITHUB_TOKEN=ghp_xxxxxxxxxxxx")
        sys.exit(1)

    if args.once or args.dry_run:
        result = run_once(config, args.dry_run)
        print("\n" + "=" * 50)
        print(result)
        print("=" * 50)
    else:
        run_periodic(config, dry_run=False)


if __name__ == "__main__":
    main()
