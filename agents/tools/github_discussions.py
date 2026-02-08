"""
Tool for interacting with GitHub Discussions.
Reads and writes comments in the "For Developers" category.
"""

import os
from dataclasses import dataclass

import httpx
from langchain_core.tools import tool


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


def _graphql_query(query: str, variables: dict | None = None) -> dict:
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
    category: str = "For Developers",
    limit: int = 10,
) -> str:
    """
    List recent discussions from the specified GitHub repository and category.
    Returns discussion titles, authors, and comment counts.
    """
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
        return f"Error: {str(e)} — Set GITHUB_TOKEN to use this tool."
    except Exception as e:
        return f"Error listing discussions: {str(e)}"


@tool
def get_discussion_comments(
    repo: str = "KlimDos/my-blog",
    discussion_number: int = 1,
) -> str:
    """
    Get all comments from a specific discussion.
    """
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
            return f"Discussion #{discussion_number} not found"

        result = f"=== Discussion #{discussion_number}: {disc['title']} ===\n"
        result += f"Author: {disc['author']['login'] if disc['author'] else 'unknown'}\n"
        result += f"Body: {disc['body'][:500]}\n\n"

        comments = disc["comments"]["nodes"]
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
        return f"Error: {str(e)}"
    except Exception as e:
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
    if not comment_body.strip():
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
        return f"Comment posted successfully! URL: {comment.get('url', 'N/A')}"

    except ValueError as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error posting comment: {str(e)}"


@tool
def create_discussion(
    repo: str = "KlimDos/my-blog",
    title: str = "",
    body: str = "",
    category: str = "For Developers",
) -> str:
    """
    Create a new GitHub Discussion in the specified category.
    """
    if not title.strip() or not body.strip():
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
            return f"Category '{category}' not found. Available: {available}"

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

        result = _graphql_query(mutation, {
            "repoId": repo_id,
            "categoryId": category_id,
            "title": title,
            "body": body,
        })

        disc = result["createDiscussion"]["discussion"]
        return f"Discussion created: #{disc['number']} — {disc['url']}"

    except ValueError as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error creating discussion: {str(e)}"
