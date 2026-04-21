/* 思想工具箱 · matcher.js
 *
 * v1 版:关键词 + 标签打分。
 * trigger_keywords 命中 +3,tags 命中 +2,title 命中 +1。
 * 最多返回 3 条结果。无命中时由 main.js 自行处理兜底逻辑。
 */

(function () {
  'use strict';

  function normalize(str) {
    return (str || '').toLowerCase().trim();
  }

  function matchEntries(userInput, entries) {
    const input = normalize(userInput);
    if (!input) return [];

    const scored = entries.map(entry => {
      let score = 0;

      (entry.trigger_keywords || []).forEach(kw => {
        if (kw && input.includes(normalize(kw))) score += 3;
      });

      (entry.tags || []).forEach(tag => {
        if (tag && input.includes(normalize(tag))) score += 2;
      });

      if (entry.title && input.includes(normalize(entry.title))) {
        score += 1;
      }

      return { entry, score };
    });

    return scored
      .filter(r => r.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, 3);
  }

  // Fallback: return three foundational tools when nothing matched.
  function fallbackEntries(entries) {
    const preferred = ['main-contradiction', 'seek-truth-from-facts', 'practice-theory-cycle'];
    return preferred
      .map(id => entries.find(e => e.id === id))
      .filter(Boolean)
      .map(entry => ({ entry, score: 0 }));
  }

  window.Matcher = { matchEntries, fallbackEntries };
})();
