"""
Stage 1: Site Assessment Prototype
Analyzes blog.alimov.top and generates improvement suggestions.

Usage:
    python site_assessor.py [--config agents.yaml] [--url https://blog.alimov.top]

Requirements:
    - Ollama running with qwen2.5-coder:7b or llama3.2:3b
    - pip install -r requirements.txt
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from typing import Optional

from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from config import load_config
from tools.site_reader import fetch_page


def create_assessment_chain(model: str, host: str, temperature: float = 0.35):
    """Create LangChain chain for site assessment."""
    llm = ChatOllama(
        model=model,
        base_url=host,
        temperature=temperature,
        num_ctx=8192,
        num_predict=2048,
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", """Ты эксперт по Hugo, веб-разработке и UX.
Ты анализируешь мотоциклетный блог на базе Hugo (тема PaperMod).

Правила ответа:
- Отвечай ТОЛЬКО на русском языке
- Давай конкретные, реализуемые предложения
- Учитывай, что это автоматически генерируемый контент (переводы статей)
- Фокусируйся на разделе "For Developers" и улучшении опыта читателей
- Предлагай улучшения, которые можно реализовать программно"""),

        ("human", """Проанализируй состояние мотоциклетного блога.

URL: {url}
Дата анализа: {date}

=== Данные со страницы ===
Заголовок: {title}
Мета-описание: {meta_description}
Количество слов: {word_count}

--- Структура (заголовки) ---
{headings}

--- Контент (первые 3000 символов) ---
{content}

--- Внутренние ссылки ({links_count} всего) ---
{links}

=== Задание ===
На основе анализа:

1. Оцени текущее состояние блога (что хорошо, что плохо) — 3-5 пунктов
2. Предложи 5-7 конкретных улучшений для раздела "For Developers":
   - Улучшения UX и навигации
   - Возможности обратной связи от читателей
   - SEO и метаданные
   - Контент и структура статей
   - Техническая оптимизация
3. Укажи приоритеты (высокий / средний / низкий) для каждого улучшения

Формат ответа: структурированный Markdown."""),
    ])

    chain = prompt | llm | StrOutputParser()
    return chain


def run_assessment(url: str, config_path: Optional[str] = None) -> str:
    """Run site assessment and return the analysis."""
    cfg = load_config(config_path)

    print(f"[{datetime.now().isoformat()}] Starting site assessment for {url}")
    print(f"  Model: {cfg.ollama.coder_model}")
    print(f"  Ollama: {cfg.ollama.host}")
    print()

    # Fetch site data
    print("  Fetching site data...")
    try:
        page = fetch_page(url)
    except Exception as e:
        return f"Error fetching site: {e}"

    print(f"  Title: {page.title}")
    print(f"  Word count: {page.word_count}")
    print(f"  Links: {len(page.links)}")
    print(f"  Headings: {len(page.headings)}")
    print()

    # Run LLM analysis
    print("  Running LLM analysis (this may take a while on CPU)...")
    chain = create_assessment_chain(
        model=cfg.ollama.coder_model,
        host=cfg.ollama.host,
        temperature=cfg.ollama.temperature,
    )

    result = chain.invoke({
        "url": url,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "title": page.title,
        "meta_description": page.meta_description,
        "word_count": page.word_count,
        "headings": "\n".join(page.headings[:20]) if page.headings else "Нет заголовков",
        "content": page.content[:3000],
        "links_count": len(page.links),
        "links": "\n".join(page.links[:15]),
    })

    print("  Analysis complete!")
    return result


def main():
    parser = argparse.ArgumentParser(description="Site Assessment Agent")
    parser.add_argument("--config", default=None, help="Path to agents config YAML")
    parser.add_argument("--url", default="https://blog.alimov.top", help="Site URL to analyze")
    parser.add_argument("--output", default=None, help="Save output to file")
    args = parser.parse_args()

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


if __name__ == "__main__":
    main()
