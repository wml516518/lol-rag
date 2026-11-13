"""请求/响应模型：字段严格对齐 docs/api-contract.md 第 5 节。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

MAX_MESSAGES = 20
MAX_CONTENT_LENGTH = 2000


class ChatMessage(BaseModel):
    """一条对话消息：role 只允许 user / assistant（system 由后端自己追加）。"""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_CONTENT_LENGTH)

    @field_validator("content")
    @classmethod
    def content_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content 不能为空白")
        return value


class ChatRequest(BaseModel):
    """POST /api/chat 请求体。超过 20 条时保留最新的 20 条（在 handler 里截断）。"""

    messages: list[ChatMessage] = Field(min_length=1)
