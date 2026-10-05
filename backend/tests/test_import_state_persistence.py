"""Import state is the backend's, and it survives everything the browser does.

A dataset import is a long-running backend operation, so none of its state may
depend on the lifetime of a React component.  These tests drive the real
endpoint the console hydrates from and assert that:

* the job row is written before the work starts and updated at every stage;
* navigating away and back, or refreshing, returns the same authoritative
  state -- running, completed *or failed*;
* a failed import reports the stage it actually reached, and never 100%;
* nothing about a client disconnecting can fail the job, and remounting cannot
  start a duplicate import;
* authorization is unchanged, and activation semantics are untouched.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.datasets import registry
from app.db.models import DatasetJob
from app.db.session import async_session
from app.services import dataset_jobs

PEOPLE_CSV = "person_id,full_name\nP001,Meera Kurian\nP002,Ajay Kumar\n"


@pytest.fixture(autouse=True)
async def _no_lingering_jobs():
    """No test may leave a live job row behind for the rest of the session."""
    yield
    async with async_session() as session:
        rows = (
            await session.execute(
                select(DatasetJob).where(DatasetJob.status.in_(["QUEUED", "RUNNING"]))
            )
        ).scalars().all()
        for row in rows:
            row.status = "CANCELLED"
            row.stage = "CANCELLED"
        await session.commit()


async def _job(job_id: str) -> dict:
    async with async_session() as session:
        row = await session.get(DatasetJob, job_id)
        return registry.job_row(row)


def _current(client, headers) -> dict:
    return client.get("/api/v1/datasets/jobs/current", headers=headers).json()


def _await_job(client, headers, job_id: str, timeout_s: float = 30.0) -> dict:
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = client.get(f"/api/v1/datasets/jobs/{job_id}", headers=headers).json()
        if state.get("terminal"):
            return state
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never reached a terminal state")


def _start_import(client, headers, name: str) -> str:
    response = client.post(
        "/api/v1/datasets/import",
        headers=headers,
        files=[("files", ("people.csv", PEOPLE_CSV, "text/csv"))],
        data={"name": name},
    )
    assert response.status_code == 200, response.text
    return response.json()["job_id"]


# --------------------------------------------------------------------------- #
# The job row is the authority
# --------------------------------------------------------------------------- #


async def test_an_import_creates_persisted_job_state(client, admin_headers, db):
    job_id = _start_import(client, admin_headers, "Persisted import")
    row = await _job(job_id)

    assert row["kind"] == "import"
    assert row["requested_by"] is None or isinstance(row["requested_by"], str)
    # Everything the console needs to reconstruct the panel is on the row, so
    # nothing has to be remembered by the tab that started it.
    for field in (
        "id",
        "kind",
        "status",
        "stage",
        "progress_pct",
        "message",
        "steps",
        "created_at",
        "updated_at",
        "terminal",
    ):
        assert field in row, field


async def test_progress_and_stage_are_persisted_as_the_import_runs(
    client, admin_headers, db
):
    job_id = _start_import(client, admin_headers, "Staged import")
    final = _await_job(client, admin_headers, job_id)
    row = await _job(job_id)

    assert final["status"] == "SUCCEEDED"
    assert row["progress_pct"] == 100
    stages = [step["stage"] for step in row["steps"]]
    # The real pipeline stages, in the order they happened, including the
    # verification pass that gates activation.
    for expected in ("VALIDATING", "NORMALIZING", "INGESTING", "VERIFYING", "READY"):
        assert expected in stages, (expected, stages)
    assert stages.index("VALIDATING") < stages.index("VERIFYING") < stages.index("READY")


async def test_the_current_endpoint_returns_the_running_import(client, admin_headers, db):
    job_id = _start_import(client, admin_headers, "Current import")
    body = _current(client, admin_headers)

    assert body["job"] is not None
    assert body["job"]["id"] == job_id
    # Either it is still running (and the console watches it) or it already
    # finished -- but it is never "nothing happened".
    assert body["running"] is (not body["job"]["terminal"])


async def test_a_completed_import_is_still_shown_after_a_refresh(
    client, admin_headers, db
):
    """Returning to Administration must show the completed state, not an idle panel."""
    job_id = _start_import(client, admin_headers, "Completed import")
    _await_job(client, admin_headers, job_id)

    # This is what a remounted console (navigation back, or F5) asks.
    body = _current(client, admin_headers)
    assert body["job"] is not None
    assert body["job"]["id"] == job_id
    assert body["job"]["status"] == "SUCCEEDED"
    assert body["job"]["progress_pct"] == 100
    assert body["running"] is False
    # The result summary the console renders is on the row too.
    assert body["job"]["result"]["cases_created"] >= 1


async def test_a_failed_import_stays_failed_with_its_real_error_and_stage(
    client, admin_headers, db, container, monkeypatch
):
    """A failure must survive a refresh, and say where it happened."""
    from app.errors import DependencyUnavailableError

    class Unreachable:
        backend_name = "unreachable"

        def get(self, *_args, **_kwargs):
            raise DependencyUnavailableError("storage unreachable (test)")

        def put(self, *_args, **_kwargs):
            raise DependencyUnavailableError("storage unreachable (test)")

        def list_keys(self, *_args, **_kwargs):
            return []

    job_id = _start_import(client, admin_headers, "Doomed import")
    monkeypatch.setattr(
        type(container), "object_store", property(lambda _self: Unreachable())
    )
    final = _await_job(client, admin_headers, job_id)

    assert final["status"] == "FAILED"
    assert "storage unreachable" in (final["error"] or "")
    # The stage the import actually reached, not a bare "FAILED" and not a
    # completed-looking 100%.
    assert final["result"].get("failed_stage") == "VALIDATING"
    assert final["progress_pct"] < 100

    # ...and a refresh still says so.
    body = _current(client, admin_headers)
    assert body["job"]["id"] == job_id
    assert body["job"]["status"] == "FAILED"
    assert body["job"]["progress_pct"] < 100
    assert body["running"] is False


async def test_a_failed_import_does_not_replace_the_active_dataset(
    client, admin_headers, db, container, monkeypatch
):
    from app.errors import DependencyUnavailableError

    first = _start_import(client, admin_headers, "Good dataset")
    _await_job(client, admin_headers, first)
    listing = client.get("/api/v1/datasets", headers=admin_headers).json()
    active_before = next(item for item in listing["items"] if item["is_active"])

    class Unreachable:
        backend_name = "unreachable"

        def get(self, *_args, **_kwargs):
            raise DependencyUnavailableError("storage unreachable (test)")

        def put(self, *_args, **_kwargs):
            raise DependencyUnavailableError("storage unreachable (test)")

        def list_keys(self, *_args, **_kwargs):
            return []

    monkeypatch.setattr(
        type(container), "object_store", property(lambda _self: Unreachable())
    )
    second = _start_import(client, admin_headers, "Broken replacement")
    assert _await_job(client, admin_headers, second)["status"] == "FAILED"

    listing = client.get("/api/v1/datasets", headers=admin_headers).json()
    active_after = next(item for item in listing["items"] if item["is_active"])
    assert active_after["id"] == active_before["id"], (
        "a replacement that could not store its evidence must not take over"
    )


# --------------------------------------------------------------------------- #
# The client cannot break the job, and cannot duplicate it
# --------------------------------------------------------------------------- #


async def test_a_running_import_survives_the_client_going_away(client, admin_headers, db):
    """Unmounting the console cancels nothing: the job is owned by the app."""
    job_id = _start_import(client, admin_headers, "Survives unmount")
    # The client "navigates away": no watch, no polling.
    final = _await_job(client, admin_headers, job_id)
    assert final["status"] == "SUCCEEDED"
    assert final["result"]["cases_created"] >= 1


async def test_remounting_does_not_start_a_second_import(client, admin_headers, db):
    """A console that re-attaches instead of re-uploading cannot race itself."""
    job_id = await _queue_import_job()
    response = client.post(
        "/api/v1/datasets/import",
        headers=admin_headers,
        files=[("files", ("people.csv", PEOPLE_CSV, "text/csv"))],
        data={"name": "Duplicate import"},
    )
    assert response.status_code == 409, response.text
    assert job_id in response.text, "the client is told which job to attach to"


async def test_a_client_that_cannot_reach_the_api_does_not_fail_the_job(
    client, admin_headers, db
):
    """Only the backend job can decide that an import failed.

    A dropped socket, a closed tab or a refused request is a client event.  The
    job row is untouched by all of them, and the next successful lookup returns
    exactly the state the job is really in.
    """
    job_id = await _queue_import_job(status="QUEUED")

    # A request that never reaches an authorized handler changes nothing.
    assert client.get("/api/v1/datasets/jobs/current").status_code in (401, 403)
    assert (await _job(job_id))["status"] == "QUEUED"

    body = _current(client, admin_headers)
    assert body["job"]["id"] == job_id
    assert body["running"] is True
    assert (await _job(job_id))["status"] == "QUEUED"


async def test_a_job_orphaned_by_a_restart_is_reported_not_resurrected(
    client, admin_headers, db
):
    """A RUNNING row whose process is gone is a fact, and is reported as one."""
    job_id = await _queue_import_job(status="RUNNING")
    body = _current(client, admin_headers)
    assert body["job"]["id"] == job_id
    assert body["job"]["status"] == "FAILED"
    assert body["job"]["error"] == "interrupted_by_restart"
    assert body["running"] is False


# --------------------------------------------------------------------------- #
# Authorization
# --------------------------------------------------------------------------- #


async def test_the_current_job_endpoint_requires_administration(
    client, investigator_headers, viewer_headers, db
):
    await _queue_import_job()
    for headers in (investigator_headers, viewer_headers):
        assert client.get("/api/v1/datasets/jobs/current", headers=headers).status_code == 403
    assert client.get("/api/v1/datasets/jobs/current").status_code in (401, 403)


async def test_a_job_is_readable_by_its_id_with_the_same_payload(
    client, admin_headers, db
):
    job_id = await _queue_import_job()
    hydrated = _current(client, admin_headers)["job"]
    polled = client.get(f"/api/v1/datasets/jobs/{job_id}", headers=admin_headers).json()
    assert hydrated == polled, "hydration and polling must not disagree"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


async def _queue_import_job(status: str = "QUEUED") -> str:
    """A job row in the given state, without running any import."""
    async with async_session() as session:
        job = await registry.create_job(
            session, kind="import", dataset_id=None, requested_by=None
        )
        job.status = status
        job.stage = status
        await session.commit()
        return job.id


async def test_activation_semantics_are_unchanged(client, admin_headers, db):
    """A successful import still activates exactly one dataset."""
    job_id = _start_import(client, admin_headers, "Activating import")
    final = _await_job(client, admin_headers, job_id)
    assert final["status"] == "SUCCEEDED"

    listing = client.get("/api/v1/datasets", headers=admin_headers).json()
    active = [item for item in listing["items"] if item["is_active"]]
    assert len(active) == 1
    assert active[0]["id"] == final["dataset_id"]
    # The job is bound to the dataset it built, so the console can link to it.
    assert (await _job(job_id))["dataset_id"] == final["dataset_id"]
    assert await dataset_jobs.current_job() is None
