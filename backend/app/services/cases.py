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
from app.domain.enums import CaseStatus, PatternStatus, ResolutionStatus, Role
from app.errors import ConflictError, NotFoundError, PermissionDeniedError, ValidationFailedError
from app.security.deps import JurisdictionScope, Principal


async def create_case(
    session: AsyncSession,
    *,
    principal: Principal,
    case_number: str,
    title: str,
    jurisdiction_id: str | None = None,
    classification=None,
) -> Case:
    jurisdiction = jurisdiction_id or principal.jurisdiction_id
    from app.security.classification import require_classification
    require_classification(principal, classification or "INTERNAL")
    if jurisdiction != principal.jurisdiction_id and principal.role.value not in {"ADMIN", "SUPER_ADMIN", "DISTRICT_ADMIN", "STATION_ADMIN"}:
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
        classification=classification or "INTERNAL",
        created_by=principal.id,
    )
    session.add(case)
    await session.flush()
    return case


#: ``dataset_case_key`` of the synthetic dataset container case, created by
#: ``datasets/pipeline.py::_materialise_cases`` for every import.
_CONTAINER_CASE_KEY = "ALL"


def _real_case_exclusions() -> list:
    """Clauses that identify synthetic container cases.

    The natural key is authoritative; the number patterns are belt and
    braces for hand-edited rows from older imports.
    """
    return [
        Case.dataset_case_key.is_(None) | (Case.dataset_case_key != _CONTAINER_CASE_KEY),
        ~Case.case_number.like("%(unassigned records)%"),
        ~Case.case_number.like("%(all records)%"),
    ]


async def _sole_container_case(session: AsyncSession) -> Case | None:
    """The container case, visible only while it is the active dataset's sole case.

    Every import materialises a container case so dataset-level records
    (records no case claims) have a home — see
    ``datasets/pipeline.py::_materialise_cases``.  It is not a real case:

    * when the dataset also holds real cases, listings show those and the
      container stays hidden (unchanged behaviour);
    * when it is the **only** case of the active dataset, it must be visible:
      otherwise a case-less corpus (a phone directory, a bank customer list —
      the production "Upload of 577 files v1" dataset) surfaces as
      "No cases available" / "Could not resolve the active case" while the
      admin console happily lists the very case that is there.  That
      divergence is the production inconsistency this method removes.

    The container is dataset-level data: every role of the deployment shares
    the same dataset, so the *dataset* boundary — not the jurisdiction
    boundary, which applies to real cases — decides its visibility.  (In the
    production deployment the import carries jurisdiction SYN-DEV while the
    demo accounts sit in METRO-CENTRAL; jurisdiction-filtering the container
    made the whole app empty for everyone while the admin tab showed the
    case.)
    """
    real_count = (
        await session.execute(
            select(func.count(Case.id))
            .where(await registry.visibility_filter(session, Case))
            .where(*_real_case_exclusions())
        )
    ).scalar() or 0
    if real_count:
        return None
    return (
        await session.execute(
            select(Case)
            .where(await registry.visibility_filter(session, Case))
            .where(Case.dataset_case_key == _CONTAINER_CASE_KEY)
            .order_by(Case.created_at.desc())
            .limit(1)
        )
    ).scalars().first()


async def is_sole_container_case(session: AsyncSession, case_id: str) -> bool:
    """True when *case_id* is the container case of a case-less dataset.

    The single predicate every case-scoped read (documents, graph, timeline)
    uses to decide whether dataset-level rows (``case_id IS NULL``) belong to
    that "case" — see :func:`_sole_container_case`.
    """
    case = await session.get(Case, case_id)
    if case is None or case.dataset_case_key != _CONTAINER_CASE_KEY:
        return False
    sole = await _sole_container_case(session)
    return sole is not None and sole.id == case.id


async def list_cases(
    session: AsyncSession, scope: JurisdictionScope, *, limit: int = 100, offset: int = 0
) -> list[Case]:
    """Cases the caller may see, from the active dataset only.

    Two filters, both in SQL. Jurisdiction decides what this officer is
    entitled to; the dataset filter decides what the system currently holds.
    Without the second one, replacing a dataset left the previous import's
    cases listed here -- present in the table, absent from the graph, and
    restored by every browser refresh.

    When the active dataset contains no real case, the synthetic container
    case stands in (see :func:`_sole_container_case`), so a case-less corpus
    resolves to a working dashboard instead of "No cases available".
    """
    active = await registry.active_dataset_id(session)
    if scope.expected_dataset_id and active and scope.expected_dataset_id != active:
        raise NotFoundError("The requested dataset is no longer active.")

    stmt = (
        select(Case)
        .where(scope.case_filter())
        .where(await registry.visibility_filter(session, Case))
        .where(*_real_case_exclusions())
        .order_by(Case.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    cases = list((await session.execute(stmt)).scalars().all())
    if not cases:
        container = await _sole_container_case(session)
        if container is not None:
            cases = [container]
    return cases


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
            .where(*_real_case_exclusions())
        )
    ).scalars()
    ids = set(rows)
    if not ids:
        container = await _sole_container_case(session)
        if container is not None:
            ids.add(container.id)
    return ids


async def active_dataset_case_ids(session: AsyncSession, scope: JurisdictionScope) -> set[str]:
    """The id set STRICTLY for the active dataset — master graph universe.

    Unlike :func:`visible_case_ids`, this excludes NULL-dataset rows. The master
    graph, master analytics, and master investigation are defined as the active
    dataset's own cases/entities/relationships. Including legacy NULL rows would
    make /datasets/stats (52 cases) disagree with /graph/master (62 case_ids).

    Jurisdiction filtering still applies, so an officer only sees cases they are
    entitled to within the active dataset.
    """
    active = await registry.active_dataset_id(session)
    if active is None:
        return set()
    if scope.expected_dataset_id and active and scope.expected_dataset_id != active:
        return set()

    rows = (
        await session.execute(
            select(Case.id)
            .where(scope.case_filter())
            .where(await registry.strict_active_filter(session, Case))
            .where(*_real_case_exclusions())
        )
    ).scalars()
    ids = set(rows)
    # The container case is dataset-level scope, not a jurisdiction-scoped
    # case: records no investigation claims belong to it (graph_build's
    # container pass), and the master graph is the whole active dataset.
    # Without this, a case-less dataset projects an empty master graph, and
    # in mixed datasets the container's records vanish from it while
    # /datasets/stats still counts them.
    container_id = (
        await session.execute(
            select(Case.id)
            .where(await registry.strict_active_filter(session, Case))
            .where(Case.dataset_case_key == _CONTAINER_CASE_KEY)
            .limit(1)
        )
    ).scalar_one_or_none()
    if container_id is not None:
        ids.add(container_id)
    return ids


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
    if ref.strip().upper() == "ALL":
        raise NotFoundError("The container case 'ALL' is not an investigative case.")

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
                    .where(Case.dataset_case_key.is_(None) | (Case.dataset_case_key != "ALL"))
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
    if case.dataset_case_key == _CONTAINER_CASE_KEY:
        # The container case stands in for a case-less dataset (see
        # :func:`_sole_container_case`).  While it is the active dataset's
        # sole case it is reachable from any role of the deployment — it is
        # dataset-level data, not a jurisdiction-scoped case — and refused
        # every other way.
        sole = await _sole_container_case(session)
        if sole is None or sole.id != case.id or not registry.belongs_to_active(case, active):
            raise NotFoundError("The container case 'ALL' is not an investigative case.")
        return case
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
    """Case list rows with the counts the case table actually displays.

    Every number here is counted from stored data — documents, source
    references, findings, people and person-to-person relationships — never
    cached in the frontend and never a placeholder.  "Pending reviews" is
    included because an un-actioned review queue is the one thing that must
    never be allowed to quietly grow.
    """
    from app.db.models import InvestigationFinding, SourceReference

    cases = await list_cases(session, scope, limit=limit, offset=offset)
    case_ids = [case.id for case in cases]

    async def _counts(model, column) -> dict[str, int]:
        if not case_ids:
            return {}
        rows = (
            await session.execute(
                select(column, func.count(model.id))
                .where(column.in_(case_ids))
                .group_by(column)
            )
        ).all()
        return {row[0]: int(row[1]) for row in rows}

    doc_counts = await _counts(CaseDocument, CaseDocument.case_id)
    source_counts = await _counts(SourceReference, SourceReference.case_id)
    finding_counts = await _counts(InvestigationFinding, InvestigationFinding.case_id)

    # People and relationships come from the same graph the People and
    # Relationships pages read, so the three surfaces can never disagree about
    # how many people a case has.  One snapshot and one derivation serve the
    # whole page; a graph problem must never blank the case list.
    person_counts: dict[str, int] = {}
    relationship_counts: dict[str, int] = {}
    if case_ids:
        try:
            from app.container import get_container
            from app.domain.enums import canonical_label
            from app.services.person_relationships import derive_person_relationships

            snapshot = get_container().graph_store.multi_case_snapshot(
                case_ids, include_inactive=False
            )
            for node in snapshot.nodes.values():
                if canonical_label(node.label) != "PERSON":
                    continue
                for cid in node.properties.get("case_ids") or []:
                    if cid in set(case_ids):
                        person_counts[cid] = person_counts.get(cid, 0) + 1
            derived = derive_person_relationships(snapshot)
            wanted = set(case_ids)
            for edge in derived["edges"]:
                for cid in edge["case_ids"]:
                    if cid in wanted:
                        relationship_counts[cid] = relationship_counts.get(cid, 0) + 1
        except Exception:  # pragma: no cover - graph unavailable
            person_counts, relationship_counts = {}, {}

    # The case list must agree with the case pages: when the container case
    # of a case-less dataset is shown, its "documents" are the dataset-level
    # rows (case_id NULL) that ``document_service.list_documents`` serves for
    # it — counted here with the identical predicate.
    sole_container = await _sole_container_case(session)
    dataset_level_docs = None
    if sole_container is not None:
        active = await registry.active_dataset_id(session)
        if active is not None:
            dataset_level_docs = (CaseDocument.dataset_id == active) | CaseDocument.dataset_id.is_(
                None
            )
        else:
            dataset_level_docs = CaseDocument.dataset_id.is_(None)

    out: list[dict] = []
    for case in cases:
        doc_clause = CaseDocument.case_id == case.id
        if case.id == (sole_container.id if sole_container is not None else None):
            doc_clause = doc_clause | (CaseDocument.case_id.is_(None) & dataset_level_docs)
        documents = int(
            (
                await session.execute(
                    select(func.count(CaseDocument.id)).where(
                        doc_clause, CaseDocument.is_deleted.is_(False)
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
                "classification": case.classification.value,
                "document_count": documents,
                "source_count": source_counts.get(case.id, 0),
                "finding_count": finding_counts.get(case.id, 0),
                "person_count": person_counts.get(case.id, 0),
                "relationship_count": relationship_counts.get(case.id, 0),
                "pending_review_count": pending_reviews,
                "created_at": case.created_at.isoformat() if case.created_at else None,
            }
        )
    return out


_CASE_TRANSITIONS: dict[CaseStatus, set[CaseStatus]] = {
    CaseStatus.DRAFT: {CaseStatus.OPEN, CaseStatus.ACTIVE_INVESTIGATION},
    CaseStatus.OPEN: {CaseStatus.ACTIVE_INVESTIGATION, CaseStatus.UNDER_REVIEW},
    CaseStatus.ACTIVE_INVESTIGATION: {CaseStatus.UNDER_REVIEW, CaseStatus.SUBMITTED},
    CaseStatus.UNDER_REVIEW: {CaseStatus.ACTIVE_INVESTIGATION, CaseStatus.SUBMITTED},
    CaseStatus.SUBMITTED: {CaseStatus.UNDER_REVIEW, CaseStatus.CLOSED},
    CaseStatus.CLOSED: {CaseStatus.SEALED},
    CaseStatus.SEALED: set(),
}


async def update_status(
    session: AsyncSession, case: Case, status: CaseStatus, principal: Principal | None = None
) -> Case:
    current = CaseStatus(case.status.value if hasattr(case.status, "value") else case.status)
    if status == current:
        return case
    if status not in _CASE_TRANSITIONS.get(current, set()):
        raise ValidationFailedError(f"Invalid case transition: {current.value} -> {status.value}.")
    if status in {CaseStatus.CLOSED, CaseStatus.SEALED}:
        allowed = {Role.SUPERVISOR, Role.STATION_ADMIN, Role.DISTRICT_ADMIN, Role.SUPER_ADMIN, Role.ADMIN}
        if principal is not None and principal.role not in allowed:
            raise PermissionDeniedError("Supervisor approval is required for this case transition.")
    case.status = status
    if status in {CaseStatus.CLOSED, CaseStatus.SEALED}:
        case.closed_at = case.closed_at or utcnow()
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
