"""Console entry point: run the cerebellum-cua MCP server over stdio.

Parses ``--db-dsn`` / ``--secret`` (and an optional ``--max-response-tokens``
ceiling), then serves the five operations as MCP tools over the stdio transport.
Invoke as ``python -m cerebellum_cua.mcp`` or via the ``cerebellum-cua-mcp``
console script.
"""

from __future__ import annotations

import argparse

from cerebellum_cua.mcp.server import run_stdio


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the stdio MCP server."""
    parser = argparse.ArgumentParser(
        prog="cerebellum-cua-mcp",
        description="Serve cerebellum-cua's five operations as MCP tools over stdio.",
    )
    parser.add_argument(
        "--db-dsn",
        required=True,
        help="SQLite file path / sqlite:///... or postgresql://... DSN.",
    )
    parser.add_argument(
        "--secret",
        required=True,
        help="HS256 secret used to sign and validate lazy tokens.",
    )
    parser.add_argument(
        "--max-response-tokens",
        type=int,
        default=None,
        help="Optional per-response token ceiling (default: off).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Parse arguments and run the stdio MCP server."""
    args = _parse_args(argv)
    run_stdio(
        args.db_dsn,
        args.secret,
        max_response_tokens=args.max_response_tokens,
    )


if __name__ == "__main__":  # pragma: no cover - module entry point
    main()
