"""Storage subpackage: the ``StorageBackend`` seam and its two implementations.

``get_backend`` is the single entry point the engine/cli use to obtain a backend
from a DSN string without importing the concrete classes themselves — SQLite for
dev/Linux, PostgreSQL for production.
"""

from __future__ import annotations

from urllib.parse import urlsplit

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
        return SQLiteBackend(_sqlite_path(dsn), **kw)  # type: ignore[arg-type]
    return SQLiteBackend(dsn, **kw)  # type: ignore[arg-type]


def _sqlite_path(dsn: str) -> str:
    """Extract the SQLite file path from a ``sqlite://`` DSN.

    Follows the SQLAlchemy convention: the authority is always empty for SQLite,
    so exactly one leading slash separates the scheme from the database path.
    Strip only that one slash, preserving any further leading slash:

    - ``sqlite:///rel.db``   -> ``rel.db``        (relative)
    - ``sqlite:////abs.db``  -> ``/abs.db``       (absolute)
    - ``sqlite:///:memory:`` -> ``:memory:``
    - ``sqlite://``          -> ``:memory:``

    The previous implementation used ``lstrip("/")``, which stripped *every*
    leading slash and so made absolute paths impossible (see issue #49).
    """
    path = urlsplit(dsn).path
    if path.startswith("/"):
        path = path[1:]
    return path or ":memory:"
