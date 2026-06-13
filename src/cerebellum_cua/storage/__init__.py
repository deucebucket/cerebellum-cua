"""Storage subpackage: the ``StorageBackend`` seam and its two implementations.

``get_backend`` is the single entry point the engine/cli use to obtain a backend
from a DSN string without importing the concrete classes themselves — SQLite for
dev/Linux, PostgreSQL for production.
"""

from __future__ import annotations

from cerebellum_cua.storage.base import StorageBackend
from cerebellum_cua.storage.postgres import PostgresBackend
from cerebellum_cua.storage.sqlite import SQLiteBackend

__all__ = ["StorageBackend", "SQLiteBackend", "PostgresBackend", "get_backend"]

_PG_PREFIXES = ("postgresql://", "postgres://", "postgresql+", "host=", "dbname=")


def get_backend(dsn: str | None = None, **kw: object) -> StorageBackend:
    """Return a backend chosen from ``dsn``.

    - ``None`` or a plain filesystem path / ``sqlite:///...`` -> SQLite.
    - a ``postgresql://`` / ``postgres://`` / libpq keyword DSN -> Postgres.

    Extra keyword args are forwarded to the chosen backend's constructor
    (e.g. ``schema_path=`` for Postgres).
    """
    if dsn is None:
        return SQLiteBackend(**kw)  # type: ignore[arg-type]
    lowered = dsn.strip().lower()
    if lowered.startswith(_PG_PREFIXES):
        return PostgresBackend(dsn, **kw)  # type: ignore[arg-type]
    if lowered.startswith("sqlite://"):
        # sqlite:///abs/path or sqlite:///:memory: -> strip the scheme.
        rest = dsn.split("://", 1)[1]
        path = rest.lstrip("/") or ":memory:"
        if rest.endswith(":memory:"):
            path = ":memory:"
        return SQLiteBackend(path, **kw)  # type: ignore[arg-type]
    return SQLiteBackend(dsn, **kw)  # type: ignore[arg-type]
