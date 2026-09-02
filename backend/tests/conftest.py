"""Shared fixtures for the CrimeLink test suite.

Everything runs on the **embedded** profile: SQLite + NetworkX + the local
filesystem + the in-process broker.  No containers, no network — the same
domain, pipeline, analytics and API code that runs on PostgreSQL/Neo4j/MinIO
in production, because those are adapters and only the adapters change.

Every test gets its own graph file and object-store directory, so tests cannot
leak state into one another.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Iterator

import pytest

# Point the embedded profile at a throwaway directory before anything imports
# Settings, so no test can ever write into the repository's var/ directory.
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="crimelink-tests-"))
os.environ["CRIMELINK_PROFILE"] = "embedded"
os.environ["CRIMELINK_DATA_DIR"] = str(_TMP_ROOT / "data")
os.environ["CRIMELINK_OBJECT_STORE_DIR"] = str(_TMP_ROOT / "objects")
os.environ["CRIMELINK_LOG_LEVEL"] = "WARNING"
# The suite hammers the API; production brute-force limits would trip on it.
os.environ["CRIMELINK_RATE_LIMIT_PER_MINUTE"] = "100000"
os.environ["CRIMELINK_RATE_LIMIT_AUTH_PER_MINUTE"] = "100000"

from app.config import Settings, reload_settings  # noqa: E402
from app.container import Container, set_container  # noqa: E402
from app.db.session import (  # noqa: E402
    async_session,
    configure_for_tests,
    dispose_engines,
    init_db,
)

TEST_DB_URL = f"sqlite+aiosqlite:///{_TMP_ROOT / 'test.db'}"
configure_for_tests(TEST_DB_URL)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return reload_settings()


@pytest.fixture(scope="session", autouse=True)
def _bootstrap(settings: Settings) -> Iterator[None]:
    """Create the schema once per session and tear the engines down after."""
    import asyncio

    asyncio.run(init_db())
    yield
    asyncio.run(dispose_engines())


@pytest.fixture()
def workspace(tmp_path: Path, settings: Settings) -> Settings:
    """A Settings copy whose graph file and object store live in tmp_path."""
    isolated = settings.model_copy(
        update={
            "data_dir": tmp_path / "data",
            "object_store_dir": tmp_path / "objects",
            "graph_snapshot_path": tmp_path / "graph.json",
        }
    )
    isolated.ensure_directories()
    return isolated


class RecordingBroker:
    """Stands in for Celery / the in-process pool.

    Dispatching is recorded instead of executed so a test can run the pipeline
    deterministically, in the test's own thread, and assert on the result.
    """

    backend_name = "recording"

    def __init__(self) -> None:
        self.dispatched: list[dict] = []

    def dispatch_document_pipeline(self, **kwargs) -> None:
        self.dispatched.append(kwargs)

    def dispatch_nightly_patterns(self, *, trace_id: str) -> None:
        self.dispatched.append({"kind": "nightly", "trace_id": trace_id})

    def dispatch_audit_anchor(self, *, trace_id: str) -> None:
        self.dispatched.append({"kind": "anchor", "trace_id": trace_id})

    def health(self) -> dict:
        return {"backend": self.backend_name, "pending_jobs": len(self.dispatched), "alive": True}

    def drain(self) -> int:
        """Run everything that has been dispatched, once, synchronously."""
        from app.pipeline.orchestrator import (
            process_document,
            run_audit_anchor,
            run_nightly_patterns,
        )

        pending, self.dispatched = self.dispatched, []
        for call in pending:
            if call.get("kind") == "nightly":
                run_nightly_patterns(trace_id=call["trace_id"])
            elif call.get("kind") == "anchor":
                run_audit_anchor(trace_id=call["trace_id"])
            else:
                process_document(**call)
        return len(pending)


@pytest.fixture()
def container(workspace: Settings) -> Iterator[Container]:
    from app.adapters.graph.embedded import EmbeddedGraphStore

    box = Container(workspace)
    # Resolve the graph lazily but against the isolated snapshot file.
    box._graph_store = EmbeddedGraphStore(workspace)
    box._broker = RecordingBroker()
    set_container(box)
    yield box
    box.reset()


@pytest.fixture()
def graph(container: Container):
    return container.graph_store


@pytest.fixture()
def broker(container: Container) -> RecordingBroker:
    return container.broker


@pytest.fixture()
def store(container: Container):
    return container.object_store


@pytest.fixture()
async def db() -> Any:
    async with async_session() as session:
        yield session


# --------------------------------------------------------------------------- #
# People and cases
# --------------------------------------------------------------------------- #

PASSWORD = "CrimeLink@Inv1"


def _make_user(badge: str, name: str, role: str, jurisdiction: str) -> Any:
    from app.db.base import new_uuid
    from app.db.models import User
    from app.db.session import sync_session
    from app.domain.enums import Role
    from app.security.passwords import hash_password

    with sync_session() as session:
        existing = (
            session.query(User).filter(User.badge_number == badge).one_or_none()
        )
        if existing is not None:
            return existing
        user = User(
            id=new_uuid(),
            badge_number=badge,
            full_name=name,
            hashed_password=hash_password(PASSWORD),
            role=Role(role),
            station_id=f"PS-{jurisdiction.split('-')[-1]}",
            jurisdiction_id=jurisdiction,
        )
        session.add(user)
        session.flush()
        # Detach: callers use these rows from other sessions.
        session.expunge(user)
        return user


@pytest.fixture(scope="session")
def users() -> dict[str, Any]:
    """The four test roles, created once per session with the sync session."""
    return {
        "ADM-0001": _make_user("ADM-0001", "Admin Rao", "ADMIN", "RJ-JAIPUR"),
        "INV-0001": _make_user("INV-0001", "Inspector Sharma", "INVESTIGATOR", "RJ-JAIPUR"),
        "VIW-0001": _make_user("VIW-0001", "Constable Verma", "VIEWER", "RJ-JAIPUR"),
        "INV-0002": _make_user("INV-0002", "Inspector Kota", "INVESTIGATOR", "RJ-KOTA"),
    }


async def _make_case(session, owner, number: str, jurisdiction: str) -> Any:
    import uuid

    from app.db.base import new_uuid
    from app.db.models import Case
    from app.domain.enums import CaseStatus

    # A unique case number per test keeps one test's rows out of another's.
    number = f"{number}-{uuid.uuid4().hex[:6]}"
    case = Case(
        id=new_uuid(),
        case_number=number,
        title="Test case",
        jurisdiction_id=jurisdiction,
        status=CaseStatus.OPEN,
        created_by=owner.id,
    )
    session.add(case)
    await session.commit()
    return case


@pytest.fixture()
async def case(db, users) -> Any:
    return await _make_case(db, users["INV-0001"], "FIR/2024/0001/PS-TEST", "RJ-JAIPUR")


@pytest.fixture()
async def other_case(db, users) -> Any:
    return await _make_case(db, users["INV-0002"], "FIR/2024/0002/PS-KOTA", "RJ-KOTA")


# --------------------------------------------------------------------------- #
# HTTP client
# --------------------------------------------------------------------------- #


@pytest.fixture()
def app(workspace: Settings, container: Container):
    from app.main import create_app

    return create_app()


@pytest.fixture()
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client


def auth_headers(client, badge: str, password: str = PASSWORD) -> dict[str, str]:
    """Log in and return an Authorization header."""
    response = client.post(
        "/api/v1/auth/login", json={"badge_number": badge, "password": password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture()
def investigator_headers(client, users) -> dict[str, str]:
    return auth_headers(client, "INV-0001")


@pytest.fixture()
def viewer_headers(client, users) -> dict[str, str]:
    return auth_headers(client, "VIW-0001")


@pytest.fixture()
def admin_headers(client, users) -> dict[str, str]:
    return auth_headers(client, "ADM-0001")


@pytest.fixture()
def kota_headers(client, users) -> dict[str, str]:
    return auth_headers(client, "INV-0002")


# --------------------------------------------------------------------------- #
# Sample documents
# --------------------------------------------------------------------------- #

SAMPLE_FIR = (
    "FIRST INFORMATION REPORT\n"
    "Police Station: M.I. Road, Jaipur City, Rajasthan.\n"
    "FIR No. 231/2024, dated 14/08/2024. Sections 384, 386, 120B IPC.\n"
    "Complainant Smt. Sunita Devi, wife of Shri Kailash Chand, resident of "
    "Bapu Nagar, Jaipur, age about 45 years.\n"
    "Accused Ramesh Kumar Yadav alias Ramesh Yadav, son of Shri Ram Prasad Yadav, "
    "came to my shop at about 20:30 hours along with his associate Suresh Mehta "
    "and demanded rupees fifty lakh as rangdari.\n"
    "Accused Vikram Singh Rathore is the brother of Meena Rathore and is also "
    "involved in the extortion. The accused used mobile number +919829012345 "
    "and vehicle RJ14AB1234.\n"
    "The amount was to be transferred to account 50100234567890 (IFSC SBIN0001234).\n"
    "Recorded by Inspector Rajesh Khanna."
)

SAMPLE_CDR = "\n".join(
    ["calling_number,called_number,timestamp,duration_seconds,direction,imei"]
    + [
        f"+919829012345,+91{9870000000 + i},2024-08-0{(i % 9) + 1}T10:00:00Z,"
        f"{60 + i},OUTGOING,3557100{i:04d}"
        for i in range(1, 13)
    ]
)

SAMPLE_HINDI_FIR = (
    "प्रथम सूचना रिपोर्ट\n"
    "थाना एम आई रोड, जयपुर शहर, राजस्थान।\n"
    "दिनांक 14/08/2024, धारा 384, 386, 120बी भादवि।\n"
    "शिकायतकर्ता श्रीमती सुनीता देवी पत्नी श्री कैलाश चंद, निवासी बापू नगर, जयपुर, "
    "उम्र लगभग 45 वर्ष।\n"
    "आरोपी रमेश यादव उर्फ रमेश कुमार पुत्र श्री राम प्रसाद निवासी सांगानेर ने "
    "मेरी दुकान पर आया और पचास लाख रुपये रंगदारी की मांग की।\n"
    "आरोपी विक्रम सिंह राठौड़ भी उसके साथ था।\n"
    "आरोपी सुरेश मेहता रमेश यादव का सहयोगी है तथा वसूली का काम करता है।\n"
    "विक्रम सिंह राठौड़ का भाई होने के नाते मीना राठौड़ भी इस मामले में संलिप्त पाई गई है।\n"
    "मोबाइल नंबर 9829012345 से धमकी भरी कॉल की और कहा कि राशि खाता संख्या "
    "50100234567890 में जमा कराई जाए।\n"
    "हस्ताक्षर सुनीता देवी, दर्ज कर्ता निरीक्षक राजेश खन्ना।"
)

# 22 transfers of ₹48,000 in 30 days: every one is below the ₹50,000 reporting
# threshold, but together they move ₹10.56 lakh — the textbook structuring
# signature the pattern engine is required to surface.
SAMPLE_BANK = "\n".join(
    ["txn_id,date,from_account,to_account,amount,ifsc,remarks"]
    + [
        "TXN%03d,2024-08-%02d,50100234567890,50200011112222,48000,SBIN0001234,cash"
        % (i, (i % 28) + 1)
        for i in range(1, 23)
    ]
)
