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
import os
import sys
import time
from datetime import datetime

import structlog
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage
try:
    from langchain.agents import create_react_agent
except ImportError:
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

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 15


SYSTEM_PROMPT = """Ты — AI-помощник для мотоциклетного блога blog.alimov.top.
Блог построен на Hugo (тема PaperMod) и содержит автоматически переведённые с английского статьи о мотоциклах.

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
    log.info("user_agent.creating_agent",
             model=config.ollama.user_model,
             host=config.ollama.host,
             temperature=config.ollama.temperature,
             num_ctx=config.ollama.num_ctx)

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

    log.info("user_agent.tools_registered",
             tools=[t.name for t in tools])

    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt=SYSTEM_PROMPT,
    )

    log.info("user_agent.agent_ready")
    return agent


def run_once(config, dry_run: bool = False):
    """Run the user agent once with retries."""
    start_time = time.monotonic()

    log.info("user_agent.run_start",
             model=config.ollama.user_model,
             host=config.ollama.host,
             site=config.site.url,
             github_repo=config.github.repo,
             github_token_set=bool(config.github.token),
             dry_run=dry_run)

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

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        attempt_start = time.monotonic()
        try:
            log.info("user_agent.attempt_start",
                     attempt=attempt, max_retries=MAX_RETRIES)

            agent = create_user_agent(config)

            log.info("user_agent.invoking_agent",
                     attempt=attempt, task_length=len(task))

            result = agent.invoke({"messages": messages})

            # Log all messages in the conversation for visibility
            agent_messages = result.get("messages", [])
            log.info("user_agent.agent_messages_count",
                     total_messages=len(agent_messages))

            for i, msg in enumerate(agent_messages):
                msg_type = type(msg).__name__
                content_preview = ""
                if hasattr(msg, "content") and msg.content:
                    content_preview = str(msg.content)[:200]

                # Log tool calls if present
                tool_calls = getattr(msg, "tool_calls", None)
                if tool_calls:
                    for tc in tool_calls:
                        log.info("user_agent.tool_call",
                                 message_idx=i,
                                 tool_name=tc.get("name", "unknown"),
                                 tool_args_keys=list(tc.get("args", {}).keys()))

                log.info("user_agent.message",
                         message_idx=i,
                         message_type=msg_type,
                         content_preview=content_preview)

            final_message = agent_messages[-1].content if agent_messages else ""
            elapsed = round(time.monotonic() - attempt_start, 1)

            log.info("user_agent.completed",
                     attempt=attempt,
                     result_length=len(final_message),
                     elapsed_seconds=elapsed)

            total_elapsed = round(time.monotonic() - start_time, 1)
            log.info("user_agent.run_finished",
                     total_elapsed_seconds=total_elapsed,
                     status="success")

            return final_message

        except Exception as e:
            last_error = e
            elapsed = round(time.monotonic() - attempt_start, 1)
            log.warning("user_agent.attempt_failed",
                        attempt=attempt,
                        max_retries=MAX_RETRIES,
                        error=str(e),
                        error_type=type(e).__name__,
                        elapsed_seconds=elapsed)
            if attempt < MAX_RETRIES:
                delay = RETRY_DELAY_SECONDS * attempt
                log.info("user_agent.retrying",
                         next_attempt=attempt + 1,
                         delay_seconds=delay)
                time.sleep(delay)

    total_elapsed = round(time.monotonic() - start_time, 1)
    log.error("user_agent.all_retries_failed",
              max_retries=MAX_RETRIES,
              error=str(last_error),
              error_type=type(last_error).__name__,
              total_elapsed_seconds=total_elapsed)
    return f"Error after {MAX_RETRIES} retries: {last_error}"


def run_periodic(config, dry_run: bool = False):
    """Run the user agent periodically."""
    interval = config.schedule_interval_minutes * 60

    log.info("user_agent.periodic_start",
             interval_minutes=config.schedule_interval_minutes,
             dry_run=dry_run)

    run_count = 0
    while True:
        run_count += 1
        log.info("user_agent.periodic_run",
                 run_number=run_count)
        try:
            result = run_once(config, dry_run)
            print("\n" + "=" * 50)
            print(result)
            print("=" * 50 + "\n")
        except Exception as e:
            log.error("user_agent.periodic_error",
                      run_number=run_count, error=str(e))

        log.info("user_agent.sleeping",
                 next_run_in_minutes=config.schedule_interval_minutes)
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="User Agent for blog.alimov.top")
    parser.add_argument("--config", default=None, help="Path to agents config YAML")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--dry-run", action="store_true", help="Don't post to GitHub")
    args = parser.parse_args()

    log.info("user_agent.starting",
             config_path=args.config,
             once=args.once,
             dry_run=args.dry_run,
             pid=os.getpid())

    config = load_config(args.config)
    log.info("user_agent.config_loaded",
             site_url=config.site.url,
             github_repo=config.github.repo,
             ollama_host=config.ollama.host,
             ollama_model=config.ollama.user_model,
             github_token_set=bool(config.github.token))

    if not config.github.token and not args.dry_run:
        log.error("user_agent.no_github_token",
                   hint="export GITHUB_TOKEN=ghp_xxxxxxxxxxxx or use --dry-run")
        sys.exit(1)

    if args.once or args.dry_run:
        result = run_once(config, args.dry_run)
        print("\n" + "=" * 50)
        print(result)
        print("=" * 50)
        log.info("user_agent.exit", mode="once")
    else:
        run_periodic(config, dry_run=False)


if __name__ == "__main__":
    main()
