"""HTML → clean markdown with YAML frontmatter.

marxists.org's Mao pages vary in structure (different decades, different
contributors). The parser is tolerant: the smoke test in Step 3 exists
specifically to tune these rules on 5 diverse articles before the full crawl.

Public API:
    html_to_markdown(html: str, article: dict, volume: int) -> (markdown: str, warnings: list[str])
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, Comment
from markdownify import markdownify as md

# Selectors for junk we always remove, regardless of template.
# marxists.org uses both modern (nav/footer) and classic-era elements.
JUNK_SELECTORS = (
    "nav", "footer", "script", "style", "noscript", "iframe",
    "form", "button", "input",
    ".ad", ".banner", ".navbar", ".breadcrumbs",
    "#header", "#footer", "#navigation",
)

# Common marxists.org boilerplate phrases that wrap article bodies.
# When detected, we take the text BEFORE the phrase (for trailing) or AFTER (for leading).
TRAILING_BOILERPLATE_MARKERS = [
    "中文马克思主义文库",
    "马克思主义文库",
    "返回主页",
    "Marxists Internet Archive",
    "back to Mao Zedong",
    "上一篇",
    "下一篇",
]
LEADING_BOILERPLATE_MARKERS = [
    "中文马克思主义文库",
    "毛泽东参考原著",
    "毛泽东文献",
]


def html_to_markdown(
    html: str,
    article: dict,
    volume: int,
    trim_log: list[dict] | None = None,
) -> tuple[str, list[str]]:
    """Strip junk, extract body, render to markdown, prepend frontmatter.

    Returns (markdown, warnings).
    warnings is a list of non-fatal issues the crawler should report for review
    (e.g., "body looks unusually short", "unexpected table found").

    If trim_log is provided, every candidate trailing-boilerplate match (whether
    trimmed or skipped) is appended as a dict with decision + context.
    """
    warnings: list[str] = []
    soup = BeautifulSoup(html, "lxml")

    # Strip HTML comments
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()

    # Strip junk tags
    for sel in JUNK_SELECTORS:
        for tag in soup.select(sel):
            tag.decompose()

    # Pick the largest meaningful content container. marxists.org pages typically
    # put the body directly inside <body>, sometimes wrapped in a single <div>.
    container = soup.body or soup
    text_all = container.get_text("", strip=True)
    if len(text_all) < 200:
        warnings.append(f"body text is unusually short ({len(text_all)} chars) — may have over-stripped")

    # Flag: if any <table> survived, report (tables may carry data that needs special handling)
    tables = container.find_all("table")
    if tables:
        warnings.append(f"found {len(tables)} <table> element(s); will render as markdown tables")

    # Convert to markdown. markdownify handles headings, bold, italics, links, lists, tables.
    raw_md = md(
        str(container),
        heading_style="ATX",       # # ## ###
        strip=["a"],                # strip anchor wrappers but keep text; we lose URLs but body is the point
        bullets="-",
    )

    # Post-process: collapse excessive blank lines, trim each line
    lines = [ln.rstrip() for ln in raw_md.splitlines()]
    # collapse >2 consecutive blanks to 1
    cleaned_lines: list[str] = []
    blank_run = 0
    for ln in lines:
        if ln == "":
            blank_run += 1
            if blank_run <= 1:
                cleaned_lines.append("")
        else:
            blank_run = 0
            cleaned_lines.append(ln)
    body = "\n".join(cleaned_lines).strip()

    # Leading first, trailing second — so a position-0 breadcrumb can't be misread as tail junk.
    body = _trim_leading_boilerplate(body, warnings)
    body = _trim_trailing_boilerplate(body, warnings, trim_log)

    # Final length check
    if len(body) < 500:
        warnings.append(f"final body length {len(body)} chars — review for over-strip")

    # Terminal-char check: flag if body doesn't end in an expected terminator. Picks up
    # silent truncation where the parser cut mid-sentence.
    #
    # Valid terminators:
    #   Sentence:        。 . ！ ? ！ ？
    #   Closing brackets: ) ) 」 』 〉 》 〕 ] 】
    #   Closing quotes:   " " ' ' (U+201D / U+2019)  — legitimate when a footnote or
    #                     block-quote ends with a quoted line like: 。"
    last = body.rstrip()
    if last:
        tail = last[-1]
        terminators = "。.！？!?」）)』〉》〕]】”’”’"
        if tail not in terminators:
            warnings.append(f"body ends with {tail!r} — does not look like a sentence/footnote terminator")

    frontmatter = _build_frontmatter(article, volume)
    return frontmatter + body + "\n", warnings


def _build_frontmatter(article: dict, volume: int) -> str:
    lines = ["---"]
    lines.append(f"id: {article['id']}")
    lines.append(f"stable_slug: {article['stable_slug']}")
    lines.append(f'title: "{article["title"]}"')
    lines.append(f"year: {article['year']}")
    if article.get("month") is not None:
        lines.append(f"month: {article['month']}")
    lines.append(f"volume: {volume}")
    if article.get("category"):
        lines.append(f'category: "{article["category"]}"')
    lines.append(f"source_url: {article.get('url_primary') or ''}")
    lines.append(f"provider: {article.get('provider') or 'marxist.org'}")
    lines.append("---")
    lines.append("")
    lines.append("")
    return "\n".join(lines)


def _trim_trailing_boilerplate(body: str, warnings: list[str], trim_log: list[dict] | None = None) -> str:
    """Look for a known trailing marker and cut at it — but only if it's really boilerplate.

    Rules (A + B + length guard):
      (B) Context-aware: the marker must be preceded by a line boundary (newline or BOS).
          Inline occurrences like 〔注：…——中文马克思主义文库〕 are annotations, not boilerplate.
      (A) Tail-proximity: cut must be within the last 500 chars of the body. Real trailing
          footers are short ("返回·上一篇·下一篇·文库"). Anything further in is almost
          certainly article content.
      (L) Length guard: skip trailing trim entirely on docs with body_len ≤ 2000. Short
          pieces (e.g., 为人民服务 at ~1200 chars) have no room for real trailing boilerplate
          after their content + footnotes — so any match is a false positive.

    Every candidate match is logged to trim_log with decision + ±30 char context,
    for aggregate audit across many articles.
    """
    if trim_log is None:
        trim_log = []

    cut_at = None
    cut_marker = None
    body_len = len(body)

    # Length guard: short docs have no trailing boilerplate worth cutting.
    if body_len <= 2000:
        return body

    for marker in TRAILING_BOILERPLATE_MARKERS:
        start = 0
        while True:
            idx = body.find(marker, start)
            if idx == -1:
                break
            # Context ±30 chars for logging
            ctx_start = max(0, idx - 30)
            ctx_end = min(body_len, idx + len(marker) + 30)
            ctx = body[ctx_start:ctx_end].replace("\n", "↵")

            # Rule B: preceding char must be newline or BOS (or whitespace after a newline)
            before = body[:idx]
            # Last non-space char before idx — we allow indentation
            prev = before.rstrip(" \t")
            context_ok = (prev == "") or prev.endswith("\n")

            # Rule A: must be within 500 chars of the tail
            tail_distance = body_len - idx
            near_tail = tail_distance < 500

            if context_ok and near_tail:
                decision = "TRIM"
                if cut_at is None or idx < cut_at:
                    cut_at = idx
                    cut_marker = marker
            elif context_ok and not near_tail:
                decision = "SKIP_NOT_NEAR_TAIL"
            elif not context_ok and near_tail:
                decision = "SKIP_INLINE_CONTEXT"
            else:
                decision = "SKIP_BOTH"

            trim_log.append({
                "marker": marker,
                "position": idx,
                "body_length": body_len,
                "tail_distance": tail_distance,
                "context": ctx,
                "decision": decision,
            })
            start = idx + 1

    if cut_at is not None:
        trimmed_amount = body_len - cut_at
        warnings.append(f"trimmed {trimmed_amount} trailing chars at {cut_marker!r} (pos {cut_at}/{body_len})")
        return body[:cut_at].rstrip()
    return body


def _trim_leading_boilerplate(body: str, warnings: list[str]) -> str:
    """If the body starts with known nav text, skip past it."""
    head = body[:500]
    for marker in LEADING_BOILERPLATE_MARKERS:
        idx = head.find(marker)
        if idx != -1:
            # Skip past this marker's line
            nl = body.find("\n", idx)
            if nl > 0:
                cut = nl + 1
                warnings.append(f"trimmed {cut} leading chars at nav marker")
                return body[cut:].lstrip()
    return body
