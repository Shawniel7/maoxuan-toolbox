/* 思想工具箱 · renderer.js
 *
 * 把一个 entry 渲染成一张结果卡(DOM 节点)。
 * 不依赖任何框架。所有用户可见文本都用 textContent 写入,
 * 避免 XSS。URL 通过构造 <a> 节点插入。
 */

(function () {
  'use strict';

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function section(label, bodyNode) {
    const wrap = el('div', 'section');
    wrap.appendChild(el('div', 'section-label', label));
    wrap.appendChild(bodyNode);
    return wrap;
  }

  function renderExcerpts(excerpts) {
    const wrap = document.createElement('div');
    (excerpts || []).forEach(ex => {
      const block = el('div', 'excerpt');
      block.appendChild(document.createTextNode('「' + ex.text + '」'));
      const cite = el('span', 'excerpt-cite');
      cite.appendChild(document.createTextNode('—— ' + ex.source + '  '));
      if (ex.url) {
        const a = document.createElement('a');
        a.href = ex.url;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        a.textContent = '[查看全文]';
        cite.appendChild(a);
      }
      block.appendChild(cite);
      wrap.appendChild(block);
    });
    return wrap;
  }

  function renderActionList(entryId, actions) {
    const list = el('ul', 'action-list');
    (actions || []).forEach((action, i) => {
      const li = document.createElement('li');
      const id = `act-${entryId}-${i}`;
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.id = id;
      cb.dataset.entry = entryId;
      cb.dataset.idx = String(i);

      const storageKey = `mx-toolbox-check-${entryId}-${i}`;
      try {
        if (localStorage.getItem(storageKey) === '1') cb.checked = true;
      } catch (e) { /* ignore */ }

      cb.addEventListener('change', () => {
        try {
          localStorage.setItem(storageKey, cb.checked ? '1' : '0');
        } catch (e) { /* ignore */ }
      });

      const lbl = document.createElement('label');
      lbl.htmlFor = id;
      lbl.textContent = action;

      li.appendChild(cb);
      li.appendChild(lbl);
      list.appendChild(li);
    });
    return list;
  }

  function renderReadingList(items) {
    const ul = el('ul', 'reading-list');
    (items || []).forEach(item => {
      const li = document.createElement('li');
      if (item.url) {
        const a = document.createElement('a');
        a.href = item.url;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        a.textContent = item.title;
        li.appendChild(a);
      } else {
        li.textContent = item.title;
      }
      ul.appendChild(li);
    });
    return ul;
  }

  function renderEntryCard(entry, opts) {
    opts = opts || {};
    const card = el('article', 'result-card');
    card.dataset.entryId = entry.id;

    card.appendChild(el('h2', 'card-title', '【' + entry.title + '】'));
    card.appendChild(el('div', 'card-source', entry.theory_source));

    card.appendChild(section(
      '一句话',
      el('div', 'one-liner', entry.one_liner)
    ));

    if (entry.original_excerpts && entry.original_excerpts.length) {
      card.appendChild(section('原文', renderExcerpts(entry.original_excerpts)));
    }

    // Personalized interpretation from AI mode falls back to static one.
    const interp = opts.personalizedInterpretation || entry.modern_interpretation;
    if (interp) {
      card.appendChild(section(
        opts.personalizedInterpretation ? '为你的情况译读(AI)' : '现代译读',
        el('p', null, interp)
      ));
    }

    if (entry.analogy_story) {
      const storyWrap = document.createElement('div');
      storyWrap.appendChild(el('div', 'story-title', entry.analogy_story.title));
      storyWrap.appendChild(el('p', 'story-body', entry.analogy_story.body));
      card.appendChild(section('类比故事', storyWrap));
    }

    const actions = opts.personalizedActions || entry.action_checklist;
    if (actions && actions.length) {
      card.appendChild(section(
        opts.personalizedActions ? '为你定制的行动清单(AI)' : '行动清单',
        renderActionList(entry.id, actions)
      ));
    }

    if (entry.further_reading && entry.further_reading.length) {
      card.appendChild(section('延伸阅读', renderReadingList(entry.further_reading)));
    }

    if (entry.caveats) {
      const cav = el('div', 'caveat', entry.caveats);
      const wrap = el('div', 'section');
      wrap.appendChild(el('div', 'section-label', '注意'));
      wrap.appendChild(cav);
      card.appendChild(wrap);
    }

    return card;
  }

  window.renderEntryCard = renderEntryCard;
})();
