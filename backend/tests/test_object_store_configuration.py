"""A production deployment must say where its evidence lives, and say it once.

The failure this pins down is quiet: the process starts, the API answers, and
every document renders as "record unavailable" because the endpoint is a
Compose service name that only resolves inside a Docker network.  The
investigator then believes the evidence is gone.

So the endpoint is checked where it is used:

* the configuration names the variable that is wrong, and stays silent outside
  production (where ``minio:9000`` is correct);
* the container refuses to build a client that could only ever fail, with a
  503 that carries the actionable message instead of a confusing
  "object not found";
* the readiness endpoint reports the degradation as a health fact -- the
  request itself still succeeds, because a degraded dependency is not a reason
  to refuse every request.
"""

from __future__ import annotations

import time

import pytest

from app.config import Settings
from app.container import Container
from app.errors import DependencyUnavailableError

DURABLE_ENDPOINT = "s3.eu-west-1.amazonaws.com:443"
COMPOSE_ENDPOINT = "minio:9000"


def _production(**overrides) -> Settings:
    """Production settings with every fail-closed value supplied for real."""
    values: dict = {
        "profile": "production",
        "environment": "production",
        # What the deployment sets (see .env.vercel.example).  With ``auto`` a
        # process outside a container resolves to the host context, which
        # rewrites Compose names to localhost before this check runs.
        "runtime_context": "production",
        "debug": False,
        "secret_key": "test-only-secret-key-0123456789abcdef",
        "cors_origins": ["https://console.example.gov.in"],
        "neo4j_password": "test-only-neo4j-credential",
        "minio_secret_key": "test-only-object-store-credential",
        "postgres_dsn": (
            "postgresql+psycopg2://crimelink_app:test-only-password"
            "@db.example.net:5432/crimelink"
        ),
        "minio_endpoint": DURABLE_ENDPOINT,
    }
    values.update(overrides)
    return Settings(**values)


# --------------------------------------------------------------------------- #
# The configuration answer
# --------------------------------------------------------------------------- #


def test_a_compose_service_hostname_is_rejected_in_production():
    problem = _production(minio_endpoint=COMPOSE_ENDPOINT).object_store_endpoint_problem
    assert problem is not None
    assert "CRIMELINK_MINIO_ENDPOINT" in problem
    assert COMPOSE_ENDPOINT.split(":")[0] in problem
    assert "Docker Compose" in problem


def test_an_unset_endpoint_is_rejected_in_production():
    problem = _production(minio_endpoint="").object_store_endpoint_problem
    assert problem is not None
    assert "CRIMELINK_MINIO_ENDPOINT" in problem
    assert "not set" in problem


def test_a_durable_endpoint_is_accepted_in_production():
    assert _production().object_store_endpoint_problem is None


def test_the_compose_hostname_is_correct_outside_production():
    """Inside the Compose stack ``minio:9000`` *is* the right answer."""
    settings = Settings(profile="embedded", minio_endpoint=COMPOSE_ENDPOINT)
    assert settings.object_store_endpoint_problem is None


def test_a_host_machine_rewrites_the_compose_name_before_the_check():
    """``python run.py`` on a laptop talks to ``localhost:9000`` on purpose."""
    settings = _production(runtime_context="host", minio_endpoint=COMPOSE_ENDPOINT)
    assert settings.minio_endpoint.startswith("localhost"), settings.minio_endpoint
    assert settings.object_store_endpoint_problem is None


# --------------------------------------------------------------------------- #
# The container refuses to build a client that cannot work
# --------------------------------------------------------------------------- #


def test_the_container_refuses_a_client_it_knows_can_only_fail():
    container = Container(_production(minio_endpoint=COMPOSE_ENDPOINT))
    with pytest.raises(DependencyUnavailableError) as raised:
        container.object_store
    assert "CRIMELINK_MINIO_ENDPOINT" in str(raised.value)

    # Not cached: a second read states the problem again rather than handing
    # back a half-built client whose every call would fail.
    with pytest.raises(DependencyUnavailableError):
        container.object_store


def test_the_local_backend_is_not_affected_by_the_production_check(tmp_path):
    """A deployment that chooses local storage is a decision, not a mistake."""
    container = Container(
        _production(
            object_store_backend="local",
            data_dir=tmp_path / "data",
            object_store_dir=tmp_path / "objects",
        )
    )
    assert container.object_store.backend_name == "local"


# --------------------------------------------------------------------------- #
# A degraded dependency degrades readiness; it does not fail the request
# --------------------------------------------------------------------------- #


def _await_job(client, headers, job_id: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/v1/datasets/jobs/{job_id}", headers=headers).json()
        if body["terminal"]:
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def test_an_import_that_cannot_store_its_bytes_fails_instead_of_activating(
    client, admin_headers, container, monkeypatch
):
    """Evidence that cannot be stored must not become the active dataset.

    The import is refused with the storage error, the job ends FAILED for a
    real backend reason, and the half-built dataset never reaches the listing.
    """

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

    response = client.post(
        "/api/v1/datasets/import",
        headers=admin_headers,
        files=[
            (
                "files",
                ("people.csv", "person_id,full_name\nP001,Meera Kurian\n", "text/csv"),
            )
        ],
        data={"name": "Storage outage"},
    )
    assert response.status_code == 200, response.text

    final = _await_job(client, admin_headers, response.json()["job_id"])
    assert final["status"] == "FAILED"
    assert "storage unreachable" in (final["error"] or "")
    # The job says *where* it failed.  Storage is written during VALIDATING, so
    # that is the stage recorded -- not a completed run that broke afterwards.
    assert final["result"].get("failed_stage") == "VALIDATING"
    assert final["progress_pct"] < 100, "a failed import must not report 100%"

    listing = client.get("/api/v1/datasets", headers=admin_headers).json()
    listed = {item["id"] for item in listing.get("items", [])}
    assert final["dataset_id"] not in listed, (
        "a dataset whose evidence was never stored must not be offered as active"
    )


def test_readiness_reports_the_misconfiguration_without_failing(
    client, container, monkeypatch
):
    def _unavailable(_self):
        raise DependencyUnavailableError(
            "CRIMELINK_MINIO_ENDPOINT is set to the Docker Compose service "
            "hostname 'minio'; that name only resolves inside the Compose network."
        )

    monkeypatch.setattr(type(container), "object_store", property(_unavailable))

    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200, response.text
    check = response.json()["checks"]["object_store"]
    assert check["status"] == "error"
    # The error type is what an operator greps the logs for; the message that
    # names the variable is in the 503 the evidence endpoint returns.
    assert check["error"] == "DependencyUnavailableError"
