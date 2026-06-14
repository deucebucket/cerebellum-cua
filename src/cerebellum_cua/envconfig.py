"""Lightweight ``.env`` / environment configuration loader.

This is a deliberately tiny, dependency-free config layer distinct from
:class:`~cerebellum_cua.config.MatrixConfig` (which governs traversal/filtering).
It resolves the three deployment secrets the CLI and engine need:

* ``CEREBELLUM_SECRET`` — HS256 secret for lazy-load JWT tokens.
* ``CEREBELLUM_DB_DSN`` — storage DSN (a sqlite path or ``postgresql://...``).
* ``CEREBELLUM_ELEVATION_PASSWORD`` — OPTIONAL, sensitive. Used only to answer
  sudo/polkit/UAC prompts when a tutorial deliberately drives an elevation
  dialog. Leave unset to disable elevation handling entirely.

Resolution order for every key is **os.environ first, then the .env file** —
real environment variables always win over the file. Values are never logged or
echoed by this module; callers must keep them out of logs too.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

#: Canonical environment-variable keys.
SECRET_KEY = "CEREBELLUM_SECRET"
DB_DSN_KEY = "CEREBELLUM_DB_DSN"
ELEVATION_PASSWORD_KEY = "CEREBELLUM_ELEVATION_PASSWORD"


def _strip_quotes(value: str) -> str:
    """Strip one matching layer of surrounding single or double quotes."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def load_env(path: str = ".env") -> dict[str, str]:
    """Parse a ``KEY=VALUE`` env file into a dict.

    Blank lines and ``#`` comment lines are ignored, as is an optional
    leading ``export`` keyword. Surrounding quotes around a value are stripped.
    Whitespace around the key and value is trimmed. Lines without an ``=`` are
    skipped. A missing file yields an empty dict (not an error), so the loader
    is safe to call unconditionally.

    Args:
        path: Path to the env file. Defaults to ``.env`` in the cwd.

    Returns:
        A mapping of parsed keys to their string values.
    """
    result: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return result

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        result[key] = _strip_quotes(value.strip())
    return result


@dataclass(frozen=True, slots=True)
class EnvConfig:
    """Resolved deployment secrets, with env-over-file precedence.

    Construct via :meth:`load`. Fields hold the resolved values (or ``None``
    when unset). Never log these values.
    """

    secret: str | None
    db_dsn: str | None
    elevation_password: str | None

    @classmethod
    def load(cls, path: str = ".env") -> EnvConfig:
        """Resolve config from ``os.environ`` first, then the ``.env`` file.

        Args:
            path: Path to the env file consulted for keys absent from the
                process environment. Defaults to ``.env`` in the cwd.

        Returns:
            An :class:`EnvConfig` with each field resolved or ``None``.
        """
        file_values = load_env(path)

        def resolve(key: str) -> str | None:
            # os.environ wins; fall back to the file; empty string -> None.
            value = os.environ.get(key)
            if value is None:
                value = file_values.get(key)
            if value is None or value == "":
                return None
            return value

        return cls(
            secret=resolve(SECRET_KEY),
            db_dsn=resolve(DB_DSN_KEY),
            elevation_password=resolve(ELEVATION_PASSWORD_KEY),
        )

    def __repr__(self) -> str:  # pragma: no cover - trivial, redacts secrets
        """Redacted repr so secrets never leak into logs or tracebacks."""

        def mark(value: str | None) -> str:
            return "<set>" if value else "<unset>"

        return (
            "EnvConfig("
            f"secret={mark(self.secret)}, "
            f"db_dsn={mark(self.db_dsn)}, "
            f"elevation_password={mark(self.elevation_password)})"
        )
