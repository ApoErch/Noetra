"""Chat: conversations per repository, and the SSE endpoint that streams one agent turn.

The agent (`core/agent`) runs inside the API process — a chat turn is interactive, the user
is watching tokens arrive, so it does not belong on the Celery queue (docs/ARCHITECTURE.md).
It only reads the DB; nothing here touches a cloned repo on disk.
"""

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from pydantic import BaseModel, Field
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from api.auth import get_current_user
from api.deps import get_owned_repository
from core.agent.runner import TurnResult, stream_turn
from core.db import SessionLocal, get_db
from core.models import ChatConversation, ChatMessage, ChatRole, Chunk, Repository, User

router = APIRouter(prefix="/api/v1/repos/{repository_id}/conversations", tags=["chat"])

# How many prior messages (user + assistant, alternating) the model sees on each turn.
# Tool results are never part of this — see ChatMessage's docstring.
HISTORY_MESSAGES = 12
TITLE_MAX_CHARS = 80


class MessageCreate(BaseModel):
    """Request body for sending one question."""

    content: str = Field(min_length=1, max_length=4_000)


def _conversation_json(conv: ChatConversation) -> dict[str, Any]:
    """Serialise a conversation row for the list/detail endpoints."""
    return {
        "id": str(conv.id),
        "title": conv.title,
        "created_at": conv.created_at.isoformat(),
        "updated_at": conv.updated_at.isoformat(),
    }


def _message_json(message: ChatMessage) -> dict[str, Any]:
    """Serialise a message row; citations/tool_trace are already JSON-shaped."""
    return {
        "id": str(message.id),
        "role": message.role.value,
        "content": message.content,
        "citations": message.citations or [],
        "tool_trace": message.tool_trace or [],
        "created_at": message.created_at.isoformat(),
    }


def _get_conversation(conversation_id: str, repo: Repository, user: User, db: Session) -> ChatConversation:
    """404 unless the conversation exists, belongs to this user, and is about this repo."""
    conv = db.scalar(
        select(ChatConversation).where(
            ChatConversation.id == conversation_id,
            ChatConversation.repository_id == repo.id,
            ChatConversation.user_id == user.id,
        )
    )
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@router.get("")
def list_conversations(
    repository_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """This user's conversations about this repo, most recently active first."""
    repo = get_owned_repository(repository_id, user, db)
    convs = db.scalars(
        select(ChatConversation)
        .where(ChatConversation.repository_id == repo.id, ChatConversation.user_id == user.id)
        .order_by(ChatConversation.updated_at.desc())
    ).all()
    return [_conversation_json(c) for c in convs]


@router.post("", status_code=201)
def create_conversation(
    repository_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Start an empty conversation; the title is set from the first question."""
    repo = get_owned_repository(repository_id, user, db)
    conv = ChatConversation(user_id=user.id, repository_id=repo.id)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return _conversation_json(conv)


@router.get("/{conversation_id}")
def get_conversation(
    repository_id: str,
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """A conversation with all its messages, oldest first."""
    repo = get_owned_repository(repository_id, user, db)
    conv = _get_conversation(conversation_id, repo, user, db)
    messages = db.scalars(
        select(ChatMessage).where(ChatMessage.conversation_id == conv.id).order_by(ChatMessage.created_at)
    ).all()
    return {**_conversation_json(conv), "messages": [_message_json(m) for m in messages]}


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(
    repository_id: str,
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Delete a conversation and (via ON DELETE CASCADE) its messages."""
    repo = get_owned_repository(repository_id, user, db)
    conv = _get_conversation(conversation_id, repo, user, db)
    db.delete(conv)
    db.commit()


def _history(db: Session, conv: ChatConversation) -> list[AnyMessage]:
    """The last HISTORY_MESSAGES stored messages as LangChain messages, oldest first."""
    rows = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conv.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(HISTORY_MESSAGES)
    ).all()
    history: list[AnyMessage] = []
    for row in reversed(rows):
        if row.role is ChatRole.USER:
            history.append(HumanMessage(content=row.content))
        else:
            history.append(AIMessage(content=row.content))
    return history


def _sse(event: dict[str, Any]) -> str:
    """One Server-Sent Events frame: `data: <json>` followed by a blank line."""
    return f"data: {json.dumps(event)}\n\n"


@router.post("/{conversation_id}/messages")
def send_message(
    repository_id: str,
    conversation_id: str,
    payload: MessageCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Ask a question; the answer streams back as SSE events.

    Events (one JSON object per `data:` line): `tool_call {name, args}`, `tool_result {name,
    summary}`, `token {text}`, `citations {items}`, `done {message_id}`, `error {detail}`.
    The user message is persisted before the stream starts; the assistant message once the
    turn completes. The stream runs on a fresh DB session because the request's session is
    closed when this handler returns, while the generator keeps running.
    """
    repo = get_owned_repository(repository_id, user, db)
    conv = _get_conversation(conversation_id, repo, user, db)
    if not db.scalar(select(exists().where(Chunk.repository_id == repo.id))):
        raise HTTPException(status_code=409, detail="Repository is still indexing; chat unlocks once chunking finishes")

    history = _history(db, conv)
    db.add(ChatMessage(conversation_id=conv.id, role=ChatRole.USER, content=payload.content))
    if not history:
        conv.title = payload.content[:TITLE_MAX_CHARS]
    db.commit()
    conv_id, repo_id, question = conv.id, repo.id, payload.content

    def generate() -> Iterator[str]:
        """Run the agent turn and translate its events into SSE frames."""
        session = SessionLocal()
        try:
            repo_row = session.get(Repository, repo_id)
            assert repo_row is not None
            for event in stream_turn(session, repo_row, question, history):
                if event["type"] != "result":
                    yield _sse(event)
                    continue
                result: TurnResult = event["result"]
                message_id = _persist_answer(session, conv_id, result)
                yield _sse({"type": "citations", "items": [c.model_dump(mode="json") for c in result.citations]})
                yield _sse({"type": "done", "message_id": str(message_id)})
        except Exception as exc:  # the stream is already open, so errors must travel as an event
            yield _sse({"type": "error", "detail": str(exc)})
        finally:
            session.close()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _persist_answer(session: Session, conversation_id: uuid.UUID, result: TurnResult) -> uuid.UUID:
    """Store the assistant message with its verified citations and tool steps; bump the conversation."""
    message = ChatMessage(
        conversation_id=conversation_id,
        role=ChatRole.ASSISTANT,
        content=result.answer,
        citations=[c.model_dump(mode="json") for c in result.citations],
        tool_trace=[dict(t) for t in result.tool_trace],
        # Explicit: the streaming session's transaction began when the turn started, so
        # `now()` would put the answer at the same instant as the question.
        created_at=datetime.now(UTC),
    )
    session.add(message)
    conv = session.get(ChatConversation, conversation_id)
    if conv is not None:
        conv.updated_at = message.created_at
        session.add(conv)
    session.commit()
    return message.id
