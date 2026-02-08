"""
Tool for reading and analyzing the blog site.
Fetches pages, extracts content, analyzes structure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

import httpx
from bs4 import BeautifulSoup
from langchain_core.tools import tool


@dataclass
class PageInfo:
    url: str
    title: str
    content: str
    links: List[str]
    meta_description: str
    headings: List[str]
    word_count: int


def fetch_page(url: str, timeout: int = 30) -> PageInfo:
    """Fetch and parse a single page."""
    headers = {
        "User-Agent": "MotoNewsSiteAssessor/1.0 (blog analysis bot)"
    }

    response = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    # Extract title
    title = ""
    title_tag = soup.find("title")
    if title_tag:
        title = title_tag.get_text(strip=True)

    # Extract meta description
    meta_desc = ""
    meta_tag = soup.find("meta", attrs={"name": "description"})
    if meta_tag and meta_tag.get("content"):
        meta_desc = meta_tag["content"]

    # Extract main content
    content = ""
    # Try main content area (Hugo PaperMod)
    main = soup.find("main") or soup.find("article") or soup.find("div", class_="md-content")
    if main:
        # Remove navigation, scripts, styles
        for tag in main.find_all(["nav", "script", "style", "footer"]):
            tag.decompose()
        content = main.get_text(separator="\n", strip=True)
    else:
        body = soup.find("body")
        if body:
            content = body.get_text(separator="\n", strip=True)

    # Extract links
    links = []
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if href.startswith(("http://", "https://")) and url.split("/")[2] in href:
            links.append(href)
        elif href.startswith("/"):
            base = "/".join(url.split("/")[:3])
            links.append(base + href)

    links = list(set(links))

    # Extract headings
    headings = []
    for level in range(1, 5):
        for h in soup.find_all(f"h{level}"):
            text = h.get_text(strip=True)
            if text:
                headings.append(f"h{level}: {text}")

    # Word count
    word_count = len(content.split()) if content else 0

    return PageInfo(
        url=url,
        title=title,
        content=content[:5000],  # Limit content for LLM context
        links=links[:50],
        meta_description=meta_desc,
        headings=headings,
        word_count=word_count,
    )


@tool
def get_site_snapshot(url: str = "https://blog.alimov.top") -> str:
    """
    Fetch a snapshot of the blog site for analysis.
    Returns page title, structure, content summary, and links.
    """
    try:
        page = fetch_page(url)

        result = f"""=== Site Snapshot: {page.url} ===
Title: {page.title}
Meta Description: {page.meta_description}
Word Count: {page.word_count}

--- Headings ---
{chr(10).join(page.headings[:20]) if page.headings else 'No headings found'}

--- Content (first 3000 chars) ---
{page.content[:3000]}

--- Internal Links ({len(page.links)} total) ---
{chr(10).join(page.links[:20])}
"""
        return result

    except Exception as e:
        return f"Error fetching site: {str(e)}"


@tool
def get_page_content(url: str) -> str:
    """
    Fetch content from a specific page on the blog.
    Useful for analyzing individual articles or sections.
    """
    try:
        page = fetch_page(url)
        return f"""=== Page: {page.url} ===
Title: {page.title}
Word Count: {page.word_count}

--- Content ---
{page.content[:4000]}
"""
    except Exception as e:
        return f"Error fetching page: {str(e)}"


@tool
def analyze_site_structure(url: str = "https://blog.alimov.top") -> str:
    """
    Analyze the overall structure of the blog site.
    Returns information about navigation, categories, and organization.
    """
    try:
        page = fetch_page(url)

        # Find navigation structure
        response = httpx.get(url, timeout=30, follow_redirects=True)
        soup = BeautifulSoup(response.text, "html.parser")

        # Extract nav items
        nav_items = []
        nav = soup.find("nav", class_="md-nav--primary") or soup.find("nav")
        if nav:
            for a_tag in nav.find_all("a"):
                text = a_tag.get_text(strip=True)
                href = a_tag.get("href", "")
                if text and href:
                    nav_items.append(f"  {text} -> {href}")

        # Find categories
        categories = []
        for tag in soup.find_all(class_=re.compile(r"category|tag")):
            text = tag.get_text(strip=True)
            if text and len(text) < 50:
                categories.append(text)
        categories = list(set(categories))

        result = f"""=== Site Structure Analysis: {url} ===

--- Navigation ({len(nav_items)} items) ---
{chr(10).join(nav_items[:30]) if nav_items else 'No navigation found'}

--- Categories/Tags ---
{chr(10).join(categories[:20]) if categories else 'No categories found'}

--- Page Headings ---
{chr(10).join(page.headings[:20]) if page.headings else 'No headings found'}

--- Content Summary ---
Total links on page: {len(page.links)}
Word count: {page.word_count}
"""
        return result

    except Exception as e:
        return f"Error analyzing site: {str(e)}"
