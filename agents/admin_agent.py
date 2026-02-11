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
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 15


# ---------------------------------------------------------------------------
# Step 1: Fetch discussions with "Implement" label
# ---------------------------------------------------------------------------

def fetch_implement_discussions(repo: str) -> list[dict]:
    """Fetch discussions that have the 'Implement' label.

    Includes retries for transient DNS/network failures in K8s.
    """
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

    last_error = None
    for attempt in range(1, 4):
        try:
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
        except Exception as e:
            last_error = e
            log.warning("admin.fetch_discussions.attempt_failed",
                        attempt=attempt, error=str(e))
            if attempt < 3:
                time.sleep(10)

    log.error("admin.fetch_discussions.all_failed", error=str(last_error))
    raise RuntimeError(f"Failed to fetch discussions after 3 attempts: {last_error}")


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

def fetch_repo_tree(repo: str, path: str = "", ref: str = "main",
                    token: Optional[str] = None) -> list[str]:
    """Recursively list files in a repo directory. Returns list of paths.

    Includes retries for transient network failures.
    """
    import httpx

    t = token or _token_for_repo(repo) or os.getenv("GITHUB_TOKEN", "")
    headers = {
        "Authorization": f"Bearer {t}",
        "Accept": "application/vnd.github.v3+json",
    }

    last_error = None
    for attempt in range(1, 4):
        try:
            r = httpx.get(
                f"https://api.github.com/repos/{repo}/git/trees/{ref}",
                headers=headers,
                params={"recursive": "1"},
                timeout=30,
            )
            r.raise_for_status()
            tree = r.json().get("tree", [])
            return [item["path"] for item in tree if item["type"] == "blob"]
        except Exception as e:
            last_error = e
            log.warning("admin.fetch_tree.attempt_failed",
                        repo=repo, attempt=attempt, error=str(e))
            if attempt < 3:
                time.sleep(5)

    raise RuntimeError(f"Failed to fetch repo tree after 3 attempts: {last_error}")


REPOS = {
    "blog": "KlimDos/my-blog",
    "aggregator": "eblooo/moto-news",
}

# Key files to fetch per repo
_BLOG_KEY_FILES = [
    "config.yaml", "config.toml", "hugo.yaml", "hugo.toml",
]
_AGGREGATOR_KEY_FILES = [
    "config.yaml", "go.mod",
    "agents/agents.yaml", "agents/requirements.txt",
    "Dockerfile", "agents/Dockerfile",
]

# Keyword → (repo_key, [paths]) for targeted file fetching
_KEYWORD_FILES = {
    "seo": ("blog", ["layouts/partials/head.html", "layouts/partials/seo.html",
                      "layouts/_default/baseof.html"]),
    "robot": ("blog", ["static/robots.txt"]),
    "sitemap": ("blog", ["layouts/sitemap.xml"]),
    "навигац": ("blog", ["layouts/partials/header.html", "layouts/partials/nav.html"]),
    "меню": ("blog", ["layouts/partials/header.html"]),
    "css": ("blog", ["assets/css/extended/custom.css", "assets/css/common/main.css"]),
    "rss": ("blog", ["layouts/_default/rss.xml", "layouts/index.xml"]),
    "изображен": ("blog", ["layouts/partials/post_meta.html"]),
    "перевод": ("aggregator", ["internal/translator/ollama.go",
                                "internal/translator/deepl.go"]),
    "категори": ("aggregator", ["internal/formatter/markdown.go"]),
    "формат": ("aggregator", ["internal/formatter/markdown.go"]),
    "fetch": ("aggregator", ["internal/fetcher/rss.go",
                              "internal/fetcher/scraper.go"]),
    "publish": ("aggregator", ["internal/publisher/hugo.go",
                                "internal/publisher/github.go"]),
}


def fetch_context_for_discussion(
    discussion_title: str,
    discussion_body: str,
) -> str:
    """Build source code context from BOTH repos for the LLM.

    Fetches file trees and key config files from both repos,
    plus keyword-matched files relevant to the discussion.
    """
    context_parts = []

    # 1. File trees from both repos
    for label, repo in REPOS.items():
        token = _token_for_repo(repo)
        try:
            tree = fetch_repo_tree(repo, token=token)
            tree_text = "\n".join(tree[:150])
            if len(tree) > 150:
                tree_text += f"\n... and {len(tree) - 150} more files"
            context_parts.append(
                f"=== File tree: {repo} ({label}) ===\n{tree_text}")
            log.info("admin.context.tree", repo=repo, files=len(tree))
        except Exception as e:
            log.warning("admin.context.tree_error", repo=repo, error=str(e))

    # 2. Key config files from both repos
    for repo, key_files in [
        (REPOS["blog"], _BLOG_KEY_FILES),
        (REPOS["aggregator"], _AGGREGATOR_KEY_FILES),
    ]:
        token = _token_for_repo(repo)
        for fpath in key_files:
            try:
                content, _ = get_file_content(repo, fpath, token=token)
                context_parts.append(
                    f"=== {repo}: {fpath} ===\n{content[:3000]}")
                log.info("admin.context.file_ok", repo=repo, path=fpath)
            except Exception:
                pass

    # 3. Keyword-matched files
    discussion_text = f"{discussion_title} {discussion_body}".lower()
    fetched_extra: set[str] = set()

    for keyword, (repo_key, paths) in _KEYWORD_FILES.items():
        if keyword in discussion_text:
            repo = REPOS[repo_key]
            token = _token_for_repo(repo)
            for fpath in paths:
                key = f"{repo}:{fpath}"
                if key not in fetched_extra:
                    try:
                        content, _ = get_file_content(repo, fpath, token=token)
                        context_parts.append(
                            f"=== {repo}: {fpath} (keyword: '{keyword}') ===\n"
                            f"{content[:4000]}")
                        fetched_extra.add(key)
                        log.info("admin.context.keyword_match",
                                 repo=repo, keyword=keyword, path=fpath)
                    except Exception:
                        pass

    full_context = "\n\n".join(context_parts)
    if len(full_context) > 40000:
        full_context = full_context[:40000] + "\n\n[... context truncated ...]"

    log.info("admin.context.done", total_chars=len(full_context))
    return full_context


# ---------------------------------------------------------------------------
# Step 3: LLM generates code changes
# ---------------------------------------------------------------------------

PLAN_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You are a senior software engineer implementing improvements for a motorcycle blog ecosystem.
You have access to TWO repositories:

1. **eblooo/moto-news** — Go-based news aggregator + Python AI agents
   - cmd/, internal/ — Go backend (RSS fetcher, translator, publisher)
   - agents/ — Python AI agents (user_agent, site_assessor, admin_agent)
   - config.yaml — aggregator config (RSS sources, translator settings)

2. **KlimDos/my-blog** — Hugo blog site (theme: PaperMod)
   - config.yaml / hugo.yaml — Hugo site configuration
   - layouts/ — Hugo templates (partials, shortcodes, pages)
   - static/ — static files (robots.txt, favicons, images)
   - content/ — blog posts (auto-generated markdown)

You MUST set "target_repo" to the correct repository for the changes.
Blog/frontend changes (robots.txt, SEO, templates, CSS) → "KlimDos/my-blog"
Aggregator/agent changes (Go code, Python agents, RSS config) → "eblooo/moto-news"

CRITICAL RULES:
1. Output ONLY valid JSON — no markdown fences, no explanations outside JSON.
2. The "content" field for each file MUST be a STRING (the full file text), never an object/dict.
3. Only change files that are strictly necessary.
4. Use descriptive branch names: feature/<short-description> or fix/<short-description>.
5. Write clean, production-ready code with proper error handling.
6. Keep the PR focused — one improvement per PR.
7. If the suggestion cannot be implemented with code changes, set "feasible" to false.

Output JSON schema:
{{
  "feasible": true,
  "reason": "only if feasible=false — explain why",
  "target_repo": "KlimDos/my-blog or eblooo/moto-news",
  "branch_name": "feature/short-description",
  "pr_title": "Short PR title",
  "pr_body": "Markdown description of what was changed and why",
  "files": [
    {{
      "path": "relative/path/to/file",
      "content": "full file content as a string"
    }}
  ]
}}"""),

    ("human", """=== GitHub Discussion (Suggestion to Implement) ===
Title: {discussion_title}
Body:
{discussion_body}

=== Source code from both repositories ===

{source_context}

=== Instructions ===
Analyze the suggestion and decide which repository to target:
- Blog/frontend improvements → KlimDos/my-blog
- Aggregator/agent improvements → eblooo/moto-news

Set "target_repo" in your JSON output accordingly.
Output ONLY a JSON object. The "content" field must be a string, not a nested object."""),
])


def generate_changes(
    config,
    discussion: dict,
    source_context: str,
) -> dict:
    """Ask the LLM to generate code changes for the discussion suggestion.

    The LLM decides which repo to target (blog vs aggregator).
    Includes timing and detailed logging.
    """
    log.info("admin.llm_generate",
             discussion=discussion["number"],
             provider=config.llm.provider,
             model=config.llm.admin_model)

    llm = create_llm(config, role="admin")
    chain = PLAN_PROMPT | llm | StrOutputParser()

    llm_start = time.monotonic()
    log.info("admin.llm_generate.invoking")

    result_text = chain.invoke({
        "discussion_title": discussion["title"],
        "discussion_body": discussion["body"] or "(no body)",
        "source_context": source_context,
    })

    elapsed = round(time.monotonic() - llm_start, 1)
    log.info("admin.llm_generate.done",
             response_length=len(result_text),
             elapsed_seconds=elapsed)
    log.debug("admin.llm_generate.raw", raw=result_text[:1000])

    parsed = _parse_changes_json(result_text)
    return _validate_changes(parsed)


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


def _validate_changes(changes: dict) -> dict:
    """Validate and sanitize parsed LLM changes.

    Fixes common LLM mistakes:
    - content field as dict/list instead of string
    - missing fields
    """
    # Validate files
    files = changes.get("files", [])
    valid_files = []
    for f in files:
        path = f.get("path", "")
        content = f.get("content", "")

        if not path:
            log.warning("admin.validate.skip_no_path")
            continue

        # If content is not a string, convert it
        if isinstance(content, (dict, list)):
            log.warning("admin.validate.content_not_string",
                        path=path, content_type=type(content).__name__)
            content = json.dumps(content, indent=2, ensure_ascii=False)

        if not isinstance(content, str):
            log.warning("admin.validate.skip_bad_content",
                        path=path, content_type=type(content).__name__)
            continue

        valid_files.append({"path": path, "content": content})

    changes["files"] = valid_files
    return changes


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
    """Post a comment on the discussion with the PR link.

    Includes retries for transient network failures.
    """
    owner, name = repo.split("/")

    # Get discussion node ID
    id_query = """
    query($owner: String!, $name: String!, $number: Int!) {
      repository(owner: $owner, name: $name) {
        discussion(number: $number) { id }
      }
    }
    """

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

    last_error = None
    for attempt in range(1, 4):
        try:
            data = _graphql_query(id_query, {
                "owner": owner, "name": name, "number": discussion_number,
            })
            discussion_id = data["repository"]["discussion"]["id"]

            _graphql_query(mutation, {"discussionId": discussion_id, "body": body})
            log.info("admin.comment_posted",
                     discussion=discussion_number, pr_url=pr_url)
            return
        except Exception as e:
            last_error = e
            log.warning("admin.comment.attempt_failed",
                        discussion=discussion_number,
                        attempt=attempt, error=str(e))
            if attempt < 3:
                time.sleep(5)

    log.error("admin.comment.all_failed",
              discussion=discussion_number, error=str(last_error))


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

DEFAULT_TARGET_REPO = "eblooo/moto-news"


def _token_for_repo(repo: str) -> Optional[str]:
    """Return the correct GitHub token for write access to the given repo.

    - eblooo/moto-news  -> EBLOOO_GH_TOKEN
    - KlimDos/my-blog   -> GITHUB_TOKEN (GIT_TOKEN from Doppler)
    - anything else      -> GITHUB_TOKEN (fallback)
    """
    if repo.startswith("eblooo/"):
        token = os.getenv("EBLOOO_GH_TOKEN", "")
        if token:
            log.info("admin.token_for_repo", repo=repo, token_source="EBLOOO_GH_TOKEN")
            return token
        log.warning("admin.token_for_repo.missing",
                    repo=repo, expected_env="EBLOOO_GH_TOKEN")
    return None  # falls back to GITHUB_TOKEN inside github_pr helpers


def process_discussion(
    config,
    discussion: dict,
    dry_run: bool = False,
) -> Optional[str]:
    """Process a single discussion: generate changes and create PR.

    Includes full retry logic for each step. Returns PR URL on success, None on skip/failure.
    """
    number = discussion["number"]
    title = discussion["title"]
    start_time = time.monotonic()

    log.info("admin.process", discussion=number, title=title[:80])

    if is_already_processed(discussion):
        log.info("admin.process.skip_already_processed", discussion=number)
        return None

    # Fetch source context from BOTH repos (with retries)
    source_context = None
    for attempt in range(1, 4):
        try:
            log.info("admin.process.fetch_context",
                     discussion=number, attempt=attempt)
            source_context = fetch_context_for_discussion(
                discussion_title=title,
                discussion_body=discussion.get("body", ""),
            )
            break
        except Exception as e:
            log.warning("admin.process.fetch_context_failed",
                        discussion=number, attempt=attempt, error=str(e))
            if attempt < 3:
                time.sleep(10)

    if not source_context:
        log.error("admin.process.no_context", discussion=number)
        return None

    elapsed_context = round(time.monotonic() - start_time, 1)
    log.info("admin.process.context_ready",
             discussion=number,
             context_chars=len(source_context),
             elapsed_seconds=elapsed_context)

    # Generate changes with LLM (with retries)
    changes = None
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.info("admin.process.llm_attempt",
                     discussion=number,
                     attempt=attempt,
                     max_retries=MAX_RETRIES)
            changes = generate_changes(config, discussion, source_context)
            break
        except Exception as e:
            last_error = e
            log.warning("admin.process.llm_attempt_failed",
                        discussion=number,
                        attempt=attempt,
                        max_retries=MAX_RETRIES,
                        error=str(e),
                        error_type=type(e).__name__)
            if attempt < MAX_RETRIES:
                delay = RETRY_DELAY_SECONDS * attempt
                log.info("admin.process.llm_retrying",
                         discussion=number,
                         next_attempt=attempt + 1,
                         delay_seconds=delay)
                time.sleep(delay)

    if not changes:
        elapsed = round(time.monotonic() - start_time, 1)
        log.error("admin.process.llm_all_failed",
                  discussion=number,
                  error=str(last_error),
                  elapsed_seconds=elapsed)
        return None

    # Check feasibility
    if not changes.get("feasible", True):
        reason = changes.get("reason", "unknown")
        elapsed = round(time.monotonic() - start_time, 1)
        log.info("admin.process.not_feasible",
                 discussion=number, reason=reason, elapsed_seconds=elapsed)
        return None

    # Extract target repo from LLM output (fallback to aggregator)
    target_repo = changes.get("target_repo", DEFAULT_TARGET_REPO)
    # Validate: must be one of the known repos
    valid_repos = set(REPOS.values())
    if target_repo not in valid_repos:
        log.warning("admin.process.unknown_target_repo",
                     discussion=number, target_repo=target_repo,
                     fallback=DEFAULT_TARGET_REPO)
        target_repo = DEFAULT_TARGET_REPO
    log.info("admin.process.target_repo", discussion=number, repo=target_repo)

    files = changes.get("files", [])
    # Add timestamp suffix to avoid conflicts with stale branches from prior runs
    ts = datetime.now().strftime("%m%d%H%M")
    raw_branch = changes.get("branch_name", f"feature/discussion-{number}")
    branch = f"{raw_branch}-{ts}"
    pr_title = changes.get("pr_title", f"Implement: {title[:60]}")
    pr_body = changes.get("pr_body", f"Implements suggestion from discussion #{number}")

    if not files:
        log.warning("admin.process.no_files", discussion=number)
        return None

    log.info("admin.process.changes_ready",
             discussion=number,
             target_repo=target_repo,
             branch=branch,
             file_count=len(files),
             files=[f["path"] for f in files])

    if dry_run:
        elapsed = round(time.monotonic() - start_time, 1)
        log.info("admin.process.dry_run_skip",
                 discussion=number, target_repo=target_repo,
                 elapsed_seconds=elapsed)
        for f in files:
            log.info("admin.dry_run.file",
                     path=f["path"],
                     content_length=len(f.get("content", "")))
        return None

    # Create the PR (with retries for transient GitHub API failures)
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.info("admin.process.creating_pr",
                     discussion=number, attempt=attempt,
                     branch=branch, target_repo=target_repo)
            repo_token = _token_for_repo(target_repo)
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
                token=repo_token,
            )
            pr_url = pr["html_url"]
            elapsed = round(time.monotonic() - start_time, 1)
            log.info("admin.process.pr_created",
                     discussion=number, pr_url=pr_url,
                     elapsed_seconds=elapsed)

            # Comment on the discussion with PR link
            discussion_repo = config.github.repo
            comment_on_discussion(discussion_repo, number, pr_url)

            return pr_url

        except Exception as e:
            last_error = e
            log.warning("admin.process.pr_attempt_failed",
                        discussion=number,
                        attempt=attempt,
                        error=str(e),
                        error_type=type(e).__name__)
            if attempt < MAX_RETRIES:
                delay = RETRY_DELAY_SECONDS * attempt
                log.info("admin.process.pr_retrying",
                         next_attempt=attempt + 1, delay_seconds=delay)
                time.sleep(delay)

    elapsed = round(time.monotonic() - start_time, 1)
    log.error("admin.process.pr_all_failed",
              discussion=number,
              error=str(last_error),
              elapsed_seconds=elapsed)
    return None


def run_pipeline(config, once: bool = False, dry_run: bool = False) -> None:
    """Main admin agent pipeline with full retry and timing."""
    log.info("admin.starting",
             provider=config.llm.provider,
             model=config.llm.admin_model,
             repo=config.github.repo,
             github_token_set=bool(config.github.token),
             dry_run=dry_run,
             pid=os.getpid())

    run_count = 0
    while True:
        run_count += 1
        run_start = time.monotonic()
        log.info("admin.run", run_number=run_count)

        try:
            discussions = fetch_implement_discussions(config.github.repo)

            if not discussions:
                log.info("admin.no_implement_discussions")
            else:
                processed = 0
                skipped = 0
                failed = 0
                for d in discussions:
                    try:
                        pr_url = process_discussion(config, d, dry_run=dry_run)
                        if pr_url:
                            processed += 1
                            log.info("admin.discussion_done",
                                     discussion=d["number"], pr_url=pr_url)
                        else:
                            skipped += 1
                    except Exception as e:
                        failed += 1
                        log.error("admin.discussion_error",
                                  discussion=d["number"],
                                  error=str(e),
                                  error_type=type(e).__name__)

                run_elapsed = round(time.monotonic() - run_start, 1)
                log.info("admin.run_complete",
                         run_number=run_count,
                         total=len(discussions),
                         processed=processed,
                         skipped=skipped,
                         failed=failed,
                         elapsed_seconds=run_elapsed)

        except Exception as e:
            run_elapsed = round(time.monotonic() - run_start, 1)
            log.error("admin.pipeline_error",
                      error=str(e),
                      error_type=type(e).__name__,
                      elapsed_seconds=run_elapsed)

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

    log.info("admin.starting",
             config_path=args.config,
             once=args.once,
             dry_run=args.dry_run,
             pid=os.getpid())

    config = load_config(args.config)

    log.info("admin.config_loaded",
             llm_provider=config.llm.provider,
             llm_model=config.llm.admin_model,
             github_repo=config.github.repo,
             github_token_set=bool(config.github.token))

    if not config.github.token:
        log.error("admin.no_github_token",
                  hint="export GITHUB_TOKEN=ghp_xxxxxxxxxxxx or use --dry-run")
        sys.exit(1)

    run_pipeline(config, once=args.once, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
