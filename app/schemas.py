"""Pydantic schemas matching the Phase 5 API contract."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Problem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = "https://errors.study-agent.local/error"
    title: str
    status: int
    code: str
    detail: str
    request_id: str
    retryable: bool = False


class ChatPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str | None = None
    message: str = Field(min_length=1, max_length=20_000)
    request_id: str | None = None


class VisionConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["confirm", "cancel"]
    recognized_text: str | None = Field(default=None, max_length=20_000)
    formulas: list[dict] = Field(default_factory=list, max_length=32)


class ThreadCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="新会话", min_length=1, max_length=120)


class ThreadRename(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)


class FileIndexResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    file_id: str
    status: str
    reused: bool = False
    created_at: str
