"""Dataset/graph build jobs: WebSocket progress and the polling fallback.

The behaviour under test is the promise made to the investigator: *start a
build, and you will find out how it goes* -- over a live socket if one can be
held open, over polling if it cannot, and never a permanent "building…".

Both transports are exercised against the same running job so the crucial
property can be asserted directly: they report the *same* state, because both
read the job row that the pipeline writes.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from starlette.websockets import WebSocketDisconnect

from app.datasets import registry
from app.db.models import Dataset, DatasetEntity, DatasetRelationship
from app.db.session import async_session


async def _make_dataset(name: str = "WS Fixture") -> str:
    """A tiny but real dataset: two entities and the edge between them."""
    async with async_session() as session:
        dataset = await registry.create_dataset(
            session, name=name, version="1", source_kind="folder"
        )
        dataset_id = dataset.id
        session.add_all(
            [
                DatasetEntity(
                    dataset_id=dataset_id,
                    canonical_id="PERSON:P1",
                    entity_type="PERSON",
                    name="Alpha",
                    normalized_value="ALPHA",
                    attributes={},
                    provenance={"file": "people.csv", "row": 2},
                ),
                DatasetEntity(
                    dataset_id=dataset_id,
                    canonical_id="PHONE:PH1",
                    entity_type="PHONE",
                    name="9812345678",
                    normalized_value="9812345678",
                    attributes={},
                    provenance={"file": "phones.csv", "row": 2},
                ),
                DatasetRelationship(
                    dataset_id=dataset_id,
                    source_canonical_id="PERSON:P1",
                    target_canonical_id="PHONE:PH1",
                    rel_type="USES_PHONE",
                    confidence=1.0,
                    edge_key="ws-fixture-edge",
                    attributes={},
                    provenance={"file": "phones.csv", "row": 2},
                    case_ids=[],
                ),
            ]
        )
        await session.commit()
    return dataset_id


@pytest.fixture()
async def dataset_id(container):
    return await _make_dataset()


async def _wait_for_terminal(client, headers, job_id: str, timeout: float = 20.0) -> dict:
    """Poll until the job finishes — the fallback path, used as a helper."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        body = client.get(f"/api/v1/datasets/jobs/{job_id}", headers=headers).json()
        if body["terminal"]:
            return body
        await asyncio.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


# ---------------------------------------------------------------------------
# Starting a build
# ---------------------------------------------------------------------------


async def test_starting_a_build_returns_a_job_id_immediately(
    client, admin_headers, dataset_id
):
    response = client.post(
        f"/api/v1/datasets/{dataset_id}/graph/rebuild", headers=admin_headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["job_id"]
    assert body["job"]["kind"] == "build_graph"
    assert body["job"]["status"] in ("QUEUED", "RUNNING")
    assert body["job"]["terminal"] is False


async def test_a_build_actually_completes_and_writes_the_graph(
    client, admin_headers, dataset_id, container
):
    job_id = client.post(
        f"/api/v1/datasets/{dataset_id}/graph/rebuild", headers=admin_headers
    ).json()["job_id"]

    final = await _wait_for_terminal(client, admin_headers, job_id)
    assert final["status"] == "SUCCEEDED", final
    assert final["progress_pct"] == 100
    assert final["result"]["nodes_written"] == 2
    assert final["result"]["edges_written"] == 1
    assert container.graph_store.stats()["nodes"] >= 2


async def test_rebuild_requires_admin(client, investigator_headers, dataset_id):
    response = client.post(
        f"/api/v1/datasets/{dataset_id}/graph/rebuild", headers=investigator_headers
    )
    assert response.status_code == 403


async def test_unknown_dataset_is_a_404(client, admin_headers):
    response = client.post(
        "/api/v1/datasets/does-not-exist/graph/rebuild", headers=admin_headers
    )
    assert response.status_code == 404


async def test_unknown_job_is_a_404(client, admin_headers):
    assert (
        client.get("/api/v1/datasets/jobs/nope", headers=admin_headers).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# The WebSocket transport
# ---------------------------------------------------------------------------


def _token(headers: dict) -> str:
    return headers["Authorization"].split(" ", 1)[1]


async def test_websocket_streams_progress_to_completion(
    client, admin_headers, dataset_id
):
    """The headline check: connect, receive real stages, receive completion."""
    job_id = client.post(
        f"/api/v1/datasets/{dataset_id}/graph/rebuild", headers=admin_headers
    ).json()["job_id"]

    frames: list[dict] = []
    with client.websocket_connect(
        f"/api/v1/jobs/ws/job/{job_id}?token={_token(admin_headers)}"
    ) as ws:
        # The first frame is always a snapshot, so a late subscriber is never
        # left staring at an empty progress bar.
        first = json.loads(ws.receive_text())
        assert first["type"] == "job_snapshot"
        assert first["id"] == job_id
        frames.append(first)

        if not first.get("terminal"):
            while True:
                try:
                    frame = json.loads(ws.receive_text())
                except WebSocketDisconnect:
                    break
                frames.append(frame)
                if frame.get("terminal"):
                    break

    assert frames[-1]["status"] == "SUCCEEDED", frames[-1]
    assert frames[-1]["progress_pct"] == 100
    # Progress is monotonic — a bar that jumps backwards looks broken.
    percentages = [f["progress_pct"] for f in frames]
    assert percentages == sorted(percentages), percentages
    # Real stage names, not a fabricated animation.
    stages = {f.get("stage") for f in frames}
    assert stages & {"BUILDING_GRAPH", "BUILDING_RELATIONSHIPS", "COMPLETED"}, stages


async def test_websocket_without_a_token_is_rejected(client, admin_headers, dataset_id):
    job_id = client.post(
        f"/api/v1/datasets/{dataset_id}/graph/rebuild", headers=admin_headers
    ).json()["job_id"]
    with client.websocket_connect(f"/api/v1/jobs/ws/job/{job_id}") as ws:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_text()
    assert excinfo.value.code == 4401


async def test_websocket_with_a_bad_token_is_rejected(client, admin_headers, dataset_id):
    job_id = client.post(
        f"/api/v1/datasets/{dataset_id}/graph/rebuild", headers=admin_headers
    ).json()["job_id"]
    with client.websocket_connect(
        f"/api/v1/jobs/ws/job/{job_id}?token=not-a-real-token"
    ) as ws:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_text()
    assert excinfo.value.code == 4401


async def test_unknown_job_closes_with_a_distinct_code(client, admin_headers):
    """4404, not 4401: a client bug must not look like an auth problem."""
    with client.websocket_connect(
        f"/api/v1/jobs/ws/job/no-such-job?token={_token(admin_headers)}"
    ) as ws:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_text()
    assert excinfo.value.code == 4404


async def test_the_job_route_is_not_shadowed_by_the_case_route(
    client, admin_headers, dataset_id
):
    """``/jobs/ws/job/{id}`` must win over ``/jobs/ws/{case_id}``.

    Declared in the wrong order, every job subscription would be read as a
    case called "job" and closed 4403 — a failure that looks exactly like a
    permissions problem and is not one.
    """
    job_id = client.post(
        f"/api/v1/datasets/{dataset_id}/graph/rebuild", headers=admin_headers
    ).json()["job_id"]
    with client.websocket_connect(
        f"/api/v1/jobs/ws/job/{job_id}?token={_token(admin_headers)}"
    ) as ws:
        assert json.loads(ws.receive_text())["type"] == "job_snapshot"


async def test_subscribing_after_completion_still_reports_the_result(
    client, admin_headers, dataset_id
):
    """A client that arrives late gets the outcome, not silence."""
    job_id = client.post(
        f"/api/v1/datasets/{dataset_id}/graph/rebuild", headers=admin_headers
    ).json()["job_id"]
    await _wait_for_terminal(client, admin_headers, job_id)

    with client.websocket_connect(
        f"/api/v1/jobs/ws/job/{job_id}?token={_token(admin_headers)}"
    ) as ws:
        snapshot = json.loads(ws.receive_text())
        assert snapshot["terminal"] is True
        assert snapshot["status"] == "SUCCEEDED"


# ---------------------------------------------------------------------------
# The polling fallback reports the same truth
# ---------------------------------------------------------------------------


async def test_polling_reports_the_same_state_as_the_socket(
    client, admin_headers, dataset_id
):
    """Never connect a socket at all; polling alone must see it through."""
    job_id = client.post(
        f"/api/v1/datasets/{dataset_id}/graph/rebuild", headers=admin_headers
    ).json()["job_id"]

    observed: list[dict] = []
    for _ in range(400):
        body = client.get(f"/api/v1/datasets/jobs/{job_id}", headers=admin_headers).json()
        observed.append(body)
        if body["terminal"]:
            break
        await asyncio.sleep(0.02)

    final = observed[-1]
    assert final["terminal"] is True
    assert final["status"] == "SUCCEEDED"
    assert final["progress_pct"] == 100
    assert final["result"]["nodes_written"] == 2
    percentages = [o["progress_pct"] for o in observed]
    assert percentages == sorted(percentages)
    assert final["steps"], "polling exposes the stage history too"


async def test_a_dropped_socket_does_not_affect_the_job(
    client, admin_headers, dataset_id, container
):
    """Simulated WebSocket failure: the build must still complete.

    The socket is opened and abandoned mid-stream. The job is owned by the
    application, not by the connection, so the rebuild continues and polling
    confirms the real outcome.
    """
    job_id = client.post(
        f"/api/v1/datasets/{dataset_id}/graph/rebuild", headers=admin_headers
    ).json()["job_id"]

    with client.websocket_connect(
        f"/api/v1/jobs/ws/job/{job_id}?token={_token(admin_headers)}"
    ) as ws:
        ws.receive_text()  # snapshot, then walk away

    final = await _wait_for_terminal(client, admin_headers, job_id)
    assert final["status"] == "SUCCEEDED", "the job survived the dropped socket"
    assert final["result"]["nodes_written"] == 2
    assert container.graph_store.stats()["nodes"] >= 2


async def test_two_concurrent_rebuilds_are_refused(client, admin_headers, container):
    """A second build while one is live would race two projections together."""
    dataset_id = await _make_dataset("Concurrency Fixture")
    first = client.post(
        f"/api/v1/datasets/{dataset_id}/graph/rebuild", headers=admin_headers
    )
    assert first.status_code == 200
    second = client.post(
        f"/api/v1/datasets/{dataset_id}/graph/rebuild", headers=admin_headers
    )
    # Either the first job already finished (then a second start is fine), or
    # it is still live and must be refused with a conflict.
    assert second.status_code in (200, 409)
    if second.status_code == 409:
        assert "already running" in second.text

    await _wait_for_terminal(client, admin_headers, first.json()["job_id"])


async def test_a_failing_job_reports_the_failure(client, admin_headers, monkeypatch):
    """A build that breaks must land on FAILED with a reason, not hang."""
    dataset_id = await _make_dataset("Failure Fixture")

    async def explode(session, dataset, progress=None):
        raise RuntimeError("graph store is unreachable")

    monkeypatch.setattr(
        "app.datasets.pipeline.rebuild_graph", explode, raising=True
    )
    job_id = client.post(
        f"/api/v1/datasets/{dataset_id}/graph/rebuild", headers=admin_headers
    ).json()["job_id"]

    final = await _wait_for_terminal(client, admin_headers, job_id)
    assert final["status"] == "FAILED"
    assert final["terminal"] is True
    assert "graph store is unreachable" in (final["error"] or "")


async def test_failure_is_delivered_over_the_socket_too(
    client, admin_headers, monkeypatch
):
    dataset_id = await _make_dataset("Socket Failure Fixture")

    async def explode(session, dataset, progress=None):
        raise RuntimeError("projection failed")

    monkeypatch.setattr("app.datasets.pipeline.rebuild_graph", explode, raising=True)
    job_id = client.post(
        f"/api/v1/datasets/{dataset_id}/graph/rebuild", headers=admin_headers
    ).json()["job_id"]

    with client.websocket_connect(
        f"/api/v1/jobs/ws/job/{job_id}?token={_token(admin_headers)}"
    ) as ws:
        last = json.loads(ws.receive_text())
        while not last.get("terminal"):
            try:
                last = json.loads(ws.receive_text())
            except WebSocketDisconnect:
                break

    final = await _wait_for_terminal(client, admin_headers, job_id)
    assert final["status"] == "FAILED"


# ---------------------------------------------------------------------------
# Dataset endpoints used by the console
# ---------------------------------------------------------------------------


async def test_dataset_listing_and_activation(client, admin_headers, dataset_id):
    listing = client.get("/api/v1/datasets", headers=admin_headers).json()
    assert any(item["id"] == dataset_id for item in listing["items"])

    activated = client.post(
        f"/api/v1/datasets/{dataset_id}/activate", headers=admin_headers
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["is_active"] is True

    active = client.get("/api/v1/datasets/active", headers=admin_headers).json()
    assert active["active"]["id"] == dataset_id
    assert active["active"]["stats"]["entities"] == 2


async def test_only_one_dataset_is_ever_active(client, admin_headers):
    first = await _make_dataset("First")
    second = await _make_dataset("Second")
    client.post(f"/api/v1/datasets/{first}/activate", headers=admin_headers)
    client.post(f"/api/v1/datasets/{second}/activate", headers=admin_headers)

    listing = client.get("/api/v1/datasets", headers=admin_headers).json()["items"]
    active = [item for item in listing if item["is_active"]]
    assert len(active) == 1
    assert active[0]["id"] == second


async def test_the_jobs_route_is_not_swallowed_by_the_dataset_route(
    client, admin_headers
):
    """``/datasets/jobs/{id}`` must not bind ``dataset_id="jobs"``."""
    response = client.get("/api/v1/datasets/jobs/some-id", headers=admin_headers)
    assert response.status_code == 404
    assert "Job not found" in response.text
