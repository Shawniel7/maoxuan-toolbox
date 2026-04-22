/* =================================================================
   chat.js — 思想工具箱 · 深度对话模式

   Connects to backend POST /chat (SSE), streams the agent reply with
   smooth character-paced rendering, shows retrieved-article chips,
   and wires up 《文章名》 citation popovers + full-article modal.

   Zero build step, zero framework, zero external deps.
================================================================= */

(() => {
  "use strict";

  // ─── Config ──────────────────────────────────────────────────────
  // When served from localhost:8080 static server, hit backend on :8000.
  // When deployed behind a reverse proxy, backend is at same origin / /api.
  const BACKEND = (() => {
    const host = window.location.hostname;
    if (host === "localhost" || host === "127.0.0.1") {
      return "http://localhost:8000";
    }
    return ""; // same-origin in prod
  })();

  // Streaming render pacing. Tokens from Anthropic arrive in variable
  // chunk sizes (1-30 chars). We queue them and flush at a steady rate.
  const CHARS_PER_TICK = 3; // chars consumed per animation frame (~50/s)

  // ─── DOM refs ───────────────────────────────────────────────────
  const el = {
    stream: document.getElementById("chat-stream"),
    welcome: document.getElementById("chat-welcome"),
    form: document.getElementById("chat-form"),
    input: document.getElementById("chat-input"),
    send: document.getElementById("chat-send"),
    inputDock: document.getElementById("chat-input-dock"),
    popover: document.getElementById("citation-popover"),
    popoverBody: document.querySelector(".citation-popover-body"),
    popoverLink: document.querySelector(".citation-popover-link"),
    modal: document.getElementById("article-modal"),
    modalClose: document.getElementById("article-modal-close"),
    modalContent: document.getElementById("article-modal-content"),
  };

  // ─── State ──────────────────────────────────────────────────────
  // Conversation history: array of {role, content}. Lives in memory only.
  const history = [];
  // Retrieved chunks for the latest in-flight turn, and a cumulative
  // article index (chunk_id → chunk meta) used for popover lookups.
  let currentRetrieved = [];
  const chunkById = new Map();
  // Active streaming state
  let streaming = false;
  let streamBuffer = "";
  let streamFlushRAF = null;
  let currentBodyEl = null;
  let currentCursorEl = null;

  // ─── Helpers ────────────────────────────────────────────────────
  function scrollToBottom(smooth = true) {
    window.scrollTo({
      top: document.body.scrollHeight,
      behavior: smooth ? "smooth" : "auto",
    });
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // Minimal Markdown → HTML for agent output and article bodies.
  // Supports: # / ## / ### / #### headings, > blockquotes, **bold**,
  // simple [text](url) links, \[N] (footnote ref, rendered plain),
  // hr ---, tables (| col | col |), paragraphs separated by blank lines.
  function renderMarkdown(md) {
    const lines = md.replace(/\r\n/g, "\n").split("\n");
    const out = [];
    let i = 0;
    let inQuote = false;
    let inTable = false;
    let tableBuf = [];

    const closeQuote = () => { if (inQuote) { out.push("</blockquote>"); inQuote = false; } };
    const flushTable = () => {
      if (!inTable) return;
      if (tableBuf.length >= 2) {
        const rows = tableBuf.map((ln) =>
          ln.replace(/^\||\|$/g, "").split("|").map((c) => c.trim())
        );
        out.push("<table><thead><tr>");
        rows[0].forEach((c) => out.push(`<th>${inlineFmt(c)}</th>`));
        out.push("</tr></thead><tbody>");
        for (let r = 2; r < rows.length; r++) {
          out.push("<tr>");
          rows[r].forEach((c) => out.push(`<td>${inlineFmt(c)}</td>`));
          out.push("</tr>");
        }
        out.push("</tbody></table>");
      }
      inTable = false;
      tableBuf = [];
    };

    while (i < lines.length) {
      let ln = lines[i];
      const trimmed = ln.trim();

      // Blank line: paragraph break
      if (trimmed === "") {
        closeQuote();
        flushTable();
        i++;
        continue;
      }

      // Heading
      let mHead = trimmed.match(/^(#{1,4})\s+(.+?)\s*$/);
      if (mHead) {
        closeQuote();
        flushTable();
        const level = Math.min(mHead[1].length + 1, 4); // offset by 1 so chat h2 not huge
        out.push(`<h${level}>${inlineFmt(mHead[2])}</h${level}>`);
        i++;
        continue;
      }

      // Horizontal rule
      if (/^---+$/.test(trimmed)) {
        closeQuote();
        flushTable();
        out.push("<hr>");
        i++;
        continue;
      }

      // Table (consecutive | lines)
      if (/^\|.*\|$/.test(trimmed)) {
        flushTable();
        inTable = true;
        while (i < lines.length && /^\|.*\|$/.test(lines[i].trim())) {
          tableBuf.push(lines[i].trim());
          i++;
        }
        flushTable();
        continue;
      }

      // Blockquote
      if (/^>\s?/.test(trimmed)) {
        flushTable();
        if (!inQuote) { out.push("<blockquote>"); inQuote = true; }
        const content = trimmed.replace(/^>\s?/, "");
        if (content) out.push(`<p>${inlineFmt(content)}</p>`);
        i++;
        continue;
      } else {
        closeQuote();
      }

      // List item (simple, not nested)
      if (/^(\d+\.|\-|\*)\s+/.test(trimmed)) {
        flushTable();
        const ordered = /^\d+\./.test(trimmed);
        const tag = ordered ? "ol" : "ul";
        out.push(`<${tag}>`);
        while (i < lines.length && /^(\d+\.|\-|\*)\s+/.test(lines[i].trim())) {
          const item = lines[i].trim().replace(/^(\d+\.|\-|\*)\s+/, "");
          out.push(`<li>${inlineFmt(item)}</li>`);
          i++;
        }
        out.push(`</${tag}>`);
        continue;
      }

      // Default: paragraph. Collect consecutive non-blank lines.
      flushTable();
      let paraBuf = [trimmed];
      i++;
      while (i < lines.length && lines[i].trim() !== "" &&
             !/^#{1,4}\s+/.test(lines[i].trim()) &&
             !/^>\s?/.test(lines[i].trim()) &&
             !/^(\d+\.|\-|\*)\s+/.test(lines[i].trim()) &&
             !/^\|.*\|$/.test(lines[i].trim()) &&
             !/^---+$/.test(lines[i].trim())) {
        paraBuf.push(lines[i].trim());
        i++;
      }
      out.push(`<p>${inlineFmt(paraBuf.join(" "))}</p>`);
    }

    closeQuote();
    flushTable();
    return out.join("\n");
  }

  function inlineFmt(s) {
    // escape first, then re-introduce known patterns
    let t = escapeHtml(s);
    // bold **x** → <strong>
    t = t.replace(/\*\*([^*\n]+?)\*\*/g, "<strong>$1</strong>");
    // footnote-escaped refs like \[42] → [42]
    t = t.replace(/\\\[(\d+)\]/g, "[$1]");
    // [text](url) links
    t = t.replace(
      /\[([^\]]+)\]\(([^)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>'
    );
    // wrap 《title》 with span for citation popover (post-streaming processing
    // will enrich these with data-article-id if matched against retrieval)
    t = t.replace(/《([^》\n]+)》/g, '<span class="cite" data-title="$1">《$1》</span>');
    return t;
  }

  // ─── Message DOM construction ───────────────────────────────────
  function removeWelcome() {
    if (el.welcome) { el.welcome.remove(); }
  }

  function appendUserMessage(text) {
    removeWelcome();
    const turn = document.createElement("div");
    turn.className = "chat-turn chat-turn-user";
    turn.innerHTML = `
      <div class="chat-role">你</div>
      <div class="chat-body chat-body-user"></div>
    `;
    turn.querySelector(".chat-body").textContent = text;
    el.stream.appendChild(turn);
    scrollToBottom();
  }

  function appendAssistantStart() {
    const turn = document.createElement("div");
    turn.className = "chat-turn chat-turn-assistant";
    turn.innerHTML = `
      <div class="chat-role">分析</div>
      <div class="chat-sources" aria-label="查阅的篇目">
        <span class="chat-sources-label">查阅中…</span>
      </div>
      <div class="chat-body chat-body-assistant"></div>
    `;
    el.stream.appendChild(turn);
    currentBodyEl = turn.querySelector(".chat-body-assistant");
    const cursor = document.createElement("span");
    cursor.className = "chat-cursor";
    currentBodyEl.appendChild(cursor);
    currentCursorEl = cursor;
    return turn;
  }

  function updateSources(turnEl, retrieved) {
    // During streaming, show a dimmed "查阅中…" placeholder. After completion,
    // we replace it with cited-only chips (via finalizeSources).
    const box = turnEl.querySelector(".chat-sources");
    if (!box) return;
    box.innerHTML = '<span class="chat-sources-label">查阅中…</span>';
    // keep a cached map for post-streaming citation resolution
    retrieved.forEach((c) => chunkById.set(c.chunk_id, c));
  }

  function finalizeSources(turnEl, retrievedArticles, renderedText) {
    const box = turnEl.querySelector(".chat-sources");
    if (!box) return;
    // A unique article is "cited" if its title appears wrapped in 《...》
    // in the response text.
    const cited = [];
    const seen = new Set();
    retrievedArticles.forEach((ch) => {
      if (seen.has(ch.article_id)) return;
      const marker = `《${ch.article_title}》`;
      if (renderedText.includes(marker)) {
        cited.push(ch);
        seen.add(ch.article_id);
      }
    });

    if (cited.length === 0) {
      // Agent didn't cite anyone — hide the sources row entirely.
      box.remove();
      return;
    }
    box.innerHTML = '<span class="chat-sources-label">引用:</span> ';
    cited.forEach((ch) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chat-source-chip";
      chip.textContent = `《${ch.article_title}》`;
      chip.dataset.articleId = ch.article_id;
      chip.addEventListener("click", () => openArticleModal(ch.article_id));
      box.appendChild(chip);
    });
  }

  // ─── Streaming render with pacing ───────────────────────────────
  function scheduleFlush() {
    if (streamFlushRAF) return;
    streamFlushRAF = requestAnimationFrame(flushTick);
  }

  function flushTick() {
    streamFlushRAF = null;
    if (!currentBodyEl) return;
    if (streamBuffer.length === 0) {
      return;
    }
    // Consume up to CHARS_PER_TICK chars from buffer and append as raw text.
    const take = streamBuffer.slice(0, CHARS_PER_TICK);
    streamBuffer = streamBuffer.slice(CHARS_PER_TICK);
    // During streaming we append plain text (preserving newlines) to keep
    // rendering cheap. Markdown transformation happens at the end.
    const textNode = document.createTextNode(take);
    currentBodyEl.insertBefore(textNode, currentCursorEl);

    if (streamBuffer.length > 0) {
      scheduleFlush();
    }
  }

  function appendDelta(text) {
    streamBuffer += text;
    scheduleFlush();
  }

  async function drainBuffer() {
    // Force-flush remaining buffered text with no pacing (end of stream).
    if (!currentBodyEl) return;
    if (streamFlushRAF) {
      cancelAnimationFrame(streamFlushRAF);
      streamFlushRAF = null;
    }
    if (streamBuffer.length > 0) {
      const textNode = document.createTextNode(streamBuffer);
      currentBodyEl.insertBefore(textNode, currentCursorEl);
      streamBuffer = "";
    }
  }

  function finalizeMessage(turnEl) {
    // Convert the accumulated plain text to rendered markdown with
    // clickable 《...》 citations, then wire up popovers.
    if (currentCursorEl) { currentCursorEl.remove(); currentCursorEl = null; }
    const raw = currentBodyEl.textContent || "";
    currentBodyEl.innerHTML = renderMarkdown(raw);
    wireCitations(currentBodyEl);
    finalizeSources(turnEl, currentRetrieved, raw);
    currentBodyEl = null;
    scrollToBottom();
    return raw;
  }

  // ─── Citation popover wiring ────────────────────────────────────
  function wireCitations(root) {
    // Map article_title → [chunks]
    const byTitle = new Map();
    currentRetrieved.forEach((ch) => {
      if (!byTitle.has(ch.article_title)) byTitle.set(ch.article_title, []);
      byTitle.get(ch.article_title).push(ch);
    });
    root.querySelectorAll("span.cite").forEach((span) => {
      const title = span.dataset.title;
      const chunks = byTitle.get(title);
      if (!chunks || chunks.length === 0) {
        // Not in retrieval set — make it plain text (no popover)
        span.classList.remove("cite");
        span.classList.add("cite-plain");
        return;
      }
      span.classList.add("cite-active");
      span.dataset.articleId = chunks[0].article_id;
      span.addEventListener("click", (e) => {
        e.stopPropagation();
        showCitationPopover(span, chunks);
      });
    });
  }

  function showCitationPopover(anchor, chunks) {
    // Populate
    const preview = chunks
      .slice(0, 2)
      .map((c) => {
        const txt = (c.preview || c.text || "").slice(0, 200);
        const section = c.section_title ? ` · ${escapeHtml(c.section_title)}` : "";
        return `<div class="citation-passage"><div class="citation-section">${escapeHtml(c.article_title)}${section}</div><div class="citation-text">${escapeHtml(txt)}${txt.length >= 200 ? "…" : ""}</div></div>`;
      })
      .join("");
    el.popoverBody.innerHTML = preview;
    el.popoverLink.textContent = `→ 查看全文《${chunks[0].article_title}》`;
    el.popoverLink.onclick = (e) => {
      e.preventDefault();
      hidePopover();
      openArticleModal(chunks[0].article_id);
    };

    // Position near anchor
    const r = anchor.getBoundingClientRect();
    const popRect = el.popover.getBoundingClientRect();
    el.popover.classList.remove("hidden");
    // Re-measure after visible
    const pw = el.popover.offsetWidth;
    const ph = el.popover.offsetHeight;
    const viewportW = window.innerWidth;
    let left = r.left + window.scrollX;
    let top = r.bottom + window.scrollY + 8;
    if (left + pw > viewportW - 16) left = viewportW - pw - 16;
    if (left < 16) left = 16;
    // If below would fall below viewport, put above
    if (r.bottom + ph + 16 > window.innerHeight && r.top - ph - 8 > 0) {
      top = r.top + window.scrollY - ph - 8;
    }
    el.popover.style.left = `${left}px`;
    el.popover.style.top = `${top}px`;
  }

  function hidePopover() {
    el.popover.classList.add("hidden");
  }

  document.addEventListener("click", (e) => {
    if (!el.popover.contains(e.target)) hidePopover();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { hidePopover(); closeArticleModal(); }
  });

  // ─── Article modal ──────────────────────────────────────────────
  async function openArticleModal(articleId) {
    el.modal.classList.remove("hidden");
    el.modalContent.innerHTML = '<div class="loading">加载中…</div>';
    document.body.classList.add("modal-open");
    try {
      const r = await fetch(`${BACKEND}/article/${encodeURIComponent(articleId)}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      const body = data.markdown_body || "";
      const meta = [
        `<h1 class="article-title">${escapeHtml(data.title)}</h1>`,
        `<p class="article-meta">${data.year}.${String(data.month || 0).padStart(2, "0")} · 《毛泽东选集》第 ${data.volume} 卷 · <a href="${data.source_url}" target="_blank" rel="noopener">marxists.org 原文</a></p>`,
      ].join("");
      el.modalContent.innerHTML = meta + renderMarkdown(body);
      el.modalContent.scrollTop = 0;
    } catch (e) {
      el.modalContent.innerHTML = `<div class="empty-state">加载失败:${escapeHtml(String(e))}</div>`;
    }
  }

  function closeArticleModal() {
    el.modal.classList.add("hidden");
    document.body.classList.remove("modal-open");
  }

  el.modalClose.addEventListener("click", closeArticleModal);
  el.modal.addEventListener("click", (e) => {
    if (e.target === el.modal) closeArticleModal();
  });

  // ─── Send / stream flow ─────────────────────────────────────────
  async function sendMessage(text) {
    if (streaming || !text.trim()) return;
    streaming = true;
    el.send.disabled = true;
    el.send.textContent = "…";
    currentRetrieved = [];

    const userMsg = text.trim();
    history.push({ role: "user", content: userMsg });
    appendUserMessage(userMsg);

    const turnEl = appendAssistantStart();
    scrollToBottom();

    let assistantText = "";

    try {
      const resp = await fetch(`${BACKEND}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: history }),
      });
      if (!resp.ok || !resp.body) {
        throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let sseBuffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        sseBuffer += decoder.decode(value, { stream: true });
        // SSE events are separated by \n\n; each starts with "data: "
        let idx;
        while ((idx = sseBuffer.indexOf("\n\n")) !== -1) {
          const rawEvt = sseBuffer.slice(0, idx);
          sseBuffer = sseBuffer.slice(idx + 2);
          const line = rawEvt.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          const payload = line.slice(6);
          let ev;
          try { ev = JSON.parse(payload); } catch { continue; }

          if (ev.type === "retrieved") {
            currentRetrieved = ev.chunks || [];
            updateSources(turnEl, currentRetrieved);
          } else if (ev.type === "text_delta") {
            appendDelta(ev.delta || "");
          } else if (ev.type === "error") {
            appendDelta(`\n\n⚠ 出错了：${ev.message}`);
          } else if (ev.type === "done") {
            // consumed by loop end
          }
        }
      }
      await drainBuffer();
      assistantText = finalizeMessage(turnEl);
    } catch (e) {
      appendDelta(`\n\n⚠ 网络错误：${String(e)}`);
      await drainBuffer();
      assistantText = finalizeMessage(turnEl);
    } finally {
      streaming = false;
      el.send.disabled = false;
      el.send.textContent = "发送";
      if (assistantText) {
        history.push({ role: "assistant", content: assistantText });
      }
      scrollToBottom();
    }
  }

  // ─── Event wiring ───────────────────────────────────────────────
  el.form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = el.input.value.trim();
    if (!text) return;
    el.input.value = "";
    autoResize();
    sendMessage(text);
  });

  el.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      el.form.requestSubmit();
    }
  });

  // Auto-resize textarea up to 6 rows
  function autoResize() {
    el.input.style.height = "auto";
    const max = parseFloat(getComputedStyle(el.input).lineHeight) * 6;
    el.input.style.height = Math.min(el.input.scrollHeight, max) + "px";
  }
  el.input.addEventListener("input", autoResize);

  // Example chips fill the input
  document.addEventListener("click", (e) => {
    const chip = e.target.closest(".example-chip[data-fill]");
    if (!chip) return;
    el.input.value = chip.dataset.fill;
    autoResize();
    el.input.focus();
  });
})();
