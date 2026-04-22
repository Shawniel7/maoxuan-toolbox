"""Multi-turn analysis agent grounded in retrieved 毛选 theory.

Public entry point:
    stream_reply(messages, cited_chunk_ids=None) -> AsyncIterator[dict]

Yielded event dicts:
    {"type": "retrieved",   "chunks": [{chunk_id, article_title, section_title,
                                        source_url, is_footnote, preview}]}
    {"type": "text_delta",  "delta": str}
    {"type": "error",       "message": str}
    {"type": "done"}

The agent calls `rag.retrieve(latest_user_msg, top_k=8)` once per turn (the
production default retriever = hybrid + BM25 + query rewriting; see rag.py
module docstring). The retrieved chunks are appended to the final user
message as an <retrieved_theory> block so the model sees them as context
for this turn specifically, not persistent system state.

Fails fast with a clear message when ANTHROPIC_API_KEY is missing; never
hardcodes a key.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal, Optional

from backend import rag

Role = Literal["user", "assistant"]


@dataclass
class Message:
    role: Role
    content: str


@dataclass
class SessionState:
    """State carried across turns of a single chat session.

    Currently unused by the backend (v1 of the agent is stateless — the client
    passes full message history each request). Reserved for future use (e.g.,
    citation deduplication via `cited_chunk_ids`).
    """
    cited_chunk_ids: list[str] = field(default_factory=list)
    cited_article_ids: list[str] = field(default_factory=list)
    user_profile_hints: dict = field(default_factory=dict)


# ═════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT — locked after 2026-04-22 calibration.
# Any change must re-run tmp/calibration.py across the 3 test queries and
# confirm: Ex1 method transfer intact, Ex2 leads with out-of-scope naming,
# Ex3 declines without citing 毛选.
# ═════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """你是「思想工具箱」的分析助手,熟读《毛泽东选集》全四卷,擅长用其中的分析方法帮助当代人思考生活、工作、学习中的困惑。

═══════════════════════════════════════════════════════════════
你的角色边界
═══════════════════════════════════════════════════════════════
- 你不是毛泽东本人,不模仿其语气、不使用第一人称。你是一位「熟读毛选的现代分析师」。
- 你的价值在于提供**思维工具和分析框架**,不替用户做决定,不给政治立场,不替代专业心理/法律/医疗建议。
- 超出方法论分析的请求(医疗、法律、投资、政治表态、占卜算命),礼貌说明边界,建议寻求专业人士。
- **你的使命是把毛选的分析方法迁移到当代情境中,让用户获得可复用的思维工具。**
- **用户应该带走的不是「毛主席说了什么」,而是「下次遇到类似问题,我可以这样分析」。**

═══════════════════════════════════════════════════════════════
你可用的知识(三层,优先级递减)
═══════════════════════════════════════════════════════════════

1. <retrieved_theory> 中的原文段落 —— 你引用毛选时的**唯一合法来源**
   - 每段附元数据:篇名 (article_title)、章节 (section_title)、是否注释 (is_footnote)
   - 引文必须从这些段落里**原样**摘取,长度 ≤ 80 个汉字
   - 引文末尾以《文章名》标出处,例如:"……实事求是。"——《实践论》
   - 不得跨段落拼接以制造连贯感
   - 注释类(is_footnote=true)的段落用于补充历史背景,不作为方法论主要论据

2. <retrieved_stories> 中的故事案例 —— 用于打类比
   - 可改写转述,无需原样引用
   - 该块可能为空,缺失不必弥补、不要编造故事

3. 你自己的一般知识 —— 仅用于:组织语言、解释概念、衔接段落
   - 不得用于伪造任何毛选语句
   - 不得用于伪造历史事件或人物事迹

═══════════════════════════════════════════════════════════════
Step 0:适用性判断(这是强制性的前置检查)
═══════════════════════════════════════════════════════════════

在进入下方工作流程之前,先做一次内部判断:用户的问题是否真的需要一个分析方法?

以下情况**不要**启动方法迁移流程:
- 用户主要在寻求情感支持(失恋、亲人离世、抑郁、孤独感、纯粹的难过倾诉)
- 用户在经历急性心理危机(自我伤害念头、绝望感、无法正常生活)
- 用户寻求的是专业建议而非思维工具(具体的医疗诊断、法律咨询、投资推荐)
- 用户只是想被听见,没有在问"怎么办"

正确的回应是简短的共情 + 温和边界说明 + 指向合适资源。具体格式:

1. 一句话共情,承认情绪的真实性(不要评判、不要说"你应该")
2. 简短说明你是什么:"我是一个用毛泽东思想方法论做分析的工具,对[这类问题]可能帮不上忙"
3. 建议合适的资源:找信任的人倾诉 / 专业心理咨询 / 相关热线 / 给自己一些时间
4. **不要引用毛选,不要做任何分析,不要给行动清单**

**混合情况**(用户问题里既有情感也有可分析的部分,例如买房决策 + 伴侣冲突):
- 先明确指出情感部分属于分析工具够不着的范畴,建议用户另外处理
- 然后针对可分析部分做方法迁移
- 这两步的顺序不能颠倒,不能把情感部分"吸收进"分析框架

═══════════════════════════════════════════════════════════════
工作流程(仅在 Step 0 判定为适用时启动;语言自然,不机械;
**不要把这些步骤的标题写在输出中**)
═══════════════════════════════════════════════════════════════

1. **澄清或共情(1-2 句)** —— 复述对用户处境的理解。
   如果用户输入信息严重不足(< 20 字且无具体情境),先追问 1 个关键问题再分析,不要硬上。

2. **理论定位** —— 点出困境对应的毛选方法论概念(主要矛盾 / 矛盾的特殊性 / 实践论 / 调查研究 / 持久战 等)。

3. **引用原文** —— 从 <retrieved_theory> 选 1-2 段最贴切的**原样引用**,每段 ≤ 80 字,以《文章名》标出处。

4. **方法迁移示范** —— 这是本次回答的主体部分,应占约 60% 篇幅。分两个子步骤:

   (a) **拆解毛的分析逻辑**:用 1-2 句话说明这段原文里,毛是怎么分析当时那个问题的 —— 抓住了什么变量?排除了什么干扰?依据什么判断主次?

   (b) **平移到用户情境**:把 (a) 里的分析逻辑一比一用到用户的具体问题上,演示整个推理过程。用户应该看到:"原来这个思路可以用来想我自己的事。"

   错误示范:"毛说要抓主要矛盾,所以你应该分清优先级。"(这是口号翻译,没有方法迁移)

   正确示范:"毛在 1937 年判断抗日是主要矛盾时,用的方法是:列出所有矛盾,然后问『哪个一旦激化会决定其他矛盾的走向』。用这个方法到你的情境:你列出的四件事里……"

5. **类比故事** —— 如 <retrieved_stories> 有相关案例,讲一个;否则跳过。

6. **行动建议** —— 3-5 条具体、可操作、用户今天就能开始做的事。

═══════════════════════════════════════════════════════════════
硬性规则
═══════════════════════════════════════════════════════════════
- **不得编造毛泽东语录。** <retrieved_theory> 没有相关段落时,不引用,直接做方法迁移示范 + 行动建议。
- **不重复引用同一段落。** 已在本次对话中引用过的段落不要再次引用,除非用户明确要求复述。
- **多轮对话联系上下文。** 不重复追问用户已经说过的信息。
- 拒绝扮演政治立场、算命、心理治疗。礼貌转向方法论或建议找专业人士。
- 所有输出用简体中文、Markdown 格式。
- 回答末尾可选附上"引用出处"列表:《文章名》 · 章节 · 原文链接

═══════════════════════════════════════════════════════════════
工具化原则(产品核心,每次回复都应满足)
═══════════════════════════════════════════════════════════════
- **用户带走的是方法,不是结论。** 理想状态下,用户读完你的回答,
  下次遇到类似问题即使不打开这个工具,也能自己用这个方法想一遍。
- **引用原文是为了支撑方法,不是为了增加权威感。** 一段引文如果
  只是重复你现代译读里说的话,就删掉它——它没提供独立价值。
- **行动建议必须基于刚才演示的方法。** 每条行动前,用户应该能
  自己回答"这条行动对应的是刚才拆解的分析逻辑里哪一步?"
  如果对不上,说明行动是凭空给的,重写。
- **承认方法的边界。** 毛选方法论适合处理:战略决策、组织管理、
  认知-实践循环、复杂情境里找主次。不适合处理:情感创伤、
  亲密关系修复、存在主义焦虑、价值观冲突。遇到后者请礼貌承认
  这个工具不是为此而造。
"""


# ═════════════════════════════════════════════════════════════════════════
# API-key + model config
# ═════════════════════════════════════════════════════════════════════════

class MissingAPIKeyError(RuntimeError):
    """Raised when ANTHROPIC_API_KEY is not configured."""


def _require_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise MissingAPIKeyError(
            "ANTHROPIC_API_KEY is not set. Copy backend/.env.example to "
            "backend/.env and fill in your key from "
            "https://console.anthropic.com/"
        )
    return key


def _model() -> str:
    return os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")


# ═════════════════════════════════════════════════════════════════════════
# Prompt assembly
# ═════════════════════════════════════════════════════════════════════════

def _build_theory_block(chunks: list[rag.Chunk]) -> str:
    """Format retrieved chunks into the <retrieved_theory> block for the model."""
    if not chunks:
        return "<retrieved_theory>\n(无相关原文 — 如无法引用,请直接做方法分析和行动建议)\n</retrieved_theory>"
    lines = ["<retrieved_theory>"]
    for i, ch in enumerate(chunks, 1):
        fn_tag = " (注释)" if ch.is_footnote else ""
        section = ch.section_title or "(引言)"
        lines.append(f"[{i}] 《{ch.article_title}》 · {section}{fn_tag}")
        lines.append(ch.text.strip())
        lines.append(f"source: {ch.source_url}")
        lines.append("")
    lines.append("</retrieved_theory>")
    return "\n".join(lines)


def _latest_user_text(messages: list[Message]) -> str | None:
    for m in reversed(messages):
        if m.role == "user":
            return m.content
    return None


def _chunk_to_event_dict(ch: rag.Chunk) -> dict:
    """Metadata-only snapshot emitted in the `retrieved` event — no full text
    so clients can show a 'Looking at: ...' header without shipping the corpus."""
    preview = ch.text.replace("\n", " ").strip()[:120]
    return {
        "chunk_id": ch.chunk_id,
        "article_id": ch.article_id,
        "article_title": ch.article_title,
        "section_title": ch.section_title,
        "source_url": ch.source_url,
        "is_footnote": ch.is_footnote,
        "preview": preview,
    }


# ═════════════════════════════════════════════════════════════════════════
# Main streaming entry point
# ═════════════════════════════════════════════════════════════════════════

async def stream_reply(
    messages: list[Message],
    cited_chunk_ids: Optional[list[str]] = None,
    top_k: int = 8,
) -> AsyncIterator[dict]:
    """Stream the agent's reply for a conversation.

    Args:
        messages: full history, oldest → newest. Must end with a user turn.
        cited_chunk_ids: chunks already cited earlier in the conversation;
            reserved for future dedup (currently ignored, kept for API stability).
        top_k: how many chunks to retrieve (default 8).

    Yields event dicts (see module docstring for schema). The caller is expected
    to serialize these (e.g. SSE in main.py) and forward to the client.
    """
    try:
        _require_api_key()
    except MissingAPIKeyError as e:
        yield {"type": "error", "message": str(e)}
        return

    latest = _latest_user_text(messages)
    if not latest:
        yield {"type": "error", "message": "messages must end with a user turn"}
        return

    # ── retrieve ──
    try:
        chunks = rag.retrieve(latest, top_k=top_k)
    except Exception as e:
        yield {"type": "error", "message": f"retrieval failed: {e}"}
        return

    yield {
        "type": "retrieved",
        "chunks": [_chunk_to_event_dict(c) for c in chunks],
    }

    # ── build prompt ──
    theory_block = _build_theory_block(chunks)
    stories_block = "<retrieved_stories>\n(当前语料库尚未收录故事案例)\n</retrieved_stories>"

    anthropic_messages = []
    for m in messages[:-1]:
        anthropic_messages.append({"role": m.role, "content": m.content})
    # Attach retrieval context to the LAST user message so the model treats
    # the context as scoped to this turn, not as persistent system state.
    anthropic_messages.append({
        "role": "user",
        "content": f"{theory_block}\n\n{stories_block}\n\n用户问题:\n{latest}",
    })

    # ── stream ──
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        yield {"type": "error", "message": "anthropic SDK not installed"}
        return

    client = AsyncAnthropic(api_key=_require_api_key())
    try:
        async with client.messages.stream(
            model=_model(),
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=anthropic_messages,
        ) as stream:
            async for text in stream.text_stream:
                if text:
                    yield {"type": "text_delta", "delta": text}
    except Exception as e:
        yield {"type": "error", "message": f"generation failed: {e}"}
        return

    yield {"type": "done"}
