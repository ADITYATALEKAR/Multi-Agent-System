"""Chat endpoint for reasoning over MAS runtime state and analysis results."""

from __future__ import annotations

from enum import StrEnum

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.runtime import get_runtime
from src.runtime.chat import RuntimeChatService

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["chat"])


class ChatIntent(StrEnum):
    ANSWER = "answer"
    ACTION = "action"


class ChatRequest(BaseModel):
    prompt: str = Field(..., description="Operator prompt to answer")
    task_id: str | None = None
    repo_path: str | None = None
    provider: str | None = None
    model: str | None = None


class ChatResponse(BaseModel):
    answer: str
    intent: ChatIntent = ChatIntent.ANSWER
    recommended_action: str | None = None
    source_task_id: str | None = None
    highlights: list[str] = Field(default_factory=list)
    cards: list[dict[str, str | None]] = Field(default_factory=list)
    follow_up_actions: list[dict[str, str]] = Field(default_factory=list)


@router.post("/chat")
def chat(request: ChatRequest) -> ChatResponse:
    """Reason over runtime state and return a chat response."""
    runtime = get_runtime()
    service = RuntimeChatService(runtime)
    reply = service.answer(
        prompt=request.prompt,
        task_id=request.task_id,
        repo_path=request.repo_path,
    )
    log.info(
        "chat_answered",
        task_id=reply.source_task_id,
        intent=reply.intent,
        provider=request.provider,
        model=request.model,
    )
    return ChatResponse(
        answer=reply.answer,
        intent=ChatIntent(reply.intent),
        recommended_action=reply.recommended_action,
        source_task_id=reply.source_task_id,
        highlights=reply.highlights,
        cards=[
            {
                "title": item.title,
                "body": item.body,
                "action": item.action,
                "action_label": item.action_label,
            }
            for item in reply.cards
        ],
        follow_up_actions=[
            {
                "action": item.action,
                "label": item.label,
            }
            for item in reply.follow_up_actions
        ],
    )
