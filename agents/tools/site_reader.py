"""
Tool for reading and analyzing the blog site.
Fetches pages, extracts content, analyzes structure, checks technical
endpoints and HTTP headers.
"""

from __future__ import annotations

import json as _json
import os
import random
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import httpx
import structlog
from bs4 import BeautifulSoup
from langchain_core.tools import tool


log = structlog.get_logger()

_HTTP_HEADERS = {
    "User-Agent": "MotoNewsSiteAssessor/1.0 (blog analysis bot)",
}
_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PageInfo:
    url: str
    title: str
    content: str
    links: List[str]
    meta_description: str
    headings: List[str]
    word_count: int


@dataclass
class StructuredData:
    """Open Graph, Twitter Cards, JSON-LD, canonical, and feed links."""
    og_tags: Dict[str, str] = field(default_factory=dict)
    twitter_tags: Dict[str, str] = field(default_factory=dict)
    json_ld: List[dict] = field(default_factory=list)
    canonical: str = ""
    rss_feed: str = ""
    lang: str = ""


@dataclass
class HeaderAnalysis:
    """Relevant HTTP response headers."""
    cache_control: str = ""
    content_encoding: str = ""
    server: str = ""
    strict_transport_security: str = ""
    content_security_policy: str = ""
    x_frame_options: str = ""
    content_type: str = ""


@dataclass
class SiteReport:
    """Aggregated site analysis — homepage + articles + technical checks."""
    homepage: PageInfo
    articles: List[PageInfo] = field(default_factory=list)
    sitemap_urls: List[str] = field(default_factory=list)
    structured_data: StructuredData = field(default_factory=StructuredData)
    headers: HeaderAnalysis = field(default_factory=HeaderAnalysis)
    robots_txt: str = ""
    has_rss: bool = False
    pagespeed: Optional[dict] = None


# ---------------------------------------------------------------------------
# Core page fetcher (unchanged API for backward compatibility)
# ---------------------------------------------------------------------------

def fetch_page(url: str, timeout: int = _TIMEOUT) -> PageInfo:
    """Fetch and parse a single page."""
    log.info("fetch_page.start", url=url)

    response = httpx.get(url, headers=_HTTP_HEADERS, timeout=timeout,
                         follow_redirects=True)
    response.raise_for_status()
    log.info("fetch_page.http_ok", url=url, status=response.status_code,
             content_length=len(response.text))

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

    log.info("fetch_page.parsed", url=url, title=title[:60],
             word_count=word_count, links=len(links), headings=len(headings))

    return PageInfo(
        url=url,
        title=title,
        content=content[:5000],  # Limit content for LLM context
        links=links[:50],
        meta_description=meta_desc,
        headings=headings,
        word_count=word_count,
    )


# ---------------------------------------------------------------------------
# Technical endpoint helpers
# ---------------------------------------------------------------------------

def _base_url(url: str) -> str:
    """Extract scheme + host from URL (e.g. 'https://blog.alimov.top')."""
    return "/".join(url.rstrip("/").split("/")[:3])


def fetch_sitemap(url: str, timeout: int = _TIMEOUT) -> List[str]:
    """Fetch sitemap.xml and return a list of page URLs."""
    base = _base_url(url)
    sitemap_url = f"{base}/sitemap.xml"
    log.info("fetch_sitemap.start", url=sitemap_url)

    try:
        resp = httpx.get(sitemap_url, headers=_HTTP_HEADERS, timeout=timeout,
                         follow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:
        log.warning("fetch_sitemap.error", url=sitemap_url, error=str(exc))
        return []

    try:
        root = ET.fromstring(resp.text)
        # Handle namespace: {http://www.sitemaps.org/schemas/sitemap/0.9}
        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"

        urls = [loc.text for loc in root.iter(f"{ns}loc") if loc.text]
        log.info("fetch_sitemap.done", url_count=len(urls))
        return urls
    except ET.ParseError as exc:
        log.warning("fetch_sitemap.parse_error", error=str(exc))
        return []


def fetch_robots_txt(url: str, timeout: int = _TIMEOUT) -> str:
    """Fetch robots.txt and return its content."""
    base = _base_url(url)
    robots_url = f"{base}/robots.txt"
    log.info("fetch_robots.start", url=robots_url)

    try:
        resp = httpx.get(robots_url, headers=_HTTP_HEADERS, timeout=timeout,
                         follow_redirects=True)
        resp.raise_for_status()
        log.info("fetch_robots.done", length=len(resp.text))
        return resp.text[:2000]
    except Exception as exc:
        log.warning("fetch_robots.error", url=robots_url, error=str(exc))
        return ""


def analyze_headers(url: str, timeout: int = _TIMEOUT) -> HeaderAnalysis:
    """Fetch the homepage and analyze HTTP response headers."""
    log.info("analyze_headers.start", url=url)

    try:
        resp = httpx.get(url, headers=_HTTP_HEADERS, timeout=timeout,
                         follow_redirects=True)
        h = resp.headers
        analysis = HeaderAnalysis(
            cache_control=h.get("cache-control", ""),
            content_encoding=h.get("content-encoding", ""),
            server=h.get("server", ""),
            strict_transport_security=h.get("strict-transport-security", ""),
            content_security_policy=h.get("content-security-policy", ""),
            x_frame_options=h.get("x-frame-options", ""),
            content_type=h.get("content-type", ""),
        )
        log.info("analyze_headers.done",
                 server=analysis.server,
                 cache=analysis.cache_control[:60],
                 encoding=analysis.content_encoding)
        return analysis
    except Exception as exc:
        log.warning("analyze_headers.error", error=str(exc))
        return HeaderAnalysis()


def extract_structured_data(url: str, timeout: int = _TIMEOUT) -> StructuredData:
    """Fetch a page and extract OG tags, Twitter Cards, JSON-LD, etc."""
    log.info("extract_structured_data.start", url=url)

    try:
        resp = httpx.get(url, headers=_HTTP_HEADERS, timeout=timeout,
                         follow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:
        log.warning("extract_structured_data.error", url=url, error=str(exc))
        return StructuredData()

    soup = BeautifulSoup(resp.text, "html.parser")
    sd = StructuredData()

    # Open Graph
    for tag in soup.find_all("meta", attrs={"property": re.compile(r"^og:")}):
        prop = tag.get("property", "")
        content = tag.get("content", "")
        if prop and content:
            sd.og_tags[prop] = content

    # Twitter Cards
    for tag in soup.find_all("meta", attrs={"name": re.compile(r"^twitter:")}):
        name = tag.get("name", "")
        content = tag.get("content", "")
        if name and content:
            sd.twitter_tags[name] = content

    # JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = _json.loads(script.string or "")
            sd.json_ld.append(data)
        except (_json.JSONDecodeError, TypeError):
            pass

    # Canonical
    canon = soup.find("link", rel="canonical")
    if canon and canon.get("href"):
        sd.canonical = canon["href"]

    # RSS / Atom feed
    feed = soup.find("link", type=re.compile(r"application/(rss|atom)\+xml"))
    if feed and feed.get("href"):
        href = feed["href"]
        if href.startswith("/"):
            href = _base_url(url) + href
        sd.rss_feed = href

    # Language
    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        sd.lang = html_tag["lang"]

    log.info("extract_structured_data.done",
             og_tags=len(sd.og_tags),
             twitter_tags=len(sd.twitter_tags),
             json_ld=len(sd.json_ld),
             has_canonical=bool(sd.canonical),
             has_rss=bool(sd.rss_feed),
             lang=sd.lang)

    return sd


def fetch_pagespeed(url: str, timeout: int = 60) -> Optional[dict]:
    """Call Google PageSpeed Insights API (free, no key needed for low volume).

    Returns a compact dict with scores and key metrics, or None on failure.
    """
    api_url = (
        "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
        f"?url={url}"
        "&category=PERFORMANCE&category=ACCESSIBILITY"
        "&category=SEO&category=BEST_PRACTICES"
        "&strategy=MOBILE"
    )
    log.info("fetch_pagespeed.start", url=url)

    try:
        resp = httpx.get(api_url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("fetch_pagespeed.error", url=url, error=str(exc))
        return None

    try:
        categories = data.get("lighthouseResult", {}).get("categories", {})
        scores = {}
        for cat_id, cat_data in categories.items():
            scores[cat_data.get("title", cat_id)] = round(
                (cat_data.get("score") or 0) * 100
            )

        # Key metrics from audits
        audits = data.get("lighthouseResult", {}).get("audits", {})
        metrics = {}
        for key in [
            "first-contentful-paint", "largest-contentful-paint",
            "total-blocking-time", "cumulative-layout-shift",
            "speed-index",
        ]:
            audit = audits.get(key, {})
            if audit.get("displayValue"):
                metrics[key] = audit["displayValue"]

        # Collect failed audits (opportunities / diagnostics)
        failed_audits = []
        for audit_id, audit in audits.items():
            score = audit.get("score")
            if score is not None and score < 0.9 and audit.get("title"):
                failed_audits.append(
                    f"- {audit['title']}: {audit.get('displayValue', 'N/A')}"
                )

        result = {
            "scores": scores,
            "metrics": metrics,
            "failed_audits": failed_audits[:20],  # Top 20
        }

        log.info("fetch_pagespeed.done", scores=scores)
        return result

    except Exception as exc:
        log.warning("fetch_pagespeed.parse_error", error=str(exc))
        return None


# ---------------------------------------------------------------------------
# Full site report (aggregated)
# ---------------------------------------------------------------------------

def build_site_report(
    url: str,
    max_articles: int = 2,
    include_pagespeed: bool = True,
) -> SiteReport:
    """Build a comprehensive site report by combining all analysis methods.

    1. Fetch homepage
    2. Fetch sitemap.xml → pick random articles → fetch them
    3. Analyze HTTP headers
    4. Extract structured data (OG, Twitter, JSON-LD, RSS)
    5. Fetch robots.txt
    6. (Optional) Call PageSpeed Insights API
    """
    log.info("build_site_report.start", url=url, max_articles=max_articles)

    # 1. Homepage
    homepage = fetch_page(url)

    # 2. Sitemap + article sampling
    sitemap_urls = fetch_sitemap(url)
    articles: List[PageInfo] = []

    # Filter to article-like URLs (not homepage, not tag/category pages)
    base = _base_url(url)
    article_urls = [
        u for u in sitemap_urls
        if u != url
        and u != f"{url}/"
        and u.startswith(base)
        and "/tags/" not in u
        and "/categories/" not in u
        and "/page/" not in u
    ]

    if article_urls:
        sample = random.sample(article_urls, min(max_articles, len(article_urls)))
        for article_url in sample:
            try:
                article = fetch_page(article_url)
                articles.append(article)
            except Exception as exc:
                log.warning("build_site_report.article_fetch_error",
                            url=article_url, error=str(exc))

    log.info("build_site_report.articles_fetched",
             sitemap_total=len(sitemap_urls),
             article_candidates=len(article_urls),
             fetched=len(articles))

    # 3. HTTP headers
    headers = analyze_headers(url)

    # 4. Structured data (from homepage)
    structured = extract_structured_data(url)

    # 5. robots.txt
    robots = fetch_robots_txt(url)

    # 6. RSS check
    has_rss = bool(structured.rss_feed)
    if not has_rss:
        # Try common Hugo feed paths
        for feed_path in ["/index.xml", "/feed.xml", "/rss.xml"]:
            try:
                resp = httpx.head(f"{base}{feed_path}", headers=_HTTP_HEADERS,
                                  timeout=10, follow_redirects=True)
                if resp.status_code == 200:
                    has_rss = True
                    structured.rss_feed = f"{base}{feed_path}"
                    break
            except Exception:
                pass

    # 7. PageSpeed (optional, takes ~15-30s)
    pagespeed = None
    if include_pagespeed:
        pagespeed = fetch_pagespeed(url)

    report = SiteReport(
        homepage=homepage,
        articles=articles,
        sitemap_urls=sitemap_urls,
        structured_data=structured,
        headers=headers,
        robots_txt=robots,
        has_rss=has_rss,
        pagespeed=pagespeed,
    )

    log.info("build_site_report.done",
             homepage_words=homepage.word_count,
             articles=len(articles),
             sitemap_pages=len(sitemap_urls),
             has_og=bool(structured.og_tags),
             has_jsonld=bool(structured.json_ld),
             has_rss=has_rss,
             has_pagespeed=pagespeed is not None)

    return report


# ---------------------------------------------------------------------------
# Source code context (read from GitHub repos via REST API)
# ---------------------------------------------------------------------------

def _github_get_file(repo: str, path: str, ref: str = "main") -> str:
    """Fetch a single file from a GitHub repo via REST API.

    Returns the decoded text content, or empty string on failure.
    Uses GITHUB_TOKEN from environment.
    """
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return ""

    api_url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={ref}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.raw+json",  # raw content
    }

    try:
        resp = httpx.get(api_url, headers=headers, timeout=15, follow_redirects=True)
        if resp.status_code == 200:
            return resp.text[:10000]  # Cap at 10k chars
        log.debug("github_get_file.not_found",
                  repo=repo, path=path, status=resp.status_code)
        return ""
    except Exception as exc:
        log.debug("github_get_file.error", repo=repo, path=path, error=str(exc))
        return ""


def _github_list_dir(repo: str, path: str, ref: str = "main") -> List[dict]:
    """List files in a GitHub repo directory via REST API.

    Returns list of dicts with 'name', 'path', 'type' ('file'|'dir'), 'size'.
    """
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return []

    api_url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={ref}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }

    try:
        resp = httpx.get(api_url, headers=headers, timeout=15, follow_redirects=True)
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not isinstance(data, list):
            return []
        return [
            {"name": f["name"], "path": f["path"],
             "type": f["type"], "size": f.get("size", 0)}
            for f in data
        ]
    except Exception:
        return []


@dataclass
class SourceContext:
    """Key information extracted from source code repos."""
    hugo_config: str = ""          # Hugo site config (config.toml/yaml)
    sample_articles: List[str] = field(default_factory=list)  # 2-3 raw markdown files
    category_map: str = ""         # Category mapping from formatter
    translation_config: str = ""   # Translation prompts from config.yaml
    aggregator_config: str = ""    # Root config.yaml
    content_tree: str = ""         # Directory listing of content/


def fetch_source_context(
    blog_repo: str = "KlimDos/my-blog",
    aggregator_repo: str = "KlimDos/moto-news",
) -> SourceContext:
    """Fetch key source files from GitHub repos for analysis.

    Reads:
    - Hugo config from blog repo
    - 2-3 sample article files from blog repo
    - Category mapping from aggregator (formatter/markdown.go)
    - Translation config from aggregator (config.yaml)
    """
    log.info("fetch_source_context.start",
             blog_repo=blog_repo, aggregator_repo=aggregator_repo)

    ctx = SourceContext()

    # 1. Hugo config from blog repo
    for config_name in ["hugo.toml", "hugo.yaml", "config.toml", "config.yaml", "config/_default/hugo.toml"]:
        content = _github_get_file(blog_repo, config_name)
        if content:
            ctx.hugo_config = content
            log.info("fetch_source_context.hugo_config",
                     file=config_name, length=len(content))
            break

    if not ctx.hugo_config:
        log.info("fetch_source_context.no_hugo_config")

    # 2. Content tree from blog repo (to understand structure)
    tree = _github_list_dir(blog_repo, "content")
    if tree:
        tree_lines = []
        for item in tree:
            prefix = "d" if item["type"] == "dir" else "f"
            tree_lines.append(f"[{prefix}] {item['path']}")
            # If it's a dir like "posts", list its contents too
            if item["type"] == "dir":
                sub = _github_list_dir(blog_repo, item["path"])
                for s in sub[:10]:
                    sp = "d" if s["type"] == "dir" else "f"
                    tree_lines.append(f"  [{sp}] {s['path']}")
        ctx.content_tree = "\n".join(tree_lines)

    # 3. Sample articles from blog repo
    # Find recent articles by listing posts/ subdirectories
    posts_dirs = _github_list_dir(blog_repo, "content/posts")
    # Sort by name desc to get recent years first
    posts_dirs.sort(key=lambda d: d["name"], reverse=True)

    article_paths: List[str] = []
    for year_dir in posts_dirs[:2]:  # latest 2 years
        if year_dir["type"] != "dir":
            continue
        months = _github_list_dir(blog_repo, year_dir["path"])
        months.sort(key=lambda d: d["name"], reverse=True)
        for month_dir in months[:1]:  # latest month
            if month_dir["type"] != "dir":
                continue
            files = _github_list_dir(blog_repo, month_dir["path"])
            md_files = [f for f in files if f["name"].endswith(".md")]
            for f in md_files[:2]:  # 2 articles per month
                article_paths.append(f["path"])

    for path in article_paths[:3]:  # Max 3 articles total
        content = _github_get_file(blog_repo, path)
        if content:
            # Only include frontmatter + first 500 chars of body
            if "---" in content:
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    frontmatter = parts[1].strip()
                    body_preview = parts[2].strip()[:500]
                    ctx.sample_articles.append(
                        f"=== {path} ===\n---\n{frontmatter}\n---\n{body_preview}..."
                    )
                else:
                    ctx.sample_articles.append(f"=== {path} ===\n{content[:1500]}")
            else:
                ctx.sample_articles.append(f"=== {path} ===\n{content[:1500]}")

    log.info("fetch_source_context.articles", count=len(ctx.sample_articles))

    # 4. Category mapping from aggregator (formatter/markdown.go)
    formatter = _github_get_file(aggregator_repo, "internal/formatter/markdown.go")
    if formatter:
        ctx.category_map = formatter
        log.info("fetch_source_context.formatter", length=len(formatter))

    # 5. Aggregator config (translation prompts, RSS feeds)
    agg_config = _github_get_file(aggregator_repo, "config.yaml")
    if agg_config:
        ctx.aggregator_config = agg_config
        log.info("fetch_source_context.aggregator_config", length=len(agg_config))

    log.info("fetch_source_context.done",
             has_hugo_config=bool(ctx.hugo_config),
             articles=len(ctx.sample_articles),
             has_formatter=bool(ctx.category_map),
             has_agg_config=bool(ctx.aggregator_config),
             has_content_tree=bool(ctx.content_tree))

    return ctx


@tool
def get_site_snapshot(url: str = "https://blog.alimov.top") -> str:
    """
    Fetch a snapshot of the blog site for analysis.
    Returns page title, structure, content summary, and links.
    """
    log.info("tool.get_site_snapshot", url=url)
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
        log.info("tool.get_site_snapshot.done",
                 title=page.title[:60], word_count=page.word_count)
        return result

    except Exception as e:
        log.error("tool.get_site_snapshot.error", url=url, error=str(e))
        return f"Error fetching site: {str(e)}"


@tool
def get_page_content(url: str) -> str:
    """
    Fetch content from a specific page on the blog.
    Useful for analyzing individual articles or sections.
    """
    log.info("tool.get_page_content", url=url)
    try:
        page = fetch_page(url)
        log.info("tool.get_page_content.done",
                 url=url, word_count=page.word_count)
        return f"""=== Page: {page.url} ===
Title: {page.title}
Word Count: {page.word_count}

--- Content ---
{page.content[:4000]}
"""
    except Exception as e:
        log.error("tool.get_page_content.error", url=url, error=str(e))
        return f"Error fetching page: {str(e)}"


@tool
def analyze_site_structure(url: str = "https://blog.alimov.top") -> str:
    """
    Analyze the overall structure of the blog site.
    Returns information about navigation, categories, and organization.
    """
    log.info("tool.analyze_site_structure", url=url)
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

        log.info("tool.analyze_site_structure.done",
                 nav_items=len(nav_items), categories=len(categories),
                 links=len(page.links))

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
        log.error("tool.analyze_site_structure.error", url=url, error=str(e))
        return f"Error analyzing site: {str(e)}"
