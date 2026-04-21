"""HTML → clean markdown with YAML frontmatter.

marxists.org's Mao pages vary in structure (different decades, different
contributors). The parser must be tolerant: the smoke test in Step 3 exists
specifically to tune these rules on 5 diverse articles before the full crawl.

Public API:
    html_to_markdown(html: str, article: dict) -> str
"""
from __future__ import annotations

from bs4 import BeautifulSoup


# Selectors for junk we always remove, regardless of template.
JUNK_SELECTORS = (
    "nav", "footer", "script", "style", "noscript",
    ".ad", ".banner", ".navbar", ".breadcrumbs",
    "#header", "#footer", "#navigation",
)


def html_to_markdown(html: str, article: dict) -> str:
    """Strip junk, pick the content container, render to markdown, prepend frontmatter.

    Args:
        html: raw HTML from the crawler
        article: manifest entry for this article (provides id, title, year, etc.)

    Returns:
        markdown string ready to be written to corpus/raw/volN/<id>.md
    """
    raise NotImplementedError(
        "step-3: implement cleaning. Tune on 5 diverse articles in the smoke test "
        "before the full 159-article run."
    )


def _build_frontmatter(article: dict) -> str:
    """YAML frontmatter block. Order matters: id, title, year, volume, source_url, category."""
    lines = ["---"]
    lines.append(f"id: {article['id']}")
    lines.append(f"title: {article['title']}")
    lines.append(f"year: {article['year']}")
    if article.get("month"):
        lines.append(f"month: {article['month']}")
    if article.get("volume"):
        lines.append(f"volume: {article['volume']}")
    lines.append(f"source_url: {article.get('url_primary') or ''}")
    if article.get("category"):
        lines.append(f"category: {article['category']}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _strip_junk(soup: BeautifulSoup) -> None:
    """Remove nav/footer/script/ads in-place."""
    for sel in JUNK_SELECTORS:
        for tag in soup.select(sel):
            tag.decompose()
