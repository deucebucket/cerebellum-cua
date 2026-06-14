"""CLI entry point: ``python -m cerebellum_cua.cli`` / the ``cerebellum-cua`` console script.

Parses arguments, constructs a :class:`~cerebellum_cua.cli.engine.CuaEngine`, and
hands control to :func:`~cerebellum_cua.cli.repl.run_stdio_loop`. ``--db-dsn`` and
``--secret`` are OPTIONAL on the command line: when omitted they fall back to the
``.env`` / environment config layer (``CEREBELLUM_DB_DSN`` / ``CEREBELLUM_SECRET``
via :class:`~cerebellum_cua.envconfig.EnvConfig`). An explicit flag always wins.
The rest seed a default :class:`~cerebellum_cua.config.MatrixConfig` and an
optional default capture target.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from cerebellum_cua.cli.engine import CuaEngine
from cerebellum_cua.cli.modes import DEFAULT_MODE, kwargs_for_mode, mode_names
from cerebellum_cua.cli.repl import run_stdio_loop
from cerebellum_cua.config import MatrixConfig
from cerebellum_cua.envconfig import EnvConfig

#: Default traversal depth, read off a throwaway config instance (slots-safe).
_DEFAULT_MAX_DEPTH = MatrixConfig().max_depth


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cerebellum-cua",
        description="Cerebellum CUA engine v4.2 (JSONL stdio REPL)",
    )
    parser.add_argument(
        "--db-dsn",
        default=None,
        help="Storage DSN: a path / sqlite:///... for SQLite, "
        "postgresql://user:pass@host:5432/matrixui for Postgres. "
        "Falls back to CEREBELLUM_DB_DSN (env or .env) when omitted.",
    )
    parser.add_argument(
        "--secret",
        default=None,
        help="HS256 secret for lazy-load JWT tokens. "
        "Falls back to CEREBELLUM_SECRET (env or .env) when omitted.",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=_DEFAULT_MAX_DEPTH,
        help="Maximum traversal depth (default: %(default)s).",
    )
    parser.add_argument(
        "--target-exe",
        default=None,
        help="Default target executable/class regex for build_matrix.",
    )
    parser.add_argument(
        "--target-title",
        default=None,
        help="Default target window-title regex for build_matrix.",
    )
    parser.add_argument(
        "--mode",
        choices=mode_names(),
        default=DEFAULT_MODE,
        help="Execution mode (default: %(default)s). 'desktop' attaches to the "
        "real session; 'vm' and 'background' use the isolated virtual session "
        "from scripts/run-vm.sh ('background' is headless, no visible cursor).",
    )
    return parser


def _config_from_args(args: argparse.Namespace) -> MatrixConfig:
    extra: dict[str, object] = {}
    if args.target_exe:
        extra["target_exe"] = args.target_exe
    if args.target_title:
        extra["target_window_title"] = args.target_title
    cfg = MatrixConfig(max_depth=args.max_depth)
    cfg.extra = extra
    return cfg


def _resolve_credentials(
    args: argparse.Namespace, env: EnvConfig
) -> tuple[str, str]:
    """Resolve the DSN and secret from CLI flags, falling back to env config.

    An explicit ``--db-dsn`` / ``--secret`` always takes precedence; otherwise
    the corresponding ``CEREBELLUM_*`` value (env var or ``.env``) is used.
    Raises :class:`SystemExit` with a clear message if either is missing from
    every source.
    """
    db_dsn: str | None = args.db_dsn or env.db_dsn
    secret: str | None = args.secret or env.secret
    missing: list[str] = []
    if not db_dsn:
        missing.append("--db-dsn (or CEREBELLUM_DB_DSN)")
    if not secret:
        missing.append("--secret (or CEREBELLUM_SECRET)")
    if missing:
        raise SystemExit("error: missing required credential(s): " + ", ".join(missing))
    assert db_dsn is not None and secret is not None  # narrowed by the checks above
    return db_dsn, secret


def main(argv: Sequence[str] | None = None) -> int:
    """Parse args, build the engine, and run the stdio loop. Returns an exit code."""
    args = _build_parser().parse_args(argv)
    db_dsn, secret = _resolve_credentials(args, EnvConfig.load())
    config = _config_from_args(args)
    mode_kwargs = kwargs_for_mode(args.mode)
    with CuaEngine(db_dsn, secret, config=config, **mode_kwargs) as engine:
        run_stdio_loop(engine)
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
