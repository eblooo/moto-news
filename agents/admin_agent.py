"""
Admin Agent — reads GitHub Discussions labelled "Implement" and creates PRs.

Pipeline:
1. Fetch discussions with "Implement" label from the blog repo
2. Filter out already-processed discussions (bot comment present)
3. For each unprocessed discussion:
   a. Fetch relevant source code from target repo
   b. Ask LLM to generate code changes (JSON)
   c. Create branch, commit changes, open PR
   d. Comment on discussion with PR link

Usage:
    python admin_agent.py --config agents.yaml [--once] [--dry-run]

Requirements:
    - OPENROUTER_API_KEY environment variable
    - GITHUB_TOKEN environment variable (needs repo + discussions write)
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
from typing import Optional

import structlog
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from config import load_config, create_llm
from tools.github_discussions import _graphql_query, _get_headers
from tools.github_pr import apply_changes_as_pr, get_file_content

log = structlog.get_logger()

BOT_MARKER = "<!-- admin-agent-pr -->"
MAX_RETRIES = 2
RETRY_DELAY_SECONDS = 20


# ---------------------------------------------------------------------------
# Step 1: Fetch discussions with "Implement" label
# ---------------------------------------------------------------------------

def fetch_implement_discussions(repo: str) -> list[dict]:
    """Fetch discussions that have the 'Implement' label."""
    log.info("admin.fetch_discussions", repo=repo)
    owner, name = repo.split("/")

    query = """
    query($owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) {
        discussions(first: 20, orderBy: {field: CREATED_AT, direction: DESC}) {
          nodes {
            id
            number
            title
            body
            author { login }
            createdAt
            category { name }
            url
            labels(first: 10) { nodes { name } }
            comments(first: 30) {
              nodes {
                body
                author { login }
              }
            }
          }
        }
      }
    }
    """

    data = _graphql_query(query, {"owner": owner, "name": name})
    all_discussions = data["repository"]["discussions"]["nodes"]

    # Filter: must have "Implement" label
    implement = []
    for d in all_discussions:
        labels = [l["name"].lower() for l in d.get("labels", {}).get("nodes", [])]
        if "implement" in labels:
            implement.append(d)

    log.info("admin.fetch_discussions.done",
             total=len(all_discussions), with_implement_label=len(implement))
    return implement


def is_already_processed(discussion: dict) -> bool:
    """Check if the bot already commented with a PR link on this discussion."""
    comments = discussion.get("comments", {}).get("nodes", [])
    for c in comments:
        if BOT_MARKER in (c.get("body") or ""):
            return True
    return False


# ---------------------------------------------------------------------------
# Step 2: Fetch source code context for the target repo
# ---------------------------------------------------------------------------

def fetch_repo_tree(repo: str, path: str = "", ref: str = "main") -> list[str]:
    """Recursively list files in a repo directory. Returns list of paths."""
    import httpx

    headers = {
        "Authorization": f"Bearer {os.getenv('GITHUB_TOKEN', '')}",
        "Accept": "application/vnd.github.v3+json",
    }

    r = httpx.get(
        f"https://api.github.com/repos/{repo}/git/trees/{ref}",
        headers=headers,
        params={"recursive": "1"},
        timeout=30,
    )
    r.raise_for_status()
    tree = r.json().get("tree", [])
    return [item["path"] for item in tree if item["type"] == "blob"]


def fetch_context_for_discussion(
    target_repo: str,
    discussion_title: str,
    discussion_body: str,
) -> str:
    """Build source code context relevant to the discussion suggestion.

    Fetches:
    - Repository file tree (structure)
    - Key config files
    - Files that might be relevant based on the discussion text
    """
    context_parts = []

    # 1. File tree
    try:
        tree = fetch_repo_tree(target_repo)
        # Show truncated tree
        tree_text = "\n".join(tree[:200])
        if len(tree) > 200:
            tree_text += f"\n... and {len(tree) - 200} more files"
        context_parts.append(f"=== Repository file tree ({target_repo}) ===\n{tree_text}")
        log.info("admin.context.tree", repo=target_repo, files=len(tree))
    except Exception as e:
        log.warning("admin.context.tree_error", error=str(e))
        context_parts.append(f"=== File tree unavailable: {e} ===")

    # 2. Key config files (try common ones)
    key_files = [
        "config.yaml", "config.toml", "config.yml",
        "hugo.yaml", "hugo.toml",
        "go.mod",
        "agents/agents.yaml",
        "agents/requirements.txt",
        "Dockerfile",
        "agents/Dockerfile",
    ]

    for fpath in key_files:
        try:
            content, _ = get_file_content(target_repo, fpath)
            context_parts.append(f"=== {fpath} ===\n{content[:3000]}")
            log.info("admin.context.file_ok", path=fpath)
        except Exception:
            pass  # File doesn't exist, skip

    # 3. Try to find relevant files based on discussion keywords
    discussion_text = f"{discussion_title} {discussion_body}".lower()

    # Map common keywords to relevant paths
    keyword_paths = {
        "seo": ["layouts/partials/head.html", "layouts/partials/seo.html",
                 "layouts/_default/baseof.html"],
        "robot": ["static/robots.txt", "layouts/robots.txt"],
        "sitemap": ["layouts/sitemap.xml", "config.yaml"],
        "навигац": ["layouts/partials/header.html", "layouts/partials/nav.html"],
        "rss": ["layouts/_default/rss.xml", "layouts/index.xml"],
        "изображен": ["layouts/partials/post_meta.html",
                       "internal/formatter/markdown.go"],
        "перевод": ["internal/translator/ollama.go", "internal/translator/deepl.go"],
        "категори": ["internal/formatter/markdown.go"],
        "формат": ["internal/formatter/markdown.go"],
        "fetch": ["internal/fetcher/rss.go", "internal/fetcher/scraper.go"],
        "publish": ["internal/publisher/hugo.go", "internal/publisher/github.go"],
    }

    fetched_extra = set()
    for keyword, paths in keyword_paths.items():
        if keyword in discussion_text:
            for fpath in paths:
                if fpath not in fetched_extra:
                    try:
                        content, _ = get_file_content(target_repo, fpath)
                        context_parts.append(
                            f"=== {fpath} (keyword match: '{keyword}') ===\n"
                            f"{content[:4000]}"
                        )
                        fetched_extra.add(fpath)
                        log.info("admin.context.keyword_match",
                                 keyword=keyword, path=fpath)
                    except Exception:
                        pass

    full_context = "\n\n".join(context_parts)
    # Cap total context to ~30K chars to stay within token limits
    if len(full_context) > 30000:
        full_context = full_context[:30000] + "\n\n[... context truncated ...]"

    log.info("admin.context.done", total_chars=len(full_context))
    return full_context


# ---------------------------------------------------------------------------
# Step 3: LLM generates code changes
# ---------------------------------------------------------------------------

PLAN_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You are a senior software engineer implementing improvements for a motorcycle blog ecosystem.
You receive an improvement suggestion from a GitHub Discussion and source code from the target repository.
Your job is to produce the exact file changes needed to implement the suggestion.

CRITICAL RULES:
1. Output ONLY valid JSON — no markdown fences, no explanations outside JSON.
2. For each file, provide the COMPLETE file content (not diffs/patches).
3. Only change files that are strictly necessary.
4. Use descriptive branch names: feature/<short-description> or fix/<short-description>.
5. Write clean, production-ready code with proper error handling.
6. Keep the PR focused — one improvement per PR.
7. If the suggestion cannot be implemented with code changes alone (needs manual steps,
   infrastructure changes, etc.), set "feasible" to false and explain why.

Output JSON schema:
{{
  "feasible": true,
  "reason": "only if feasible=false — explain why",
  "branch_name": "feature/short-description",
  "pr_title": "Short PR title",
  "pr_body": "Markdown description of what was changed and why",
  "files": [
    {{
      "path": "relative/path/to/file",
      "content": "full file content"
    }}
  ]
}}"""),

    ("human", """=== GitHub Discussion (Suggestion to Implement) ===
Title: {discussion_title}
Body:
{discussion_body}

=== Target Repository: {target_repo} ===

{source_context}

=== Instructions ===
Analyze the suggestion and generate the code changes needed to implement it in the repository {target_repo}.
Output ONLY a JSON object following the schema above. No other text."""),
])


def generate_changes(
    config,
    discussion: dict,
    target_repo: str,
    source_context: str,
) -> dict:
    """Ask the LLM to generate code changes for the discussion suggestion."""
    log.info("admin.llm_generate",
             discussion=discussion["number"],
             target_repo=target_repo)

    llm = create_llm(config, role="admin")
    chain = PLAN_PROMPT | llm | StrOutputParser()

    result_text = chain.invoke({
        "discussion_title": discussion["title"],
        "discussion_body": discussion["body"] or "(no body)",
        "target_repo": target_repo,
        "source_context": source_context,
    })

    log.info("admin.llm_generate.done", response_length=len(result_text))
    log.debug("admin.llm_generate.raw", raw=result_text[:1000])

    return _parse_changes_json(result_text)


def _parse_changes_json(text: str) -> dict:
    """Parse the LLM's JSON output with fallback strategies."""

    # Strip markdown code fences
    json_str = text.strip()
    if "```json" in json_str:
        json_str = json_str.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in json_str:
        parts = json_str.split("```")
        if len(parts) >= 2:
            json_str = parts[1]

    # Try direct parse
    try:
        return json.loads(json_str.strip())
    except json.JSONDecodeError:
        log.debug("admin.parse.direct_failed")

    # Sanitize: fix unescaped newlines inside strings
    sanitized = _sanitize_json_string(json_str.strip())
    try:
        return json.loads(sanitized)
    except json.JSONDecodeError as e:
        log.warning("admin.parse.sanitized_failed", error=str(e))

    # Last resort: try to find JSON object in the text
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(_sanitize_json_string(match.group()))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse LLM response as JSON. First 500 chars: {text[:500]}")


def _sanitize_json_string(json_str: str) -> str:
    """Fix unescaped newlines inside JSON string values."""
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
        if in_string and ch == '\t':
            result.append('\\t')
            continue
        result.append(ch)
    return ''.join(result)


# ---------------------------------------------------------------------------
# Step 4: Comment on discussion with PR link
# ---------------------------------------------------------------------------

def comment_on_discussion(repo: str, discussion_number: int, pr_url: str) -> None:
    """Post a comment on the discussion with the PR link."""
    owner, name = repo.split("/")

    # Get discussion node ID
    id_query = """
    query($owner: String!, $name: String!, $number: Int!) {
      repository(owner: $owner, name: $name) {
        discussion(number: $number) { id }
      }
    }
    """
    data = _graphql_query(id_query, {
        "owner": owner, "name": name, "number": discussion_number,
    })
    discussion_id = data["repository"]["discussion"]["id"]

    body = (
        f"{BOT_MARKER}\n"
        f"**PR created:** {pr_url}\n\n"
        f"This PR was automatically generated by the admin-agent "
        f"based on this discussion.\n\n"
        f"_Please review the changes before merging._"
    )

    mutation = """
    mutation($discussionId: ID!, $body: String!) {
      addDiscussionComment(input: {discussionId: $discussionId, body: $body}) {
        comment { id url }
      }
    }
    """
    _graphql_query(mutation, {"discussionId": discussion_id, "body": body})
    log.info("admin.comment_posted", discussion=discussion_number, pr_url=pr_url)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def determine_target_repo(discussion: dict) -> str:
    """Determine which repo to create the PR in based on discussion content.

    Heuristic: if the suggestion is about Hugo/blog/frontend, target the blog repo.
    If it's about aggregator/fetcher/translator/agents code, target moto-news.
    """
    text = f"{discussion['title']} {discussion.get('body', '')}".lower()

    moto_news_keywords = [
        "aggregat", "fetcher", "rss", "scraper", "translator", "перевод",
        "markdown", "formatter", "sqlite", "publisher", "agent", "pipeline",
        "go ", "golang", "internal/",
    ]
    for kw in moto_news_keywords:
        if kw in text:
            return "eblooo/moto-news"

    # Default: blog repo (most suggestions target the Hugo site)
    return "KlimDos/my-blog"


def process_discussion(
    config,
    discussion: dict,
    dry_run: bool = False,
) -> Optional[str]:
    """Process a single discussion: generate changes and create PR.

    Returns PR URL on success, None on failure or skip.
    """
    number = discussion["number"]
    title = discussion["title"]
    log.info("admin.process", discussion=number, title=title[:80])

    if is_already_processed(discussion):
        log.info("admin.process.skip_already_processed", discussion=number)
        return None

    # Determine target repo
    target_repo = determine_target_repo(discussion)
    log.info("admin.process.target_repo", discussion=number, repo=target_repo)

    # Fetch source context
    source_context = fetch_context_for_discussion(
        target_repo=target_repo,
        discussion_title=title,
        discussion_body=discussion.get("body", ""),
    )

    # Generate changes with LLM (with retries)
    changes = None
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            changes = generate_changes(config, discussion, target_repo, source_context)
            break
        except Exception as e:
            last_error = e
            log.warning("admin.llm.attempt_failed",
                        attempt=attempt, error=str(e))
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)

    if not changes:
        log.error("admin.llm.all_retries_failed",
                  discussion=number, error=str(last_error))
        return None

    # Check feasibility
    if not changes.get("feasible", True):
        reason = changes.get("reason", "unknown")
        log.info("admin.process.not_feasible", discussion=number, reason=reason)
        return None

    files = changes.get("files", [])
    branch = changes.get("branch_name", f"feature/discussion-{number}")
    pr_title = changes.get("pr_title", f"Implement: {title[:60]}")
    pr_body = changes.get("pr_body", f"Implements suggestion from discussion #{number}")

    if not files:
        log.warning("admin.process.no_files", discussion=number)
        return None

    log.info("admin.process.changes_ready",
             discussion=number,
             branch=branch,
             files=[f["path"] for f in files])

    if dry_run:
        log.info("admin.process.dry_run_skip", discussion=number)
        for f in files:
            log.info("admin.dry_run.file",
                     path=f["path"],
                     content_length=len(f.get("content", "")))
        return None

    # Create the PR
    try:
        pr = apply_changes_as_pr(
            repo=target_repo,
            branch_name=branch,
            pr_title=pr_title,
            pr_body=(
                f"{pr_body}\n\n---\n"
                f"_Auto-generated from discussion "
                f"[#{number}]({discussion['url']})_"
            ),
            files=files,
        )
        pr_url = pr["html_url"]
        log.info("admin.process.pr_created",
                 discussion=number, pr_url=pr_url)

        # Comment on the discussion with PR link
        discussion_repo = os.getenv("GITHUB_REPO", "KlimDos/my-blog")
        try:
            comment_on_discussion(discussion_repo, number, pr_url)
        except Exception as e:
            log.warning("admin.process.comment_failed",
                        discussion=number, error=str(e))

        return pr_url

    except Exception as e:
        log.error("admin.process.pr_failed",
                  discussion=number, error=str(e))
        return None


def run_pipeline(config, once: bool = False, dry_run: bool = False) -> None:
    """Main admin agent pipeline."""
    log.info("admin.starting",
             provider=config.llm.provider,
             model=config.llm.admin_model,
             repo=config.github.repo,
             dry_run=dry_run)

    while True:
        try:
            discussions = fetch_implement_discussions(config.github.repo)

            if not discussions:
                log.info("admin.no_implement_discussions")
            else:
                for d in discussions:
                    try:
                        pr_url = process_discussion(config, d, dry_run=dry_run)
                        if pr_url:
                            log.info("admin.discussion_done",
                                     discussion=d["number"], pr_url=pr_url)
                    except Exception as e:
                        log.error("admin.discussion_error",
                                  discussion=d["number"], error=str(e))

        except Exception as e:
            log.error("admin.pipeline_error", error=str(e))

        if once:
            break

        delay = config.schedule_interval_minutes * 60
        log.info("admin.sleeping", minutes=config.schedule_interval_minutes)
        time.sleep(delay)


def main():
    parser = argparse.ArgumentParser(description="Admin Agent — implement suggestions as PRs")
    parser.add_argument("--config", default=None, help="Path to agents.yaml config")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate changes but don't create PRs")
    args = parser.parse_args()

    config = load_config(args.config)

    if not config.github.token:
        print("Error: GITHUB_TOKEN is required.")
        sys.exit(1)

    run_pipeline(config, once=args.once, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
