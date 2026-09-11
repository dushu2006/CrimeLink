"""Dataset-pinned investigation threads.

A thread remembers prior questions, confirmed facts, open hypotheses,
examined entities, open gaps, and unresolved mentions — so a follow-up
question builds on the last answer instead of starting cold. Every list
is bounded (memory is a rolling window, not an archive), and the thread
is pinned to its dataset: continuing against a different active dataset
is refused rather than allowed to leak conclusions across data.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import new_uuid
from app.db.models import InvestigationSession
from app.errors import NotFoundError, ValidationFailedError

from .schemas import MemorySection

MAX_QUESTIONS = 20
MAX_CONFIRMED = 50
MAX_HYPOTHESES = 20
MAX_ENTITIES = 100
MAX_GAPS = 50
MAX_UNRESOLVED = 50
MAX_CONTRADICTIONS = 20


def blank_state(objective: str = "") -> dict[str, Any]:
    """Fresh thread state; every list starts empty and bounded."""
    return {
        "objective": objective,
        "questions": [],
        "confirmed": [],
        "hypotheses": [],
        "entities": [],
        "gaps": [],
        "unresolved": [],
        "contradictions": [],
    }


def _bounded(items: list, newcomers: list, limit: int) -> list:
    merged = [item for item in items if item not in newcomers] + list(newcomers)
    return merged[-limit:]


def record_turn(
    state: dict[str, Any],
    *,
    question: str,
    facts: list[str] | None = None,
    hypotheses: list[dict[str, Any]] | None = None,
    entities: list[str] | None = None,
    gaps: list[str] | None = None,
    unresolved: list[str] | None = None,
    contradictions: list[str] | None = None,
) -> dict[str, Any]:
    """Merge one answered question into the thread state (bounded)."""
    merged = dict(state or blank_state())
    merged["questions"] = _bounded(merged.get("questions", []), [question], MAX_QUESTIONS)
    merged["confirmed"] = _bounded(merged.get("confirmed", []), facts or [], MAX_CONFIRMED)
    merged["hypotheses"] = _bounded(merged.get("hypotheses", []), hypotheses or [], MAX_HYPOTHESES)
    merged["entities"] = _bounded(merged.get("entities", []), entities or [], MAX_ENTITIES)
    merged["gaps"] = _bounded(merged.get("gaps", []), gaps or [], MAX_GAPS)
    merged["unresolved"] = _bounded(merged.get("unresolved", []), unresolved or [], MAX_UNRESOLVED)
    merged["contradictions"] = _bounded(
        merged.get("contradictions", []), contradictions or [], MAX_CONTRADICTIONS
    )
    if not merged.get("objective"):
        merged["objective"] = question[:300]
    return merged


async def get_session(
    session: AsyncSession, investigation_id: str, *, dataset_id: str
) -> InvestigationSession:
    """Load a thread, refusing cross-dataset continuation."""
    row = (
        await session.execute(
            select(InvestigationSession).where(InvestigationSession.id == investigation_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"Unknown investigation {investigation_id}.")
    if row.dataset_id != dataset_id:
        raise ValidationFailedError(
            "That investigation belongs to a different dataset; "
            "start a new thread against the active one."
        )
    return row


async def create_session(
    session: AsyncSession,
    *,
    dataset_id: str,
    case_id: str | None,
    scope: str,
    title: str,
    created_by: str | None,
    thread_id: str | None = None,
) -> InvestigationSession:
    """Open a new thread (flushed, committed by the caller)."""
    row = InvestigationSession(
        id=thread_id or new_uuid(),
        dataset_id=dataset_id,
        case_id=case_id,
        scope=scope,
        title=title[:300],
        state=blank_state(title),
        created_by=created_by,
    )
    session.add(row)
    await session.flush()
    return row


def memory_section(row: InvestigationSession) -> MemorySection:
    """Render a thread row as the answer's memory section."""
    state = dict(row.state or {})
    questions = [str(question) for question in state.get("questions", [])]
    return MemorySection(
        investigation_id=row.id,
        questions_asked=len(questions),
        prior_questions=questions,
        confirmed_facts=[str(item) for item in state.get("confirmed", [])],
        open_hypotheses=[dict(item) for item in state.get("hypotheses", []) if isinstance(item, dict)],
        examined_entities=[str(item) for item in state.get("entities", [])],
        open_gaps=[str(item) for item in state.get("gaps", [])],
        unresolved=[str(item) for item in state.get("unresolved", [])],
    )
