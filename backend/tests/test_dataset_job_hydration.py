"""A running import belongs to the deployment, not to the tab that started it.

The job row is written before the work starts and updated at every stage, so a
console that mounts with no local memory of the job can ask the database what
is running and re-attach.  Two consequences are asserted here:

* a browser refresh (or a trip to another page) can always recover the live
  job, and cannot start a second import on top of it;
* a job whose process died with the last restart is reported as interrupted
  instead of sitting at ``RUNNING`` forever and blocking every later import.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.datasets import registry
from app.db.models import DatasetJob
from app.db.session import async_session
from app.services import dataset_jobs


@pytest.fixture(autouse=True)
async def _no_lingering_jobs():
    """No test may leave a live job row behind for the rest of the session.

    The suite shares one database, and a queued import is *supposed* to block
    the next import -- so a leftover row here would fail an import somewhere
    else for the wrong reason.
    """
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


async def _put_job(status: str = "QUEUED", kind: str = "import") -> str:
    async with async_session() as session:
        job = await registry.create_job(
            session, kind=kind, dataset_id=None, requested_by=None
        )
        job.status = status
        job.stage = status
        await session.commit()
        return job.id


async def _finish(job_id: str, status: str = "SUCCEEDED") -> None:
    async with async_session() as session:
        job = await session.get(DatasetJob, job_id)
        job.status = status
        job.stage = status
        await session.commit()


async def test_current_job_is_the_one_that_is_running(client, admin_headers, db):
    job_id = await _put_job()
    body = client.get("/api/v1/datasets/jobs/current", headers=admin_headers).json()
    assert body["job"] is not None
    assert body["job"]["id"] == job_id
    assert body["job"]["terminal"] is False


async def test_a_finished_job_is_still_returned_but_not_as_running(
    client, admin_headers, db
):
    """A finished import must survive navigation and refresh.

    The console used to go idle the moment a job ended, because this endpoint
    only ever reported *running* jobs -- so an operator who came back after a
    completed (or failed) import saw an empty panel, read it as "nothing
    happened", and started the same import again.  The finished job is now
    returned with ``running: false``: the state persists, and the client still
    knows there is nothing to watch.
    """
    job_id = await _put_job()
    await _finish(job_id)
    body = client.get("/api/v1/datasets/jobs/current", headers=admin_headers).json()
    assert body["running"] is False
    assert body["job"] is not None
    assert body["job"]["id"] == job_id
    assert body["job"]["terminal"] is True
    assert body["job"]["status"] == "SUCCEEDED"
    # ...and it is still not offered as a reason to refuse the next import.
    assert await dataset_jobs.current_job() is None


async def test_a_second_import_is_refused_with_the_running_job_id(
    client, admin_headers, db
):
    """Starting a second import would retire the first one's dataset mid-flight."""
    job_id = await _put_job()
    response = client.post(
        "/api/v1/datasets/import",
        headers=admin_headers,
        files=[("files", ("people.csv", "person_id,full_name\nP001,Meera Kurian\n", "text/csv"))],
        data={"name": "Second import"},
    )
    assert response.status_code == 409, response.text
    # The client is told which job to attach to, so "refused" is actionable.
    assert job_id in response.text


async def test_a_job_whose_process_died_is_reported_as_interrupted(db):
    """A restart must not leave a RUNNING row that blocks every later import."""
    job_id = await _put_job(status="RUNNING")
    current = await dataset_jobs.current_job()
    assert current is not None
    assert current["id"] == job_id
    assert current["status"] == "FAILED"
    assert current["error"] == "interrupted_by_restart"

    # And the queue is clear: nothing continues to claim to be running.
    assert await dataset_jobs.current_job() is None


async def test_the_endpoint_carries_the_same_payload_as_polling(
    client, admin_headers, db
):
    """Hydration and polling must not disagree about the same job."""
    job_id = await _put_job()
    hydrated = client.get(
        "/api/v1/datasets/jobs/current", headers=admin_headers
    ).json()["job"]
    polled = client.get(
        f"/api/v1/datasets/jobs/{job_id}", headers=admin_headers
    ).json()
    assert hydrated == polled


@pytest.mark.parametrize("kind", ["build_graph", "activate"])
async def test_only_import_jobs_block_another_import(kind, client, admin_headers, db):
    """A rebuild is not an import: hydration must not hide the import route."""
    await _put_job(kind=kind)
    body = client.get("/api/v1/datasets/jobs/current", headers=admin_headers).json()
    assert body["job"] is not None
    assert body["job"]["kind"] == kind

    response = client.post(
        "/api/v1/datasets/import",
        headers=admin_headers,
        files=[("files", ("people.csv", "person_id,full_name\nP001,Meera Kurian\n", "text/csv"))],
        data={"name": "Import beside a rebuild"},
    )
    assert response.status_code == 200, response.text
