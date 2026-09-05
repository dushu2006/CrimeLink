"""Focused tests for PostgreSQL bootstrap hardening.

The bootstrap runs optional extensions (pg_trgm/btree_gin) and mandatory
append-only ``REVOKE`` hardening.  Historically they shared one transaction,
so a missing extension aborted the transaction and silently skipped the
``REVOKE``s.  These tests prove the control flow: independent hardening no
longer depends on unrelated extension creation, and every statement is still
attempted.
"""

from __future__ import annotations


class _FakeConn:
    """Mimics a PostgreSQL connection inside/outside a failed transaction.

    Like the real server, a failed statement aborts the open transaction and
    every further statement fails until a commit/rollback.  A correct bootstrap
    must therefore never let a statement run inside an aborted transaction.
    """

    def __init__(self, failures: set[int]) -> None:
        self.failures = failures
        self.executed: list[str] = []
        self._aborted = False

    async def execute(self, statement) -> None:
        raw = statement.text if hasattr(statement, "text") else str(statement)
        self.executed.append(raw)
        index = len(self.executed) - 1
        if index in self.failures:
            self._aborted = True
            raise RuntimeError(f"simulated failure for statement {index}")
        if self._aborted:
            raise AssertionError(
                f"statement executed inside an aborted transaction: {raw!r}"
            )

    async def commit(self) -> None:
        self._aborted = False

    async def rollback(self) -> None:
        self._aborted = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeEngine:
    """A minimal engine handing out one recording connection."""

    def __init__(self, failures: set[int]) -> None:
        self.failures = failures
        self.connection: _FakeConn | None = None

    def connect(self) -> _FakeConn:
        if self.connection is None:
            self.connection = _FakeConn(self.failures)
        return self.connection


async def test_extension_failure_does_not_skip_audit_revokes():
    """A missing extension must not abort the append-only REVOKE hardening.

    Statements 0 and 1 are the extensions; 2..5 are the audit-table REVOKEs.
    With the first extension failing, all six statements must still be
    attempted and every REVOKE must run in a clean (non-aborted) transaction.
    """
    from app.db.session import _bootstrap_postgres

    engine = _FakeEngine({0})
    await _bootstrap_postgres(engine)  # type: ignore[arg-type]

    executed = engine.connection.executed  # type: ignore[union-attr]
    assert len(executed) == 6
    assert "pg_trgm" in executed[0]
    assert "btree_gin" in executed[1]
    assert all("REVOKE UPDATE, DELETE" in statement for statement in executed[2:])


async def test_all_statements_apply_when_nothing_fails():
    from app.db.session import _bootstrap_postgres

    engine = _FakeEngine(set())
    await _bootstrap_postgres(engine)  # type: ignore[arg-type]

    executed = engine.connection.executed  # type: ignore[union-attr]
    assert len(executed) == 6
    assert sum("REVOKE UPDATE, DELETE" in s for s in executed) == 4
