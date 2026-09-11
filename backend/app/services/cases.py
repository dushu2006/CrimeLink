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

    # Uniqueness is judged against the cases that are actually visible --
    # this jurisdiction's view of the ACTIVE dataset plus hand-created cases.
    # ``CASE_0001`` from a replaced dataset must not block a new dataset (or
    # an investigator) from using the same number: identifiers repeat between
    # test corpora by design, and ``(dataset_id, case_number)`` is the real
    # key.
    visible = (
        await session.execute(
            select(Case)
            .where(Case.case_number == case_number)
            .where(await registry.visibility_filter(session, Case))
        )
    ).scalars().first()
    if visible is not None:
        raise ConflictError(
            "A case with this number already exists in the active dataset."
        )

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
    active = await registry.active_dataset_id(session)
    if scope.expected_dataset_id and active and scope.expected_dataset_id != active:
        raise NotFoundError("The requested dataset is no longer active.")

    stmt = (
        select(Case)
        .where(scope.case_filter())
        .where(await registry.visibility_filter(session, Case))
        .order_by(Case.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await session.execute(stmt)).scalars().all())


async def visible_case_ids(session: AsyncSession, scope: JurisdictionScope) -> set[str]:
    """The id set every dataset-aware listing should range over.

    The single composition of the two boundaries -- jurisdiction AND active
    dataset -- that explorers, search, pattern and resolution queues all
    share, so no surface can forget one of them. ``scope.case_filter()`` also
    honours approved cross-jurisdiction grants; the dataset clause makes rows
    of a replaced dataset unreachable from every one of those surfaces, not
    just from the case pages themselves.
    """
    active = await registry.active_dataset_id(session)
    if scope.expected_dataset_id and active and scope.expected_dataset_id != active:
        return set()

    rows = (
        await session.execute(
            select(Case.id)
            .where(scope.case_filter())
            .where(await registry.visibility_filter(session, Case))
        )
    ).scalars()
    return set(rows)


async def resolve_case_ref(session: AsyncSession, scope: JurisdictionScope, ref: str) -> Case:
    """Resolve a case reference to a live, visible case row.

    Two shapes are accepted, in this order:

    1. the internal id (the primary contract of every API response);
    2. a human case number -- ``CASE_0001`` -- resolved *against the active
       dataset*.  Numbers repeat between test corpora on purpose, so a number
       is only ever looked up inside ``(active_dataset_id)`` plus hand-created
       cases; if two visible cases carry the same number (source data does
       this), the reference is ambiguous and refused rather than guessed.
    """
    active = await registry.active_dataset_id(session)
    if scope.expected_dataset_id and active and scope.expected_dataset_id != active:
        raise NotFoundError("This case belongs to a dataset that is no longer active.")

    case = await session.get(Case, ref)
    if case is None:
        if active is None:
            raise NotFoundError("No dataset is currently active.")
        candidates = list(
            (
                await session.execute(
                    select(Case)
                    .where(Case.case_number == ref)
                    .where(await registry.visibility_filter(session, Case))
                )
            ).scalars()
        )
        if len(candidates) > 1:
            raise NotFoundError(
                f"Case number {ref!r} matches {len(candidates)} cases in the active "
                "dataset; open one by its case id instead."
            )
        if not candidates:
            raise NotFoundError("Case not found.")
        case = candidates[0]
    case = scope.assert_case(case)
    if not registry.belongs_to_active(case, active):
        raise NotFoundError(
            "This case belongs to a dataset that is no longer active. "
            "Activate that dataset in Administration to open it again."
        )
    return case


async def get_case(
    session: AsyncSession, scope: JurisdictionScope, case_id: str
) -> Case:
    """One case, if the caller may see it and the active dataset owns it.

    A bookmarked link to a case from a replaced dataset must not reopen it:
    its documents, entities and graph are gone, and a page rendered from a row
    whose evidence no longer exists is worse than an honest 404.
    """
    return await resolve_case_ref(session, scope, case_id)


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
    return await resolve_case_ref(session, scope, case_id)
