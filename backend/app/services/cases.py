"""Case management (PRD 10).

Case listing is always jurisdiction-scoped at the query level — an officer sees
their own jurisdiction's cases plus any case covered by an approved, unexpired
cross-jurisdiction grant.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.datasets import registry
from app.db.base import utcnow
from app.db.models import Case, CaseDocument, DetectedPattern, EntityResolutionItem
from app.domain.enums import CaseStatus, PatternStatus, ResolutionStatus
from app.errors import ConflictError, NotFoundError
from app.security.deps import JurisdictionScope, Principal


async def create_case(
    session: AsyncSession,
    *,
    principal: Principal,
    case_number: str,
    title: str,
    jurisdiction_id: str | None = None,
) -> Case:
    jurisdiction = jurisdiction_id or principal.jurisdiction_id
    if jurisdiction != principal.jurisdiction_id and principal.role.value != "ADMIN":
        # Creating a case in another jurisdiction is an administrative act.
        from app.errors import PermissionDeniedError

        raise PermissionDeniedError("You cannot create a case in another jurisdiction.")

    existing = (
        await session.execute(select(Case).where(Case.case_number == case_number))
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("A case with this number already exists.")

    case = Case(
        case_number=case_number,
        title=title,
        jurisdiction_id=jurisdiction,
        status=CaseStatus.OPEN,
        created_by=principal.id,
    )
    session.add(case)
    await session.flush()
    return case


async def list_cases(
    session: AsyncSession, scope: JurisdictionScope, *, limit: int = 100, offset: int = 0
) -> list[Case]:
    """Cases the caller may see, from the active dataset only.

    Two filters, both in SQL. Jurisdiction decides what this officer is
    entitled to; the dataset filter decides what the system currently holds.
    Without the second one, replacing a dataset left the previous import's
    cases listed here -- present in the table, absent from the graph, and
    restored by every browser refresh.
    """
    stmt = (
        select(Case)
        .where(scope.case_filter())
        .where(await registry.visibility_filter(session, Case))
        .order_by(Case.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_case(
    session: AsyncSession, scope: JurisdictionScope, case_id: str
) -> Case:
    """One case, if the caller may see it and the active dataset owns it.

    A bookmarked link to a case from a replaced dataset must not reopen it:
    its documents, entities and graph are gone, and a page rendered from a row
    whose evidence no longer exists is worse than an honest 404.
    """
    case = await session.get(Case, case_id)
    case = scope.assert_case(case)
    active = await registry.active_dataset_id(session)
    if not registry.belongs_to_active(case, active):
        raise NotFoundError(
            "This case belongs to a dataset that is no longer active. "
            "Activate that dataset in Administration to open it again."
        )
    return case


async def case_summaries(
    session: AsyncSession, scope: JurisdictionScope, *, limit: int = 100, offset: int = 0
) -> list[dict]:
    """Case list rows with document counts and pending-review counts.

    The UI's case table shows "pending reviews" because an un-actioned review
    queue is the one thing that must never be allowed to quietly grow.
    """
    cases = await list_cases(session, scope, limit=limit, offset=offset)
    out: list[dict] = []
    for case in cases:
        documents = int(
            (
                await session.execute(
                    select(func.count(CaseDocument.id)).where(
                        CaseDocument.case_id == case.id, CaseDocument.is_deleted.is_(False)
                    )
                )
            ).scalar()
            or 0
        )
        pending_reviews = int(
            (
                await session.execute(
                    select(func.count(EntityResolutionItem.id)).where(
                        EntityResolutionItem.case_id == case.id,
                        EntityResolutionItem.status == ResolutionStatus.PENDING,
                    )
                )
            ).scalar()
            or 0
        ) + int(
            (
                await session.execute(
                    select(func.count(DetectedPattern.id)).where(
                        DetectedPattern.case_id == case.id,
                        DetectedPattern.status == PatternStatus.NEW,
                    )
                )
            ).scalar()
            or 0
        )
        out.append(
            {
                "id": case.id,
                "case_number": case.case_number,
                "title": case.title,
                "jurisdiction_id": case.jurisdiction_id,
                "status": case.status.value,
                "document_count": documents,
                "pending_review_count": pending_reviews,
                "created_at": case.created_at.isoformat() if case.created_at else None,
            }
        )
    return out


async def update_status(session: AsyncSession, case: Case, status: CaseStatus) -> Case:
    case.status = status
    if status == CaseStatus.CLOSED:
        case.closed_at = utcnow()
    await session.flush()
    return case


async def require_case(session: AsyncSession, scope: JurisdictionScope, case_id: str) -> Case:
    """Load a case for an operation, or refuse with a reason.

    Shares :func:`get_case`'s rules deliberately: every entry point into a
    case -- the detail page, documents, the AI panel -- has to agree about
    whether that case exists right now, or one of them becomes the way stale
    data gets back on screen.
    """
    case = await session.get(Case, case_id)
    if case is None:
        raise NotFoundError("Case not found.")
    case = scope.assert_case(case)
    active = await registry.active_dataset_id(session)
    if not registry.belongs_to_active(case, active):
        raise NotFoundError(
            "This case belongs to a dataset that is no longer active. "
            "Activate that dataset in Administration to open it again."
        )
    return case
