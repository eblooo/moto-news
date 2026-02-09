"""
Tool for interacting with GitHub Discussions.
Reads and writes comments in the "Ideas" category.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional

import httpx
import structlog
from langchain_core.tools import tool


log = structlog.get_logger()

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"


@dataclass
class Discussion:
    id: str
    number: int
    title: str
    body: str
    author: str
    created_at: str
    comments_count: int
    category: str
    url: str


@dataclass
class Comment:
    id: str
    body: str
    author: str
    created_at: str


def _get_headers() -> dict:
    """Get GitHub API headers with token."""
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        raise ValueError("GITHUB_TOKEN environment variable is not set")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _graphql_query(query: str, variables: Optional[Dict] = None) -> dict:
    """Execute a GraphQL query against GitHub API."""
    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    response = httpx.post(
        GITHUB_GRAPHQL_URL,
        headers=_get_headers(),
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()

    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")

    return data["data"]


@tool
def list_discussions(
    repo: str = "KlimDos/my-blog",
    category: str = "Ideas",
    limit: int = 10,
) -> str:
    """
    List recent discussions from the specified GitHub repository and category.
    Returns discussion titles, authors, and comment counts.
    """
    log.info("tool.list_discussions", repo=repo, category=category, limit=limit)
    owner, name = repo.split("/")

    query = """
    query($owner: String!, $name: String!, $limit: Int!) {
      repository(owner: $owner, name: $name) {
        discussions(first: $limit, orderBy: {field: CREATED_AT, direction: DESC}) {
          nodes {
            id
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
            "limit": limit,
        })

        discussions = data["repository"]["discussions"]["nodes"]

        # Filter by category if specified
        if category:
            discussions = [
                d for d in discussions
                if d["category"]["name"].lower() == category.lower()
            ]

        log.info("tool.list_discussions.result",
                 total=len(discussions), category=category)

        if not discussions:
            return f"No discussions found in category '{category}'"

        result = f"=== GitHub Discussions ({repo}) — {category} ===\n\n"
        for d in discussions:
            result += f"#{d['number']}: {d['title']}\n"
            result += f"  Author: {d['author']['login'] if d['author'] else 'unknown'}\n"
            result += f"  Created: {d['createdAt']}\n"
            result += f"  Comments: {d['comments']['totalCount']}\n"
            result += f"  URL: {d['url']}\n"
            if d["body"]:
                result += f"  Body: {d['body'][:200]}...\n"
            result += "\n"

        return result

    except ValueError as e:
        log.error("tool.list_discussions.auth_error", error=str(e))
        return f"Error: {str(e)} — Set GITHUB_TOKEN to use this tool."
    except Exception as e:
        log.error("tool.list_discussions.error", error=str(e))
        return f"Error listing discussions: {str(e)}"


@tool
def get_discussion_comments(
    repo: str = "KlimDos/my-blog",
    discussion_number: int = 1,
) -> str:
    """
    Get all comments from a specific discussion.
    """
    log.info("tool.get_discussion_comments",
             repo=repo, discussion_number=discussion_number)
    owner, name = repo.split("/")

    query = """
    query($owner: String!, $name: String!, $number: Int!) {
      repository(owner: $owner, name: $name) {
        discussion(number: $number) {
          id
          title
          body
          author { login }
          comments(first: 50) {
            nodes {
              id
              body
              author { login }
              createdAt
            }
          }
        }
      }
    }
    """

    try:
        data = _graphql_query(query, {
            "owner": owner,
            "name": name,
            "number": discussion_number,
        })

        disc = data["repository"]["discussion"]
        if not disc:
            log.warning("tool.get_discussion_comments.not_found",
                        discussion_number=discussion_number)
            return f"Discussion #{discussion_number} not found"

        comments = disc["comments"]["nodes"]
        log.info("tool.get_discussion_comments.result",
                 discussion_number=discussion_number,
                 title=disc["title"],
                 comments_count=len(comments))

        result = f"=== Discussion #{discussion_number}: {disc['title']} ===\n"
        result += f"Author: {disc['author']['login'] if disc['author'] else 'unknown'}\n"
        result += f"Body: {disc['body'][:500]}\n\n"

        if not comments:
            result += "No comments yet.\n"
        else:
            result += f"--- {len(comments)} Comments ---\n"
            for c in comments:
                author = c["author"]["login"] if c["author"] else "unknown"
                result += f"\n[{c['createdAt']}] @{author}:\n"
                result += f"{c['body'][:300]}\n"

        return result

    except ValueError as e:
        log.error("tool.get_discussion_comments.auth_error", error=str(e))
        return f"Error: {str(e)}"
    except Exception as e:
        log.error("tool.get_discussion_comments.error", error=str(e))
        return f"Error: {str(e)}"


@tool
def create_discussion_comment(
    repo: str = "KlimDos/my-blog",
    discussion_number: int = 1,
    comment_body: str = "",
) -> str:
    """
    Post a comment to a specific GitHub Discussion.
    The comment should be constructive and relevant.
    """
    log.info("tool.create_discussion_comment",
             repo=repo, discussion_number=discussion_number,
             body_length=len(comment_body))

    if not comment_body.strip():
        log.warning("tool.create_discussion_comment.empty_body")
        return "Error: comment_body cannot be empty"

    owner, name = repo.split("/")

    # First, get discussion ID
    id_query = """
    query($owner: String!, $name: String!, $number: Int!) {
      repository(owner: $owner, name: $name) {
        discussion(number: $number) {
          id
        }
      }
    }
    """

    try:
        data = _graphql_query(id_query, {
            "owner": owner,
            "name": name,
            "number": discussion_number,
        })

        discussion_id = data["repository"]["discussion"]["id"]
        log.info("tool.create_discussion_comment.posting",
                 discussion_id=discussion_id)

        # Create comment
        mutation = """
        mutation($discussionId: ID!, $body: String!) {
          addDiscussionComment(input: {discussionId: $discussionId, body: $body}) {
            comment {
              id
              url
              createdAt
            }
          }
        }
        """

        result = _graphql_query(mutation, {
            "discussionId": discussion_id,
            "body": comment_body,
        })

        comment = result["addDiscussionComment"]["comment"]
        url = comment.get("url", "N/A")
        log.info("tool.create_discussion_comment.success",
                 discussion_number=discussion_number, url=url)
        return f"Comment posted successfully! URL: {url}"

    except ValueError as e:
        log.error("tool.create_discussion_comment.auth_error", error=str(e))
        return f"Error: {str(e)}"
    except Exception as e:
        log.error("tool.create_discussion_comment.error", error=str(e))
        return f"Error posting comment: {str(e)}"


def post_discussion(repo: str, title: str, body: str, category: str) -> str:
    """Create a new GitHub Discussion. Returns URL or error message.

    This is a shared helper used by both user_agent and site_assessor pipelines.
    Unlike the @tool-decorated create_discussion(), this function is meant
    to be called directly from pipeline code.
    """
    log.info("post_discussion",
             repo=repo, category=category,
             title=title[:80], body_length=len(body))

    if not title or not body:
        log.info("post_discussion.skip_empty")
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
        log.error("post_discussion.category_not_found",
                  category=category, available=available)
        return f"Category '{category}' not found. Available: {available}"

    log.info("post_discussion.category_found",
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

    log.info("post_discussion.creating")
    result = _graphql_query(mutation, {
        "repoId": repo_id,
        "categoryId": category_id,
        "title": title,
        "body": body,
    })

    disc = result["createDiscussion"]["discussion"]
    url = disc["url"]
    log.info("post_discussion.success",
             number=disc["number"], url=url)
    return url


@tool
def create_discussion(
    repo: str = "KlimDos/my-blog",
    title: str = "",
    body: str = "",
    category: str = "Ideas",
) -> str:
    """
    Create a new GitHub Discussion in the specified category.
    """
    log.info("tool.create_discussion",
             repo=repo, title=title[:80], category=category,
             body_length=len(body))

    if not title.strip() or not body.strip():
        log.warning("tool.create_discussion.empty_fields")
        return "Error: title and body cannot be empty"

    owner, name = repo.split("/")

    # Get category ID
    cat_query = """
    query($owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) {
        discussionCategories(first: 20) {
          nodes {
            id
            name
          }
        }
      }
    }
    """

    try:
        data = _graphql_query(cat_query, {
            "owner": owner,
            "name": name,
        })

        categories = data["repository"]["discussionCategories"]["nodes"]
        category_id = None
        for cat in categories:
            if cat["name"].lower() == category.lower():
                category_id = cat["id"]
                break

        if not category_id:
            available = [c["name"] for c in categories]
            log.error("tool.create_discussion.category_not_found",
                      category=category, available=available)
            return f"Category '{category}' not found. Available: {available}"

        log.info("tool.create_discussion.category_found",
                 category=category, category_id=category_id)

        # Get repo ID
        repo_query = """
        query($owner: String!, $name: String!) {
          repository(owner: $owner, name: $name) {
            id
          }
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
            discussion {
              id
              number
              url
            }
          }
        }
        """

        log.info("tool.create_discussion.creating", title=title[:80])
        result = _graphql_query(mutation, {
            "repoId": repo_id,
            "categoryId": category_id,
            "title": title,
            "body": body,
        })

        disc = result["createDiscussion"]["discussion"]
        log.info("tool.create_discussion.success",
                 number=disc["number"], url=disc["url"])
        return f"Discussion created: #{disc['number']} — {disc['url']}"

    except ValueError as e:
        log.error("tool.create_discussion.auth_error", error=str(e))
        return f"Error: {str(e)}"
    except Exception as e:
        log.error("tool.create_discussion.error", error=str(e))
        return f"Error creating discussion: {str(e)}"
