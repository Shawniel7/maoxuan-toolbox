"""Multi-turn analysis agent grounded in retrieved theory + stories.

Entry points:
    stream_reply(messages: list[Message], session_state: SessionState) -> AsyncIterator[Event]

Events emitted:
    {"type": "text_delta", "delta": str}
    {"type": "citation", "chunk_id": str, "article_title": str, "source_url": str}
    {"type": "done"}

Fails fast with a clear message when ANTHROPIC_API_KEY is missing — never hardcode.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal


Role = Literal["user", "assistant"]


@dataclass
class Message:
    role: Role
    content: str


@dataclass
class SessionState:
    """State carried across turns of a single chat session."""
    cited_chunk_ids: list[str] = field(default_factory=list)
    cited_article_ids: list[str] = field(default_factory=list)
    user_profile_hints: dict = field(default_factory=dict)


SYSTEM_PROMPT = """你是"思想工具箱"的分析助手，熟读《毛泽东选集》全四卷，擅长用其中的分析方法帮助当代人思考生活、工作、学习中的困惑。

## 你的角色边界

- 你不是毛泽东本人，不模仿其语气、第一人称。你是一位"熟读毛选的现代分析师"。
- 你的价值在于提供**思维工具和分析框架**，不替用户做决定，不给政治立场，不替代专业心理/法律/医疗建议。
- 超出方法论分析的请求（医疗、法律、投资、政治表态），礼貌说明边界，建议寻求专业人士。

## 知识使用原则

你可用的知识分三层，优先级从高到低：
1. <retrieved_theory> 中的原文片段 —— 这是你引用时的**唯一合法来源**
2. <retrieved_stories> 中的故事案例 —— 用于类比
3. 你自己的一般知识 —— 只用于组织语言、解释概念，不用于伪造任何毛选引文

## 工作流程（语言自然，不机械）

1. **澄清或共情（1-2 句）**：复述理解，必要时追问一个关键问题
2. **理论定位**：点出困境对应的毛选方法论概念
3. **引用原文**：从 <retrieved_theory> 选 1-2 段**原样引用**，每段 ≤ 80 字，注明出处
4. **现代译读**：用当代语言解释如何适用用户情境
5. **类比故事**：如 <retrieved_stories> 有相关案例，讲一个
6. **行动建议**：3-5 条具体可操作、今天能开始的行动

## 硬性规则

- **不得编造毛泽东语录**。<retrieved_theory> 没有对应的就不要引用。
- 引文必须原样，不得拼接多段伪造连贯性。
- **多轮对话联系上下文**，不重复追问已知信息。
- 用户描述过于模糊（< 20 字且无具体情境），先追问 1-2 个具体问题再分析。
- 拒绝扮演政治立场、算命、心理治疗。这些请求礼貌转向方法论或建议找专业人士。
- 所有输出用简体中文，Markdown 格式。
"""


class MissingAPIKeyError(RuntimeError):
    """Raised when ANTHROPIC_API_KEY is not configured."""


def _require_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise MissingAPIKeyError(
            "ANTHROPIC_API_KEY is not set. Copy backend/.env.example to backend/.env "
            "and fill in your key from https://console.anthropic.com/"
        )
    return key


async def stream_reply(
    messages: list[Message],
    session_state: SessionState,
):  # -> AsyncIterator[dict]
    """Stream the agent's reply.

    Flow per turn:
        1. If latest user message is < 20 chars with no context → emit clarifying question, return
        2. Call rag.retrieve_with_rerank(user_message, top_k=8)
        3. Load relevant stories from corpus/stories/
        4. Build prompt with <retrieved_theory> and <retrieved_stories> blocks
        5. Stream Anthropic API response, emit text_delta + citation events
    """
    _require_api_key()
    raise NotImplementedError("step-6: implement agent loop")
