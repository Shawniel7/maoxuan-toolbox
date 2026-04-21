/* 思想工具箱 · ai-mode.js
 *
 * 可选模块:如果用户粘贴了 Anthropic API key 并启用 AI 模式,
 * 把用户输入连同工具清单发给 Claude,拿到个性化的匹配和译读。
 *
 * 安全:API key 只存在 localStorage,仅向 api.anthropic.com 发送请求。
 */

(function () {
  'use strict';

  const STORAGE_KEY = 'mx-toolbox-api-key';
  const STORAGE_ENABLED = 'mx-toolbox-ai-enabled';
  const ANTHROPIC_URL = 'https://api.anthropic.com/v1/messages';
  const MODEL = 'claude-sonnet-4-6';

  function getKey() {
    try { return localStorage.getItem(STORAGE_KEY) || ''; } catch (e) { return ''; }
  }

  function setKey(v) {
    try {
      if (v) localStorage.setItem(STORAGE_KEY, v);
      else localStorage.removeItem(STORAGE_KEY);
    } catch (e) { /* ignore */ }
  }

  function isEnabled() {
    try { return localStorage.getItem(STORAGE_ENABLED) === '1'; } catch (e) { return false; }
  }

  function setEnabled(v) {
    try { localStorage.setItem(STORAGE_ENABLED, v ? '1' : '0'); } catch (e) { /* ignore */ }
  }

  function buildPrompt(userProblem, entries) {
    const trimmed = entries.map(e => ({
      id: e.id,
      title: e.title,
      theory_source: e.theory_source,
      one_liner: e.one_liner,
      tags: e.tags
    }));

    return `你是"毛泽东思想方法论助手"。你的工作是把经典分析方法应用到用户的当代困惑上,而不是讨论政治。

用户遇到了这样的困惑:

<user_problem>
${userProblem}
</user_problem>

可用的思想工具清单(JSON):

<tools>
${JSON.stringify(trimmed, null, 2)}
</tools>

请:
1. 从清单里挑选 2 - 3 个最相关的工具,返回它们的 id。
2. 对每个工具,基于用户的具体情境,写一段 150 字以内的"为你定制的译读"(中文,平实口吻,不堆叠名词)。
3. 对每个工具,给出 3 - 5 条针对这个用户的具体、可执行的行动建议。

**重要**:
- 严禁臆造任何毛泽东原文引文或历史事件细节。
- 严禁给出政治立场建议或评价。
- 如果用户的困惑涉及自伤、他人暴力、法律、医疗等严重问题,在 personalized_interpretations 里温和提醒对方寻求专业帮助。

返回严格的 JSON,结构如下(不要任何外部文字、不要 markdown 围栏):
{
  "matched_ids": ["id1", "id2"],
  "personalized_interpretations": { "id1": "...", "id2": "..." },
  "action_items": { "id1": ["a", "b", "c"], "id2": ["a", "b", "c"] }
}`;
  }

  async function analyze(userProblem, entries) {
    const key = getKey();
    if (!key) throw new Error('未设置 API key');

    const prompt = buildPrompt(userProblem, entries);

    const res = await fetch(ANTHROPIC_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': key,
        'anthropic-version': '2023-06-01',
        'anthropic-dangerous-direct-browser-access': 'true'
      },
      body: JSON.stringify({
        model: MODEL,
        max_tokens: 2000,
        messages: [{ role: 'user', content: prompt }]
      })
    });

    if (!res.ok) {
      const err = await res.text();
      throw new Error('Anthropic API 调用失败: ' + res.status + ' ' + err.slice(0, 200));
    }

    const data = await res.json();
    const text = (data.content && data.content[0] && data.content[0].text) || '';

    // Extract JSON — Claude may wrap it in text despite instructions.
    const match = text.match(/\{[\s\S]*\}/);
    if (!match) throw new Error('无法解析 AI 返回的结构化结果');

    return JSON.parse(match[0]);
  }

  window.AIMode = {
    getKey, setKey,
    isEnabled, setEnabled,
    analyze
  };
})();
