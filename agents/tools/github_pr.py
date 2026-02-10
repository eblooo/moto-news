"""
GitHub Pull Request tools — create branches, commit files, open PRs via REST API.

Used by admin_agent to implement suggestions from GitHub Discussions.
"""

from __future__ import annotations

import base64
import os
from typing import Optional

import httpx
import structlog

log = structlog.get_logger()

GITHUB_API = "https://api.github.com"


def _headers(token: Optional[str] = None) -> dict:
    """GitHub REST API headers.

    Args:
        token: Explicit token. Falls back to GITHUB_TOKEN env var.
    """
    t = token or os.getenv("GITHUB_TOKEN", "")
    if not t:
        raise ValueError("No GitHub token provided and GITHUB_TOKEN env var is not set")
    return {
        "Authorization": f"Bearer {t}",
        "Accept": "application/vnd.github.v3+json",
    }


# ---------------------------------------------------------------------------
# Low-level GitHub API helpers
# ---------------------------------------------------------------------------

def get_default_branch(repo: str, token: Optional[str] = None) -> tuple[str, str]:
    """Return (branch_name, head_sha) for the repo's default branch."""
    h = _headers(token)
    r = httpx.get(f"{GITHUB_API}/repos/{repo}", headers=h, timeout=30)
    r.raise_for_status()
    branch = r.json()["default_branch"]

    r2 = httpx.get(
        f"{GITHUB_API}/repos/{repo}/git/ref/heads/{branch}",
        headers=h, timeout=30,
    )
    r2.raise_for_status()
    sha = r2.json()["object"]["sha"]
    log.info("github_pr.default_branch", repo=repo, branch=branch, sha=sha[:8])
    return branch, sha


def create_branch(repo: str, branch_name: str, from_sha: str,
                  token: Optional[str] = None) -> None:
    """Create a new branch from the given SHA."""
    r = httpx.post(
        f"{GITHUB_API}/repos/{repo}/git/refs",
        headers=_headers(token),
        json={"ref": f"refs/heads/{branch_name}", "sha": from_sha},
        timeout=30,
    )
    r.raise_for_status()
    log.info("github_pr.branch_created", repo=repo, branch=branch_name)


def get_file_content(repo: str, path: str, ref: str = "main",
                     token: Optional[str] = None) -> tuple[str, str]:
    """Get file content and blob SHA. Returns (content_text, blob_sha).

    Raises httpx.HTTPStatusError if file not found.
    """
    r = httpx.get(
        f"{GITHUB_API}/repos/{repo}/contents/{path}",
        headers=_headers(token),
        params={"ref": ref},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    content = base64.b64decode(data["content"]).decode("utf-8")
    return content, data["sha"]


def create_or_update_file(
    repo: str,
    path: str,
    content: str,
    message: str,
    branch: str,
    file_sha: Optional[str] = None,
    token: Optional[str] = None,
) -> str:
    """Create or update a single file on a branch. Returns the new commit SHA."""
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if file_sha:
        payload["sha"] = file_sha

    r = httpx.put(
        f"{GITHUB_API}/repos/{repo}/contents/{path}",
        headers=_headers(token),
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    commit_sha = r.json()["commit"]["sha"]
    log.info("github_pr.file_committed",
             repo=repo, path=path, branch=branch, sha=commit_sha[:8])
    return commit_sha


def create_pull_request(
    repo: str,
    title: str,
    body: str,
    head: str,
    base: str = "main",
    token: Optional[str] = None,
) -> dict:
    """Create a pull request. Returns dict with 'number', 'html_url'."""
    r = httpx.post(
        f"{GITHUB_API}/repos/{repo}/pulls",
        headers=_headers(token),
        json={
            "title": title,
            "body": body,
            "head": head,
            "base": base,
        },
        timeout=30,
    )
    r.raise_for_status()
    pr = r.json()
    log.info("github_pr.pr_created",
             repo=repo, number=pr["number"], url=pr["html_url"])
    return {"number": pr["number"], "html_url": pr["html_url"]}


# ---------------------------------------------------------------------------
# High-level: apply a set of file changes as a PR
# ---------------------------------------------------------------------------

def apply_changes_as_pr(
    repo: str,
    branch_name: str,
    pr_title: str,
    pr_body: str,
    files: list[dict],
    token: Optional[str] = None,
) -> dict:
    """Create a branch, commit file changes, and open a PR.

    Args:
        repo: "owner/name"
        branch_name: new branch name (e.g. "feature/add-robots-txt")
        pr_title: PR title
        pr_body: PR body (Markdown)
        files: list of {"path": str, "content": str} dicts
        token: GitHub token with write access to the repo

    Returns:
        dict with 'number', 'html_url' of the created PR.

    Raises:
        httpx.HTTPStatusError on any API failure.
    """
    if not files:
        raise ValueError("No files to commit")

    # 1. Get base branch
    base_branch, base_sha = get_default_branch(repo, token=token)

    # 2. Create feature branch
    create_branch(repo, branch_name, base_sha, token=token)

    # 3. Commit each file
    for f in files:
        path = f["path"]
        content = f["content"]

        # Check if file exists (to get its SHA for updates)
        file_sha = None
        try:
            _, file_sha = get_file_content(repo, path, ref=branch_name, token=token)
            log.info("github_pr.file_exists", path=path, action="update")
        except httpx.HTTPStatusError:
            log.info("github_pr.file_new", path=path, action="create")

        create_or_update_file(
            repo=repo,
            path=path,
            content=content,
            message=f"{'Update' if file_sha else 'Add'} {path}",
            branch=branch_name,
            file_sha=file_sha,
            token=token,
        )

    # 4. Create PR
    pr = create_pull_request(
        repo=repo,
        title=pr_title,
        body=pr_body,
        head=branch_name,
        base=base_branch,
        token=token,
    )
    return pr
