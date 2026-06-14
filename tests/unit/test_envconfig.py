"""Unit tests for the .env / environment config loader.

These are pure parsing/precedence tests. They never touch sudo, SELinux, or any
live system; they exercise :func:`load_env` and :class:`EnvConfig` against
temporary files and a monkeypatched environment.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cerebellum_cua.cli.__main__ import _build_parser, _resolve_credentials
from cerebellum_cua.envconfig import (
    DB_DSN_KEY,
    ELEVATION_PASSWORD_KEY,
    SECRET_KEY,
    EnvConfig,
    load_env,
)


def _write(tmp_path: Path, text: str) -> str:
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_load_env_parses_keys_comments_blanks_and_quotes(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "\n".join(
            [
                "# a comment",
                "",
                "   ",
                "CEREBELLUM_SECRET=plainvalue",
                'CEREBELLUM_DB_DSN="postgresql://u:p@h:5432/db"',
                "export CEREBELLUM_ELEVATION_PASSWORD='quoted pw'",
                "  SPACED_KEY = spaced value ",
                "no_equals_line_ignored",
            ]
        ),
    )
    parsed = load_env(path)
    assert parsed[SECRET_KEY] == "plainvalue"
    assert parsed[DB_DSN_KEY] == "postgresql://u:p@h:5432/db"
    assert parsed[ELEVATION_PASSWORD_KEY] == "quoted pw"
    assert parsed["SPACED_KEY"] == "spaced value"
    assert "no_equals_line_ignored" not in parsed


def test_load_env_missing_file_returns_empty_dict(tmp_path: Path) -> None:
    assert load_env(str(tmp_path / "does-not-exist.env")) == {}


def test_envconfig_reads_values_from_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in (SECRET_KEY, DB_DSN_KEY, ELEVATION_PASSWORD_KEY):
        monkeypatch.delenv(key, raising=False)
    path = _write(
        tmp_path,
        f"{SECRET_KEY}=filesecret\n{DB_DSN_KEY}=./state.db\n",
    )
    cfg = EnvConfig.load(path)
    assert cfg.secret == "filesecret"
    assert cfg.db_dsn == "./state.db"
    assert cfg.elevation_password is None


def test_env_var_takes_precedence_over_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path, f"{SECRET_KEY}=fromfile\n{DB_DSN_KEY}=fromfile_dsn\n")
    monkeypatch.setenv(SECRET_KEY, "fromenv")
    monkeypatch.delenv(DB_DSN_KEY, raising=False)
    cfg = EnvConfig.load(path)
    assert cfg.secret == "fromenv"  # env wins
    assert cfg.db_dsn == "fromfile_dsn"  # falls back to file


def test_missing_optional_elevation_password_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in (SECRET_KEY, DB_DSN_KEY, ELEVATION_PASSWORD_KEY):
        monkeypatch.delenv(key, raising=False)
    cfg = EnvConfig.load(str(tmp_path / "absent.env"))
    assert cfg.elevation_password is None


def test_empty_string_value_resolves_to_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blank value (as in .env.example) must disable, not set, the key."""
    for key in (SECRET_KEY, DB_DSN_KEY, ELEVATION_PASSWORD_KEY):
        monkeypatch.delenv(key, raising=False)
    path = _write(tmp_path, f"{ELEVATION_PASSWORD_KEY}=\n{SECRET_KEY}=s\n")
    cfg = EnvConfig.load(path)
    assert cfg.elevation_password is None
    assert cfg.secret == "s"


def test_missing_secret_resolves_to_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in (SECRET_KEY, DB_DSN_KEY, ELEVATION_PASSWORD_KEY):
        monkeypatch.delenv(key, raising=False)
    cfg = EnvConfig.load(str(tmp_path / "absent.env"))
    assert cfg.secret is None
    assert cfg.db_dsn is None


def test_repr_redacts_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SECRET_KEY, "supersecret")
    monkeypatch.setenv(DB_DSN_KEY, "./state.db")
    monkeypatch.delenv(ELEVATION_PASSWORD_KEY, raising=False)
    text = repr(EnvConfig.load(str(tmp_path / "absent.env")))
    assert "supersecret" not in text
    assert "secret=<set>" in text
    assert "elevation_password=<unset>" in text


# --- CLI credential fallback ------------------------------------------------


def test_cli_flags_optional_in_parser() -> None:
    """--db-dsn / --secret no longer required; parser accepts neither."""
    args = _build_parser().parse_args([])
    assert args.db_dsn is None
    assert args.secret is None


def test_resolve_credentials_prefers_flags() -> None:
    args = _build_parser().parse_args(["--db-dsn", "flagdsn", "--secret", "flagsec"])
    env = EnvConfig(secret="envsec", db_dsn="envdsn", elevation_password=None)
    assert _resolve_credentials(args, env) == ("flagdsn", "flagsec")


def test_resolve_credentials_falls_back_to_env() -> None:
    args = _build_parser().parse_args([])
    env = EnvConfig(secret="envsec", db_dsn="envdsn", elevation_password=None)
    assert _resolve_credentials(args, env) == ("envdsn", "envsec")


def test_resolve_credentials_errors_when_secret_missing() -> None:
    args = _build_parser().parse_args(["--db-dsn", "flagdsn"])
    env = EnvConfig(secret=None, db_dsn=None, elevation_password=None)
    with pytest.raises(SystemExit) as exc:
        _resolve_credentials(args, env)
    assert "--secret" in str(exc.value)
