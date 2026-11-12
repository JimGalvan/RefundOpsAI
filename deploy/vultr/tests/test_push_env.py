"""Uploader checks using synthetic values; no environment files or network."""

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "push_env.py"
SPEC = importlib.util.spec_from_file_location("vultr_push_env", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
uploader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(uploader)


def settings() -> dict[str, str]:
    return {name: "synthetic-value" for name in uploader.ALLOWLIST}


def test_only_allowlisted_keys_and_fixed_production_settings() -> None:
    local = settings() | {"VULTR_API_KEY": "must-stay-local", "DJANGO_DEBUG": "true"}
    result = uploader.build_env_file(local, "localhost")
    assert "must-stay-local" not in result
    assert "VULTR_API_KEY=" not in result
    assert 'DJANGO_DEBUG="false"' in result
    assert 'DJANGO_ALLOWED_HOSTS="localhost,localhost,.localhost"' in result
    assert 'REFUNDOPS_SITE_HOSTS="portal.localhost,tracking.localhost,inbox.localhost,store.localhost"' in result
    assert len(result.splitlines()) == len(uploader.ALLOWLIST) + 3


def test_missing_keys_are_reported_together_without_values() -> None:
    with pytest.raises(ValueError) as exc:
        uploader.build_env_file({"PAYPAL_CLIENT_ID": "synthetic-value"}, "localhost")
    assert "PAYPAL_CLIENT_SECRET" in str(exc.value)
    assert "DJANGO_SECRET_KEY" in str(exc.value)
    assert "synthetic-value" not in str(exc.value)


@pytest.mark.parametrize("host", ["-option", "https://localhost", "localhost:443", "a/b", "a\nX=x", "a;id"])
def test_host_cannot_inject_ssh_or_configuration(host: str) -> None:
    with pytest.raises(ValueError):
        uploader.build_env_file(settings(), host)


def test_systemd_quoting_preserves_literal_characters() -> None:
    local = settings() | {"DJANGO_SECRET_KEY": 'a"b\\c$HOME%value#tail'}
    result = uploader.build_env_file(local, "localhost")
    assert 'DJANGO_SECRET_KEY="a\\"b\\\\c$HOME%value#tail"' in result


@pytest.mark.parametrize("value", ["x\ny", "x\ry", "x\x00y"])
def test_multiline_or_null_values_are_refused(value: str) -> None:
    with pytest.raises(ValueError, match="Only single-line values are supported: DJANGO_SECRET_KEY"):
        uploader.build_env_file(settings() | {"DJANGO_SECRET_KEY": value}, "localhost")


def test_parser_reads_only_allowlist_from_synthetic_fixture(tmp_path: Path) -> None:
    fixture = tmp_path / "synthetic-settings.txt"
    fixture.write_text(
        "# synthetic fixture\nexport PAYPAL_CLIENT_ID='quoted # literal' # comment\n"
        'PAYPAL_CLIENT_SECRET="escaped \\" quote"\n'
        "PAYPAL_ENV=sandbox # comment\nVULTR_API_KEY=ignored\n",
        encoding="utf-8",
    )
    assert uploader.read_local_env(fixture) == {
        "PAYPAL_CLIENT_ID": "quoted # literal",
        "PAYPAL_CLIENT_SECRET": 'escaped " quote',
        "PAYPAL_ENV": "sandbox",
    }


def test_duplicate_key_refused_without_value(tmp_path: Path) -> None:
    fixture = tmp_path / "duplicate-settings.txt"
    fixture.write_text("PAYPAL_ENV=one\nPAYPAL_ENV=two\n", encoding="utf-8")
    with pytest.raises(ValueError, match="^Duplicate key: PAYPAL_ENV$"):
        uploader.read_local_env(fixture)


@pytest.mark.parametrize("status", [0, 1])
def test_ssh_values_only_in_stdin_and_never_logged(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], status: int
) -> None:
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "root@localhost", "localhost"])
    monkeypatch.setattr(uploader, "read_local_env", lambda _path: settings())
    ssh = Mock(return_value=subprocess.CompletedProcess([], status, "synthetic-value", "synthetic-value"))
    monkeypatch.setattr(uploader.subprocess, "run", ssh)
    if status:
        with pytest.raises(SystemExit, match="SSH transfer failed"):
            uploader.main()
    else:
        uploader.main()
    assert "synthetic-value" in ssh.call_args.kwargs["input"]
    assert "synthetic-value" not in str(ssh.call_args.args)
    captured = capsys.readouterr()
    assert "synthetic-value" not in captured.out + captured.err
    assert ssh.call_args.kwargs["capture_output"] is True


def test_missing_key_prevents_ssh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "root@localhost", "localhost"])
    monkeypatch.setattr(uploader, "read_local_env", lambda _path: {})
    ssh = Mock()
    monkeypatch.setattr(uploader.subprocess, "run", ssh)
    with pytest.raises(SystemExit, match="Missing required keys"):
        uploader.main()
    ssh.assert_not_called()
