"""CLI entry point: ``python -m cerebellum_cua.cli`` / the ``cerebellum-cua`` console script.

Parses arguments, constructs a :class:`~cerebellum_cua.cli.engine.CuaEngine`, and
hands control to :func:`~cerebellum_cua.cli.repl.run_stdio_loop`. ``--db-dsn`` and
``--secret`` are required (spec Section 5 argparse); the rest seed a default
:class:`~cerebellum_cua.config.MatrixConfig` and an optional default capture target.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from cerebellum_cua.cli.engine import CuaEngine
from cerebellum_cua.cli.modes import DEFAULT_MODE, kwargs_for_mode, mode_names
from cerebellum_cua.cli.repl import run_stdio_loop
from cerebellum_cua.config import MatrixConfig

#: Default traversal depth, read off a throwaway config instance (slots-safe).
_DEFAULT_MAX_DEPTH = MatrixConfig().max_depth


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cerebellum-cua",
        description="Cerebellum CUA engine v4.2 (JSONL stdio REPL)",
    )
    parser.add_argument(
        "--db-dsn",
        required=True,
        help="Storage DSN: a path / sqlite:///... for SQLite, "
        "postgresql://user:pass@host:5432/matrixui for Postgres.",
    )
    parser.add_argument(
        "--secret", required=True, help="HS256 secret for lazy-load JWT tokens."
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


def main(argv: Sequence[str] | None = None) -> int:
    """Parse args, build the engine, and run the stdio loop. Returns an exit code."""
    args = _build_parser().parse_args(argv)
    config = _config_from_args(args)
    mode_kwargs = kwargs_for_mode(args.mode)
    with CuaEngine(args.db_dsn, args.secret, config=config, **mode_kwargs) as engine:
        run_stdio_loop(engine)
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
