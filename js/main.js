/* 思想工具箱 · main.js
 *
 * 入口:绑定输入/按钮,读取 entries.json,触发匹配 / AI 分析,渲染结果。
 */

(function () {
  'use strict';

  let entries = [];

  async function loadEntries() {
    try {
      const res = await fetch('data/entries.json');
      if (!res.ok) throw new Error('HTTP ' + res.status);
      entries = await res.json();
    } catch (e) {
      console.error('无法加载 entries.json', e);
      showError('加载思想工具清单失败。如果你是直接双击 index.html 打开的,请改用 `npx serve .` 或 `python3 -m http.server` 在本地服务器访问。');
    }
  }

  function showError(msg) {
    const box = document.getElementById('results');
    box.classList.remove('hidden');
    box.innerHTML = '';
    const p = document.createElement('p');
    p.className = 'empty-state';
    p.textContent = msg;
    box.appendChild(p);
  }

  function showLoading() {
    const box = document.getElementById('results');
    box.classList.remove('hidden');
    box.innerHTML = '';
    const p = document.createElement('p');
    p.className = 'loading';
    p.textContent = '正在分析……';
    box.appendChild(p);
  }

  function renderResults(matches, opts) {
    opts = opts || {};
    const box = document.getElementById('results');
    box.classList.remove('hidden');
    box.innerHTML = '';

    const header = document.createElement('div');
    header.className = 'results-header';
    if (matches.length === 0 || opts.fallback) {
      header.textContent = '你的描述比较抽象,以下是三个基础工具,先试试';
    } else {
      header.textContent = '为你匹配到 ' + matches.length + ' 个工具' +
        (opts.aiMode ? ' · AI 个性化模式' : '');
    }
    box.appendChild(header);

    matches.forEach(m => {
      const cardOpts = {};
      if (opts.ai && opts.ai.personalized_interpretations &&
          opts.ai.personalized_interpretations[m.entry.id]) {
        cardOpts.personalizedInterpretation = opts.ai.personalized_interpretations[m.entry.id];
      }
      if (opts.ai && opts.ai.action_items && opts.ai.action_items[m.entry.id]) {
        cardOpts.personalizedActions = opts.ai.action_items[m.entry.id];
      }
      const card = renderEntryCard(m.entry, cardOpts);
      box.appendChild(card);
    });

    box.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  async function handleAnalyze() {
    const input = document.getElementById('user-input').value.trim();
    if (!input) {
      document.getElementById('user-input').focus();
      return;
    }

    if (!entries.length) {
      await loadEntries();
      if (!entries.length) return;
    }

    const btn = document.getElementById('analyze-btn');
    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = '分析中…';

    try {
      const useAI = window.AIMode && window.AIMode.isEnabled() && window.AIMode.getKey();

      if (useAI) {
        showLoading();
        try {
          const ai = await window.AIMode.analyze(input, entries);
          const ids = ai.matched_ids || [];
          const matches = ids
            .map(id => entries.find(e => e.id === id))
            .filter(Boolean)
            .map(entry => ({ entry, score: 100 }));
          if (matches.length) {
            renderResults(matches, { aiMode: true, ai });
            return;
          }
          // else fall through to local matching
        } catch (e) {
          console.warn('AI mode failed, falling back to local match:', e);
          showError('AI 模式调用失败,已回退到本地匹配。错误:' + e.message);
          setTimeout(() => runLocalMatch(input), 1200);
          return;
        }
      }

      runLocalMatch(input);
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  }

  function runLocalMatch(input) {
    let matches = window.Matcher.matchEntries(input, entries);
    const fallback = matches.length === 0;
    if (fallback) matches = window.Matcher.fallbackEntries(entries);
    renderResults(matches, { fallback });
  }

  function initExamples() {
    document.querySelectorAll('.example-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        const text = chip.getAttribute('data-fill') || chip.textContent;
        const ta = document.getElementById('user-input');
        ta.value = text;
        ta.dispatchEvent(new Event('input'));
        ta.focus();
      });
    });
  }

  function initCharCounter() {
    const ta = document.getElementById('user-input');
    const counter = document.getElementById('char-count');
    if (!ta || !counter) return;
    const update = () => { counter.textContent = ta.value.length; };
    ta.addEventListener('input', update);
    update();
  }

  function initSettings() {
    const toggle = document.getElementById('toggle-settings');
    const panel = document.getElementById('settings-panel');
    const enabledCb = document.getElementById('ai-enabled');
    const keyInput = document.getElementById('ai-apikey');
    const clearBtn = document.getElementById('clear-key');

    if (!toggle || !panel || !enabledCb || !keyInput) return;

    toggle.addEventListener('click', () => panel.classList.toggle('hidden'));

    // Initialize state from storage
    if (window.AIMode) {
      enabledCb.checked = window.AIMode.isEnabled();
      keyInput.value = window.AIMode.getKey();
    }

    enabledCb.addEventListener('change', () => {
      window.AIMode && window.AIMode.setEnabled(enabledCb.checked);
    });

    keyInput.addEventListener('change', () => {
      window.AIMode && window.AIMode.setKey(keyInput.value.trim());
    });

    if (clearBtn) {
      clearBtn.addEventListener('click', () => {
        keyInput.value = '';
        enabledCb.checked = false;
        window.AIMode && window.AIMode.setKey('');
        window.AIMode && window.AIMode.setEnabled(false);
      });
    }
  }

  function initKeyboard() {
    const ta = document.getElementById('user-input');
    if (!ta) return;
    ta.addEventListener('keydown', (e) => {
      // Cmd/Ctrl + Enter triggers analyze
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault();
        handleAnalyze();
      }
    });
  }

  document.addEventListener('DOMContentLoaded', async () => {
    const btn = document.getElementById('analyze-btn');
    if (btn) btn.addEventListener('click', handleAnalyze);
    initExamples();
    initCharCounter();
    initSettings();
    initKeyboard();
    loadEntries();
  });
})();
