"""Regression tests for ``GET /api/v1/cases/{case_id}/dashboard``.

The reported defect was a hard 500 on ten of the twenty-five seeded v2 cases::

    File "app/services/case_dashboard.py", line 173, in get_case_dashboard
      pat_timestamp_attr = "detected_at" if hasattr(patterns[0], "detected_at") and patterns else "created_at"
                                                        ~~~~~~~~^^^
    IndexError: list index out of range

``hasattr(patterns[0], ...)`` was evaluated *before* the truthiness guard on
``patterns``, so any case with **no detected patterns** — the normal state of
a freshly opened case, and of most of the demo corpus — raised instead of
returning a dashboard.  The variable it assigned was read nowhere else in the
function, so the line did nothing except crash.  The console showed the
failure as "Could not load this case / Request could not be completed." with
no way to tell a genuine outage from an empty review queue.

These tests pin the contract the console depends on:

* a case with no patterns, findings, documents or graph loads with honest
  zero counts rather than an error or invented data;
* a case that *does* have activity still reports it;
* ``last_activity_at`` is derived from the case's own records, never a
  placeholder;
* an unhandled 500 carries structured diagnostics in a development
  deployment, and stays opaque in production.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.datasets import registry
from app.db.base import new_uuid, utcnow
from app.db.models import (
    Case,
    CaseDocument,
    Dataset,
    DetectedPattern,
    InvestigationFinding,
)
from app.db.session import async_session
from app.domain.enums import (
    CaseStatus,
    DocumentType,
    PatternStatus,
    PatternType,
)

JURISDICTION = "RJ-JAIPUR"

#: Set by the activity fixture, read back by its test (Case has no spare JSON
#: column to smuggle an expected value through, and hardcoding a timestamp in
#: the assertion would just restate the fixture).
_EXPECTED_LAST_ACTIVITY: dict[str, str] = {}


@pytest.fixture()
async def dashboard_case(request):
    """An activated dataset holding one case, cleaned up afterwards.

    The parameter is a callable that receives ``(session, case)`` so each test
    can add exactly the rows its scenario needs.
    """
    populate = getattr(request, "param", None)
    async with async_session() as session:
        dataset = await registry.create_dataset(
            session, name="Dashboard fixtures", version="1", source_kind="builtin"
        )
        await registry.activate(session, dataset)
        case = Case(
            id=new_uuid(),
            case_number=f"DASH-{new_uuid()[:8]}",
            title="Dashboard fixture",
            jurisdiction_id=JURISDICTION,
            dataset_id=dataset.id,
            status=CaseStatus.OPEN,
        )
        session.add(case)
        await session.flush()
        if populate is not None:
            await populate(session, case)
        await session.commit()
        context = {"case_id": case.id, "dataset_id": dataset.id}

    yield context

    await _cleanup(context["dataset_id"])


async def _cleanup(dataset_id: str) -> None:
    """Remove the fixture rows; the session-scoped database outlives one test."""
    async with async_session() as session:
        case_ids = [
            row
            for row in (
                await session.execute(
                    select(Case.id).where(Case.dataset_id == dataset_id)
                )
            ).scalars()
        ]
        if case_ids:
            for model in (DetectedPattern, InvestigationFinding, CaseDocument):
                for row in (
                    await session.execute(
                        select(model).where(model.case_id.in_(case_ids))
                    )
                ).scalars():
                    await session.delete(row)
            for row in (
                await session.execute(select(Case).where(Case.id.in_(case_ids)))
            ).scalars():
                await session.delete(row)
        for row in (
            await session.execute(select(CaseDocument).where(CaseDocument.dataset_id == dataset_id))
        ).scalars():
            await session.delete(row)
        dataset = await session.get(Dataset, dataset_id)
        if dataset is not None:
            await session.delete(dataset)
        await session.commit()


# --------------------------------------------------------------------------- #
# The exact reported failure: no detected patterns
# --------------------------------------------------------------------------- #

async def test_dashboard_loads_for_a_case_with_no_detected_patterns(
    client, investigator_headers, dashboard_case
):
    """An empty review queue is a *state*, not an error.

    ``case-d2-024`` and nine other seeded v2 cases have zero rows in
    ``detected_patterns``; every one of them answered 500 before the fix.
    """
    resp = client.get(
        f"/api/v1/cases/{dashboard_case['case_id']}/dashboard", headers=investigator_headers
    )
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["header"]["id"] == dashboard_case["case_id"]
    assert body["header"]["dataset_id"] == dashboard_case["dataset_id"]
    # Honest zeros — nothing invented to fill the page.
    assert body["stats"]["patterns"] == 0
    assert body["stats"]["documents"] == 0
    assert body["stats"]["findings"] == 0
    assert body["stats"]["relationships"] == 0
    assert body["intelligence"]["patterns"] == []
    assert body["intelligence"]["high_priority"] == []
    assert body["intelligence"]["recent_activity"] == []
    # A case with no records has no last activity; it must say so.
    assert body["header"]["last_activity_at"] is None


async def test_dashboard_reports_an_unknown_case_as_404_not_500(
    client, investigator_headers
):
    resp = client.get("/api/v1/cases/no-such-case/dashboard", headers=investigator_headers)
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "not_found"


# --------------------------------------------------------------------------- #
# A case that does have activity still reports it
# --------------------------------------------------------------------------- #

async def _add_patterns(session, case, count: int) -> None:
    base = utcnow()
    for index in range(count):
        session.add(
            DetectedPattern(
                id=f"pat-{case.id}-{index}",
                case_id=case.id,
                pattern_type=PatternType.STRUCTURING,
                confidence=0.9,
                entity_keys=["a", "b"],
                evidence_doc_ids=[],
                explanation=f"pattern {index}",
                details={},
                status=PatternStatus.NEW,
                detected_at=base,
            )
        )


@pytest.mark.parametrize(
    "dashboard_case",
    [lambda session, case: _add_patterns(session, case, 6)],
    indirect=True,
)
async def test_dashboard_recent_activity_survives_a_mixed_pattern_set(
    client, investigator_headers, dashboard_case
):
    """Patterns sort by ``detected_at`` without mixing datetime and str keys."""
    resp = client.get(
        f"/api/v1/cases/{dashboard_case['case_id']}/dashboard", headers=investigator_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["stats"]["patterns"] == 6
    assert len(body["intelligence"]["recent_activity"]) == 5
    assert {a["type"] for a in body["intelligence"]["recent_activity"]} == {"pattern"}


# --------------------------------------------------------------------------- #
# last_activity_at is derived, not stamped
# --------------------------------------------------------------------------- #

async def _add_activity(session, case) -> None:
    newest = utcnow()
    session.add(
        CaseDocument(
            id=f"doc-{case.id}-act",
            case_id=case.id,
            dataset_id=case.dataset_id,
            document_type=DocumentType.FIR,
            filename="earlier.pdf",
            storage_key=f"evidence/{case.case_number}/earlier.pdf",
            content_hash="0" * 64,
            size_bytes=10,
            created_at=newest,
        )
    )
    session.add(
        InvestigationFinding(
            id=f"find-{case.id}-act",
            case_id=case.id,
            finding_type="CHARGE",
            title="Charge sheet filed",
            narrative="Newest recorded activity on this case.",
            reason="Documented in the case diary.",
            confidence=0.8,
            created_at=newest,
        )
    )
    _EXPECTED_LAST_ACTIVITY[case.id] = newest.isoformat()


@pytest.mark.parametrize("dashboard_case", [_add_activity], indirect=True)
async def test_last_activity_comes_from_the_newest_real_record(
    client, investigator_headers, dashboard_case
):
    resp = client.get(
        f"/api/v1/cases/{dashboard_case['case_id']}/dashboard", headers=investigator_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    expected = _EXPECTED_LAST_ACTIVITY.pop(dashboard_case["case_id"])
    assert body["header"]["last_activity_at"] == expected
    assert body["stats"]["documents"] == 1
    assert body["stats"]["findings"] == 1


# --------------------------------------------------------------------------- #
# Structured diagnostics when a 500 does happen
# --------------------------------------------------------------------------- #

def test_unhandled_error_names_the_exception_in_development(app, investigator_headers):
    """A developer must be able to see *what* failed from the response alone."""
    from fastapi.testclient import TestClient

    from app.services import case_dashboard as dashboard_service

    def _boom(*_args, **_kwargs):
        raise RuntimeError("diagnostics must reach the developer")

    original = dashboard_service.get_case_dashboard
    dashboard_service.get_case_dashboard = _boom
    # ``raise_server_exceptions=False`` so the real handler runs and answers,
    # exactly as uvicorn would in a live deployment.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        try:
            resp = test_client.get(
                "/api/v1/cases/does-not-exist/dashboard", headers=investigator_headers
            )
        finally:
            dashboard_service.get_case_dashboard = original

    assert resp.status_code == 500, resp.text
    error = resp.json()["error"]
    assert error["code"] == "internal_error"
    assert error["message"] == "Request could not be completed."
    assert error["trace_id"]
    assert error["exception"] == "RuntimeError"
    # The frame reported is the innermost *application* frame — the route that
    # called the failing service — not a library or test-helper frame.
    assert error["location"]["function"] == "case_dashboard"
    assert error["location"]["file"].endswith("app/api/v1/cases.py")


def test_unhandled_error_hides_internals_in_production(
    app, investigator_headers, monkeypatch
):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.services import case_dashboard as dashboard_service

    settings = get_settings()
    monkeypatch.setattr(
        type(settings), "is_production_deployment", property(lambda _self: True)
    )

    def _boom(*_args, **_kwargs):
        raise RuntimeError("internal detail")

    original = dashboard_service.get_case_dashboard
    dashboard_service.get_case_dashboard = _boom
    with TestClient(app, raise_server_exceptions=False) as test_client:
        try:
            resp = test_client.get(
                "/api/v1/cases/does-not-exist/dashboard", headers=investigator_headers
            )
        finally:
            dashboard_service.get_case_dashboard = original

    assert resp.status_code == 500, resp.text
    error = resp.json()["error"]
    assert "exception" not in error
    assert "location" not in error
