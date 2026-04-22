"""Slice cleaned markdown into overlapping chunks with section + footnote awareness.

Strategy:
    1. Strip frontmatter and the leading horizontal-rule + title/date/editor-note prelude.
    2. Detect footnote section via `**注释**` marker; split body into (main, footnotes).
    3. Main text: split by heading lines (### or ####). Each chunk carries the most-recent
       heading as `section_title`; chunks before any heading carry section_title=None.
    4. Within a section: pack sentences into chunks of target 450 chars (range 300-600).
       Each new chunk begins with the last ~80 chars (sentence-boundary aligned) of the
       previous chunk — overlap is within-section only, never across section boundaries.
    5. Footnotes: each `\\[N]` entry becomes its own chunk with is_footnote=true,
       footnote_ref=N, section_title="注释". Not split even if long — atomic for 1:1
       mapping with reference markers.

Output: corpus/chunks.jsonl (one JSON object per line) + corpus/chunk_stats.json
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path

from backend.ingest import manifest as mf

CORPUS = Path(__file__).resolve().parents[2] / "corpus"
CORPUS_RAW = CORPUS / "raw"
CHUNKS_JSONL = CORPUS / "chunks.jsonl"
CHUNK_STATS = CORPUS / "chunk_stats.json"

MIN_CHARS = 300
TARGET_CHARS = 450
MAX_CHARS = 900
OVERLAP_CHARS = 80

# Sentence-end characters (Chinese + ASCII). Used for soft-splitting within oversized
# paragraphs AND for choosing overlap boundaries.
SENTENCE_ENDERS = "。！？.!?"

HEADING_RE = re.compile(r"^(#{2,4})\s+(.+?)\s*$")
FOOTNOTE_START_RE = re.compile(r"^\\\[(\d+)\]\s*(.*)$")  # marxists.org pattern: \[1] text


@dataclass
class ChunkRecord:
    chunk_id: str
    text: str
    char_count: int
    article_id: str
    article_title: str
    volume: int
    year: int
    month: int | None
    stable_slug: str
    section_title: str | None
    is_footnote: bool
    footnote_ref: str | None
    char_offset_start: int
    char_offset_end: int
    source_url: str
    upstream_provider: str


# ───────────────────────── frontmatter / body split ─────────────────────────

def _strip_frontmatter(text: str) -> str:
    """Remove the YAML frontmatter block and the leading horizontal-rule prelude marker.

    After this function returns, position 0 of the string is the title line (or the
    first content line) of the article. Offset accounting downstream uses this
    frontmatter-stripped body as the coordinate origin.
    """
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    body = text[end + 4:].lstrip("\n")
    # Leading horizontal-rule (marxists.org style: blank line then '---' then content)
    if body.startswith("---\n"):
        body = body[4:].lstrip("\n")
    return body


def _split_main_and_footnotes(body: str) -> tuple[str, str]:
    """Split body at the `**注释**` boundary. Returns (main, footnotes_text).
    If no footnote section found, footnotes is "".

    The boundary is typically: `\n---\n\n**注释**\n\n` — we accept variations.
    """
    # Locate **注释** on its own line (or at start of line)
    m = re.search(r"(\n---\n+)?\s*\*\*注释\*\*\s*\n", body)
    if not m:
        return body, ""
    main = body[: m.start()].rstrip() + "\n"
    # Everything after the "**注释**" header line is the footnote block
    footnotes = body[m.end():].lstrip("\n")
    return main, footnotes


# ───────────────────────── main-text chunking ─────────────────────────

def _split_sections(main: str) -> list[tuple[str | None, str, int]]:
    """Parse headings and return list of (section_title, section_text, start_offset_in_main).
    First section may have section_title=None (prelude before any heading).
    """
    sections: list[tuple[str | None, list[str], int]] = []
    current_title: str | None = None
    current_lines: list[str] = []
    current_start_pos = 0
    pos = 0

    lines = main.splitlines(keepends=True)
    for ln in lines:
        m = HEADING_RE.match(ln.rstrip("\n"))
        if m:
            # Flush current
            if current_lines:
                sections.append((current_title, "".join(current_lines), current_start_pos))
            current_title = m.group(2).strip()
            current_lines = []
            current_start_pos = pos + len(ln)  # section body starts after the heading line
        else:
            current_lines.append(ln)
        pos += len(ln)

    if current_lines:
        sections.append((current_title, "".join(current_lines), current_start_pos))

    # Convert to tuple form
    return [(t, txt.strip("\n"), off) for t, txt, off in sections if txt.strip()]


def _split_sentences(text: str) -> list[tuple[str, int]]:
    """Split text into sentence spans. Returns list of (sentence_text, start_offset).
    Sentence boundary = any char in SENTENCE_ENDERS, immediately followed by optional
    closing quotes/brackets, then whitespace/newline.
    """
    out: list[tuple[str, int]] = []
    i = 0
    n = len(text)
    start = 0
    while i < n:
        ch = text[i]
        if ch in SENTENCE_ENDERS:
            # Absorb trailing closing quotes/brackets
            j = i + 1
            while j < n and text[j] in "”’」』）)〕]】":
                j += 1
            seg = text[start:j]
            stripped = seg.strip()
            if stripped:
                # Report start offset of non-whitespace content
                leading_ws = len(seg) - len(seg.lstrip())
                out.append((stripped, start + leading_ws))
            start = j
            i = j
        else:
            i += 1
    # trailing fragment (no terminator)
    if start < n:
        seg = text[start:]
        stripped = seg.strip()
        if stripped:
            leading_ws = len(seg) - len(seg.lstrip())
            out.append((stripped, start + leading_ws))
    return out


def _pack_section(
    section_text: str,
    section_start_in_main: int,
) -> list[tuple[str, int, int]]:
    """Greedy-pack sentences into chunks targeting 450 chars, with within-section overlap.

    Returns list of (chunk_text, start_offset_in_main, end_offset_in_main).
    """
    sentences = _split_sentences(section_text)
    if not sentences:
        return []

    chunks: list[tuple[str, int, int]] = []

    # Build chunks greedily
    buf: list[tuple[str, int]] = []  # list of (sentence, offset_in_section)
    buf_chars = 0

    def emit():
        nonlocal buf, buf_chars
        if not buf:
            return
        txt = "".join(s[0] for s in buf)
        # Offsets relative to main
        first_off = section_start_in_main + buf[0][1]
        last = buf[-1]
        last_off = section_start_in_main + last[1] + len(last[0])
        chunks.append((txt, first_off, last_off))
        buf = []
        buf_chars = 0

    for sent, off in sentences:
        slen = len(sent)
        if buf_chars + slen > MAX_CHARS and buf_chars >= MIN_CHARS:
            emit()
            # Build overlap from tail of prior chunk
            # Reconstruct last chunk's sentences to find overlap
            if chunks:
                last_txt, _, _ = chunks[-1]
                overlap_sentences = _pick_overlap_sentences(last_txt, target=OVERLAP_CHARS)
                if overlap_sentences:
                    # Recompute offsets for overlap sentences within the section_text
                    # by re-running split on the overlap region — easier: just include
                    # the final sentences with their original offsets if we had them.
                    # Since we've already emitted, we take overlap from the string.
                    # For simplicity we store overlap as a prefix-string with zero
                    # offset contribution (retrieval semantics dominate offset precision).
                    overlap_joined = "".join(overlap_sentences)
                    # Use offset of current sentence start minus overlap length (capped at 0)
                    ov_off = max(0, off - len(overlap_joined))
                    buf.append((overlap_joined, ov_off))
                    buf_chars = len(overlap_joined)

        # Handle oversize single sentence (rare — still emit as its own chunk)
        if slen > MAX_CHARS and not buf:
            chunks.append((sent, section_start_in_main + off, section_start_in_main + off + slen))
            continue

        buf.append((sent, off))
        buf_chars += slen

        if buf_chars >= TARGET_CHARS:
            # Check if we should emit now or keep packing toward the upper bound
            if buf_chars >= MIN_CHARS:
                emit()
                # Seed next buf with overlap from this just-emitted chunk
                if chunks:
                    last_txt, _, _ = chunks[-1]
                    overlap_sentences = _pick_overlap_sentences(last_txt, target=OVERLAP_CHARS)
                    overlap_joined = "".join(overlap_sentences)
                    if overlap_joined:
                        # Next buf starts empty; prepend overlap at next sentence add
                        # Store overlap as a pseudo-sentence at offset off (start of next real content)
                        buf.append((overlap_joined, off + slen - len(overlap_joined) if off + slen >= len(overlap_joined) else 0))
                        buf_chars = len(overlap_joined)

    emit()
    return chunks


def _pick_overlap_sentences(chunk_text: str, target: int) -> list[str]:
    """Return trailing sentences from chunk_text totaling ~target chars."""
    sentences = _split_sentences(chunk_text)
    if not sentences:
        return []
    out: list[str] = []
    total = 0
    for sent, _ in reversed(sentences):
        out.insert(0, sent)
        total += len(sent)
        if total >= target:
            break
    return out


# ───────────────────────── footnote chunking ─────────────────────────

def _split_footnotes(footnotes_text: str, footnotes_start_in_body: int) -> list[tuple[str, int, int, str]]:
    """Split the footnote block into individual entries.

    Returns list of (text, start_offset_in_body, end_offset_in_body, footnote_ref).
    """
    out: list[tuple[str, int, int, str]] = []
    # Find all \[N] markers; a footnote entry runs until the next one (or EOF)
    marker_re = re.compile(r"\\\[(\d+)\]")
    matches = list(marker_re.finditer(footnotes_text))
    for i, m in enumerate(matches):
        ref = f"[{m.group(1)}]"
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(footnotes_text)
        seg = footnotes_text[start:end].strip()
        abs_start = footnotes_start_in_body + start
        abs_end = footnotes_start_in_body + end
        out.append((seg, abs_start, abs_end, ref))
    return out


# ───────────────────────── article driver ─────────────────────────

def chunk_article(markdown_text: str, manifest_entry: dict, volume: int) -> list[ChunkRecord]:
    """Chunk a single article. Pure function — no IO."""
    body = _strip_frontmatter(markdown_text)
    main, footnotes = _split_main_and_footnotes(body)
    footnotes_offset_in_body = len(main) + body[len(main):].find(footnotes) if footnotes else 0

    article_id = manifest_entry["id"]
    article_title = manifest_entry["title"]
    stable_slug = manifest_entry["stable_slug"]
    source_url = manifest_entry.get("url_primary", "")
    upstream_provider = manifest_entry.get("provider", "marxist.org")
    year = manifest_entry["year"]
    month = manifest_entry.get("month")

    records: list[ChunkRecord] = []
    seq = 0

    # Main-text chunks
    sections = _split_sections(main)
    for section_title, section_text, section_start in sections:
        section_chunks = _pack_section(section_text, section_start)
        for chunk_text, start_off, end_off in section_chunks:
            seq += 1
            records.append(ChunkRecord(
                chunk_id=f"{article_id}-{seq:04d}",
                text=chunk_text,
                char_count=len(chunk_text),
                article_id=article_id,
                article_title=article_title,
                volume=volume,
                year=year,
                month=month,
                stable_slug=stable_slug,
                section_title=section_title,
                is_footnote=False,
                footnote_ref=None,
                char_offset_start=start_off,
                char_offset_end=end_off,
                source_url=source_url,
                upstream_provider=upstream_provider,
            ))

    # Footnote chunks
    if footnotes:
        for fn_text, fn_start, fn_end, fn_ref in _split_footnotes(footnotes, footnotes_offset_in_body):
            seq += 1
            records.append(ChunkRecord(
                chunk_id=f"{article_id}-{seq:04d}",
                text=fn_text,
                char_count=len(fn_text),
                article_id=article_id,
                article_title=article_title,
                volume=volume,
                year=year,
                month=month,
                stable_slug=stable_slug,
                section_title="注释",
                is_footnote=True,
                footnote_ref=fn_ref,
                char_offset_start=fn_start,
                char_offset_end=fn_end,
                source_url=source_url,
                upstream_provider=upstream_provider,
            ))

    return records


def chunk_all() -> int:
    """Walk manifest, chunk every downloaded article, write chunks.jsonl. Returns count."""
    manifest = mf.load()
    n_chunks = 0
    per_article: dict[str, int] = {}
    footnote_count = 0
    length_buckets = [0] * 10  # 0-100, 100-200, ..., 900+
    sections_seen: dict[str, int] = {}

    with open(CHUNKS_JSONL, "w", encoding="utf-8") as out:
        for vol_obj, article in mf.iter_articles(manifest):
            if article["status"] != "downloaded":
                continue
            volume = vol_obj["volume"]
            md_path = CORPUS_RAW / f"vol{volume}" / f"{article['id']}-{article['stable_slug']}.md"
            if not md_path.exists():
                continue
            text = md_path.read_text(encoding="utf-8")
            records = chunk_article(text, article, volume)
            for rec in records:
                out.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
                n_chunks += 1
                if rec.is_footnote:
                    footnote_count += 1
                bucket = min(rec.char_count // 100, 9)
                length_buckets[bucket] += 1
                if rec.section_title:
                    sections_seen[rec.section_title] = sections_seen.get(rec.section_title, 0) + 1
            per_article[article["id"]] = len(records)

    stats = {
        "total_chunks": n_chunks,
        "footnote_chunks": footnote_count,
        "non_footnote_chunks": n_chunks - footnote_count,
        "articles_processed": len(per_article),
        "per_article_count_stats": {
            "min": min(per_article.values()) if per_article else 0,
            "max": max(per_article.values()) if per_article else 0,
            "mean": sum(per_article.values()) / len(per_article) if per_article else 0,
        },
        "length_histogram": {
            f"{i*100}-{(i+1)*100 if i < 9 else 'inf'}": length_buckets[i] for i in range(10)
        },
        "unique_section_titles": len(sections_seen),
    }
    with open(CHUNK_STATS, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    return n_chunks


def main() -> None:
    n = chunk_all()
    print(f"[chunker] wrote {n} chunks to {CHUNKS_JSONL}")
    print(f"[chunker] stats at {CHUNK_STATS}")


if __name__ == "__main__":
    main()
