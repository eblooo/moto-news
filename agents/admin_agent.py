"""
Stage 3: Admin Agent — LangGraph agent with human approval.

Workflow (LangGraph state machine):
1. assess_site   → snapshot of site + recent commits
2. read_discussions → new comments in "Ideas" category
3. analyze        → LLM decides what to implement
4. generate_changes → creates diff / new .md / edits hugo.toml
5. human_approval  → waits for approval (file-based or API)
6. commit          → git add/commit/push (only after approval)

Usage:
    python admin_agent.py [--config agents.yaml] [--auto-approve]

Requirements:
    - Ollama running with deepseek-r1:8b or qwen2.5-coder:7b
    - GITHUB_TOKEN environment variable
    - Local clone of the blog repository
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Dict, List, Literal

import structlog
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from config import load_config
from tools.site_reader import fetch_page
from tools.github_discussions import _graphql_query


log = structlog.get_logger()


# ===== State Definition =====

class AdminState(TypedDict):
    """State for the admin agent workflow."""
    site_snapshot: str
    discussions: List[Dict]
    analysis: str
    proposed_changes: List[Dict]  # [{file: str, action: str, content: str}]
    approval_status: str  # "pending", "approved", "rejected"
    commit_result: str
    errors: List[str]
    messages: Annotated[list, add_messages]


# ===== Node Functions =====

def assess_site(state: AdminState) -> dict:
    """Node 1: Get site snapshot and recent state."""
    log.info("admin.assess_site")

    cfg = state.get("_config")
    url = cfg.site.url if cfg else "https://blog.alimov.top"

    try:
        page = fetch_page(url)
        snapshot = f"""Site: {page.url}
Title: {page.title}
Word Count: {page.word_count}
Headings: {len(page.headings)}
Links: {len(page.links)}

Content preview:
{page.content[:2000]}

Headings:
{chr(10).join(page.headings[:15])}
"""
        log.info("admin.assess_site.done", word_count=page.word_count)
        return {"site_snapshot": snapshot}
    except Exception as e:
        log.error("admin.assess_site.error", error=str(e))
        return {
            "site_snapshot": f"Error: {e}",
            "errors": state.get("errors", []) + [f"assess_site: {e}"],
        }


def read_discussions(state: AdminState) -> dict:
    """Node 2: Read new discussions from GitHub."""
    log.info("admin.read_discussions")

    cfg = state.get("_config")
    repo = cfg.github.repo if cfg else "KlimDos/my-blog"
    owner, name = repo.split("/")

    query = """
    query($owner: String!, $name: String!, $limit: Int!) {
      repository(owner: $owner, name: $name) {
        discussions(first: $limit, orderBy: {field: UPDATED_AT, direction: DESC}) {
          nodes {
            number
            title
            body
            author { login }
            createdAt
            updatedAt
            comments(first: 10) {
              nodes {
                body
                author { login }
                createdAt
              }
            }
            category { name }
          }
        }
      }
    }
    """

    try:
        data = _graphql_query(query, {
            "owner": owner,
            "name": name,
            "limit": 10,
        })
        discussions = data["repository"]["discussions"]["nodes"]
        # Filter to "Ideas"
        discussions = [
            d for d in discussions
            if d["category"]["name"].lower() == "ideas"
        ]
        log.info("admin.read_discussions.done", count=len(discussions))
        return {"discussions": discussions}
    except Exception as e:
        log.error("admin.read_discussions.error", error=str(e))
        return {
            "discussions": [],
            "errors": state.get("errors", []) + [f"read_discussions: {e}"],
        }


def analyze(state: AdminState) -> dict:
    """Node 3: LLM analyzes site + discussions and decides what to implement."""
    log.info("admin.analyze")

    cfg = state.get("_config")
    model = cfg.ollama.admin_model if cfg else "deepseek-r1:8b"
    host = cfg.ollama.host if cfg else "http://localhost:11434"

    llm = ChatOllama(
        model=model,
        base_url=host,
        temperature=0.3,
        num_ctx=8192,
        num_predict=2048,
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", """Ты — Admin-агент для блога blog.alimov.top (Hugo PaperMod).
Ты анализируешь состояние сайта и предложения из GitHub Discussions.
Ты решаешь, какие изменения стоит внести.

Формат ответа — JSON массив изменений:
```json
[
  {{
    "file": "путь/к/файлу.md",
    "action": "create|modify|delete",
    "description": "Что и зачем менять",
    "priority": "high|medium|low",
    "content": "Содержимое файла или изменения (для create/modify)"
  }}
]
```

Если нет необходимых изменений, верни пустой массив: []

ВАЖНО:
- Предлагай только безопасные изменения (документация, конфигурация Hugo, новые страницы)
- НЕ предлагай изменения в код Python/Go приложений
- Каждое изменение должно быть обосновано"""),

        ("human", """=== Состояние сайта ===
{site_snapshot}

=== Обсуждения из GitHub Discussions ===
{discussions}

=== Задание ===
Проанализируй данные и предложи конкретные изменения для улучшения блога.
Верни JSON массив изменений."""),
    ])

    chain = prompt | llm | StrOutputParser()

    discussions_text = ""
    for d in state.get("discussions", []):
        discussions_text += f"\n### #{d.get('number', '?')}: {d.get('title', 'N/A')}\n"
        discussions_text += f"Автор: {d.get('author', {}).get('login', 'unknown')}\n"
        discussions_text += f"Тело: {d.get('body', '')[:300]}\n"
        for c in d.get("comments", {}).get("nodes", []):
            discussions_text += f"  Комментарий от {c.get('author', {}).get('login', '?')}: {c.get('body', '')[:200]}\n"

    try:
        result = chain.invoke({
            "site_snapshot": state.get("site_snapshot", "N/A"),
            "discussions": discussions_text or "Нет обсуждений",
        })

        # Try to parse JSON from response
        # LLM might wrap it in ```json ... ```
        json_str = result
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0]
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0]

        try:
            proposed_changes = json.loads(json_str.strip())
            if not isinstance(proposed_changes, list):
                proposed_changes = []
        except json.JSONDecodeError:
            proposed_changes = []

        log.info("admin.analyze.done", changes_count=len(proposed_changes))
        return {
            "analysis": result,
            "proposed_changes": proposed_changes,
        }

    except Exception as e:
        log.error("admin.analyze.error", error=str(e))
        return {
            "analysis": f"Error: {e}",
            "proposed_changes": [],
            "errors": state.get("errors", []) + [f"analyze: {e}"],
        }


def human_approval(state: AdminState) -> dict:
    """Node 5: Wait for human approval (file-based)."""
    log.info("admin.human_approval")

    changes = state.get("proposed_changes", [])

    if not changes:
        log.info("admin.human_approval.no_changes")
        return {"approval_status": "rejected"}

    # Check for auto-approve mode
    if state.get("_auto_approve"):
        log.info("admin.human_approval.auto_approved")
        return {"approval_status": "approved"}

    # Write approval request to file
    approval_file = Path("/tmp/moto-news-approval.json")
    approval_data = {
        "timestamp": datetime.now().isoformat(),
        "changes": changes,
        "analysis": state.get("analysis", ""),
        "status": "pending",
        "instructions": "Set 'status' to 'approved' or 'rejected' and save the file.",
    }

    approval_file.write_text(json.dumps(approval_data, ensure_ascii=False, indent=2))
    log.info("admin.human_approval.waiting", file=str(approval_file))
    print(f"\n{'='*50}")
    print(f"APPROVAL REQUIRED")
    print(f"Review changes in: {approval_file}")
    print(f"Edit 'status' to 'approved' or 'rejected'")
    print(f"{'='*50}\n")

    # Poll for approval (check every 30 seconds, timeout after 1 hour)
    timeout = 3600
    interval = 30
    elapsed = 0

    while elapsed < timeout:
        if approval_file.exists():
            try:
                data = json.loads(approval_file.read_text())
                status = data.get("status", "pending")
                if status in ("approved", "rejected"):
                    log.info("admin.human_approval.result", status=status)
                    return {"approval_status": status}
            except json.JSONDecodeError:
                pass

        time.sleep(interval)
        elapsed += interval

    log.warning("admin.human_approval.timeout")
    return {"approval_status": "rejected"}


def commit_changes(state: AdminState) -> dict:
    """Node 6: Apply changes and commit to git."""
    log.info("admin.commit_changes")

    if state.get("approval_status") != "approved":
        return {"commit_result": "Skipped: not approved"}

    cfg = state.get("_config")
    blog_path = cfg.site.repo_path if cfg else ""

    if not blog_path or not Path(blog_path).exists():
        return {
            "commit_result": "Error: blog repo path not configured",
            "errors": state.get("errors", []) + ["commit: blog_path not found"],
        }

    changes = state.get("proposed_changes", [])
    applied = 0

    for change in changes:
        file_path = Path(blog_path) / change.get("file", "")
        action = change.get("action", "")
        content = change.get("content", "")

        try:
            if action == "create":
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content, encoding="utf-8")
                applied += 1
                log.info("admin.commit.created", file=str(file_path))

            elif action == "modify" and file_path.exists():
                file_path.write_text(content, encoding="utf-8")
                applied += 1
                log.info("admin.commit.modified", file=str(file_path))

            elif action == "delete" and file_path.exists():
                file_path.unlink()
                applied += 1
                log.info("admin.commit.deleted", file=str(file_path))

        except Exception as e:
            log.error("admin.commit.file_error", file=str(file_path), error=str(e))

    if applied == 0:
        return {"commit_result": "No changes applied"}

    # Git commit
    try:
        subprocess.run(["git", "add", "."], cwd=blog_path, check=True)
        msg = f"AI Admin Agent: apply {applied} changes ({datetime.now().strftime('%Y-%m-%d')})"
        subprocess.run(["git", "commit", "-m", msg], cwd=blog_path, check=True)
        log.info("admin.commit.success", applied=applied)
        return {"commit_result": f"Committed {applied} changes"}
    except subprocess.CalledProcessError as e:
        log.error("admin.commit.git_error", error=str(e))
        return {
            "commit_result": f"Git error: {e}",
            "errors": state.get("errors", []) + [f"git commit: {e}"],
        }


# ===== Router =====

def should_continue(state: AdminState) -> Literal["human_approval", "end"]:
    """Decide whether to proceed to approval or end."""
    changes = state.get("proposed_changes", [])
    if changes:
        return "human_approval"
    return "end"


def should_commit(state: AdminState) -> Literal["commit_changes", "end"]:
    """Decide whether to commit or end."""
    if state.get("approval_status") == "approved":
        return "commit_changes"
    return "end"


# ===== Graph Builder =====

def build_admin_graph():
    """Build the LangGraph state machine for the admin agent."""
    graph = StateGraph(AdminState)

    # Add nodes
    graph.add_node("assess_site", assess_site)
    graph.add_node("read_discussions", read_discussions)
    graph.add_node("analyze", analyze)
    graph.add_node("human_approval", human_approval)
    graph.add_node("commit_changes", commit_changes)

    # Add edges
    graph.set_entry_point("assess_site")
    graph.add_edge("assess_site", "read_discussions")
    graph.add_edge("read_discussions", "analyze")
    graph.add_conditional_edges("analyze", should_continue, {
        "human_approval": "human_approval",
        "end": END,
    })
    graph.add_conditional_edges("human_approval", should_commit, {
        "commit_changes": "commit_changes",
        "end": END,
    })
    graph.add_edge("commit_changes", END)

    return graph.compile()


# ===== Main =====

def main():
    parser = argparse.ArgumentParser(description="Admin Agent for blog.alimov.top")
    parser.add_argument("--config", default=None, help="Path to agents config YAML")
    parser.add_argument("--auto-approve", action="store_true",
                        help="Skip human approval (DANGEROUS)")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    args = parser.parse_args()

    config = load_config(args.config)

    if not config.github.token:
        print("WARNING: GITHUB_TOKEN not set. GitHub features will be limited.")

    if not config.site.repo_path:
        print("WARNING: BLOG_REPO_PATH not set. Cannot commit changes.")

    if args.auto_approve:
        print("WARNING: Auto-approve enabled. Changes will be applied without review!")

    graph = build_admin_graph()

    initial_state: AdminState = {
        "site_snapshot": "",
        "discussions": [],
        "analysis": "",
        "proposed_changes": [],
        "approval_status": "pending",
        "commit_result": "",
        "errors": [],
        "messages": [],
        "_config": config,
        "_auto_approve": args.auto_approve,
    }

    if args.once:
        result = graph.invoke(initial_state)
        print("\n" + "=" * 50)
        print("ADMIN AGENT RESULT")
        print("=" * 50)
        print(f"Analysis:\n{result.get('analysis', 'N/A')[:1000]}")
        print(f"\nProposed changes: {len(result.get('proposed_changes', []))}")
        print(f"Approval: {result.get('approval_status', 'N/A')}")
        print(f"Commit: {result.get('commit_result', 'N/A')}")
        if result.get("errors"):
            print(f"\nErrors: {result['errors']}")
    else:
        # Periodic mode
        interval = config.schedule_interval_minutes * 60
        while True:
            try:
                result = graph.invoke(initial_state)
                log.info("admin.cycle_complete",
                         changes=len(result.get("proposed_changes", [])),
                         approval=result.get("approval_status"),
                         commit=result.get("commit_result"))
            except Exception as e:
                log.error("admin.cycle_error", error=str(e))

            log.info("admin.sleeping", minutes=config.schedule_interval_minutes)
            time.sleep(interval)


if __name__ == "__main__":
    main()
