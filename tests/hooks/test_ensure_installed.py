"""Tests for hooks/ensure-installed.sh (W6 — docs/technical-audit-2026-07-02.md
finding M8: the SessionStart hook used to be a silent, environment-blind
`pip show || pip install` one-liner with no failure signal).

There is no existing shell-hook test convention in this repo (grep over
tests/ turns up nothing for hooks/require-subagent-type.sh either), so these
invoke the script directly via subprocess with a stubbed PATH containing fake
python3/pip/uv executables. Stub behavior is controlled by env vars the stubs
themselves read (STUB_IMPORT_OK / STUB_INSTALL_OK / STUB_PEP668) so each
scenario is exercised without touching the real Python environment or
installing anything for real.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_SCRIPT = REPO_ROOT / "hooks" / "ensure-installed.sh"

_STUB_PYTHON = """#!/bin/bash
# Stub python3: behavior controlled by env vars set by the test.
if [ "$1" = "-c" ]; then
  [ "${STUB_IMPORT_OK:-0}" = "1" ] && exit 0 || exit 1
elif [ "$1" = "-m" ] && [ "$2" = "pip" ]; then
  if [ "${STUB_INSTALL_OK:-0}" = "1" ]; then
    echo "Successfully installed brightsmith-0.4.0"
    exit 0
  fi
  if [ "${STUB_PEP668:-0}" = "1" ]; then
    echo "error: externally-managed-environment" >&2
  else
    echo "some other pip failure" >&2
  fi
  exit 1
fi
exit 1
"""

_STUB_UV_OK = """#!/bin/bash
if [ "$1" = "pip" ] && [ "$2" = "install" ]; then
  echo "Resolved 1 package"
  exit 0
fi
exit 1
"""

_STUB_UV_MUST_NOT_RUN = """#!/bin/bash
echo "uv should not have been invoked in this scenario" >&2
exit 1
"""


def _write_stub(path: Path, content: str) -> None:
    path.write_text(content)
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def stub_bin(tmp_path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir / "python3", _STUB_PYTHON)
    return bin_dir


def _run_hook(tmp_path: Path, path_dirs: list[Path], env_overrides: dict) -> subprocess.CompletedProcess:
    """Run the hook with a fully-controlled PATH (stub dirs + just enough of
    the real system for bash/grep/mktemp/rm to resolve) and CLAUDE_PLUGIN_ROOT
    pointed at tmp_path (never a real install target)."""
    env = {
        "PATH": os.pathsep.join(str(p) for p in path_dirs) + os.pathsep + "/usr/bin:/bin",
        "CLAUDE_PLUGIN_ROOT": str(tmp_path),
        **env_overrides,
    }
    return subprocess.run(
        ["bash", str(HOOK_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
    )


def test_already_importable_is_silent_and_exits_0(tmp_path, stub_bin):
    result = _run_hook(tmp_path, [stub_bin], {"STUB_IMPORT_OK": "1"})
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_missing_plugin_root_is_silent_and_exits_0(tmp_path, stub_bin):
    env = {
        "PATH": str(stub_bin) + os.pathsep + "/usr/bin:/bin",
        "CLAUDE_PLUGIN_ROOT": "",
    }
    result = subprocess.run(["bash", str(HOOK_SCRIPT)], env=env, capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_install_succeeds_prints_at_most_one_short_line(tmp_path, stub_bin):
    result = _run_hook(tmp_path, [stub_bin], {"STUB_IMPORT_OK": "0", "STUB_INSTALL_OK": "1"})
    assert result.returncode == 0
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, result.stdout
    assert "installed" in lines[0].lower()
    assert len(lines[0]) < 200, "happy-path line should be short"


def test_install_failure_exits_0_and_names_exact_command(tmp_path, stub_bin):
    """A hook must not brick the session: install failure still exits 0, but
    must print the exact manual command (never a silent swallow, M8)."""
    result = _run_hook(tmp_path, [stub_bin], {"STUB_IMPORT_OK": "0", "STUB_INSTALL_OK": "0"})
    assert result.returncode == 0
    assert "pip install -e" in result.stdout
    assert str(tmp_path) in result.stdout


def test_pep668_failure_names_the_venv_workaround(tmp_path, stub_bin):
    result = _run_hook(
        tmp_path, [stub_bin],
        {"STUB_IMPORT_OK": "0", "STUB_INSTALL_OK": "0", "STUB_PEP668": "1"},
    )
    assert result.returncode == 0
    lowered = result.stdout.lower()
    assert "externally managed" in lowered or "externally-managed" in lowered
    assert "venv" in lowered


def test_generic_failure_does_not_mention_pep668(tmp_path, stub_bin):
    """The PEP 668 hint is conditional — a plain/other pip failure must not
    print a misleading venv suggestion tied to a different root cause."""
    result = _run_hook(tmp_path, [stub_bin], {"STUB_IMPORT_OK": "0", "STUB_INSTALL_OK": "0"})
    lowered = result.stdout.lower()
    assert "externally managed" not in lowered and "externally-managed" not in lowered


def test_prefers_uv_when_available_and_venv_active(tmp_path, stub_bin):
    uv_dir = tmp_path / "uvbin"
    uv_dir.mkdir()
    _write_stub(uv_dir / "uv", _STUB_UV_OK)
    venv_dir = tmp_path / "venv"
    venv_dir.mkdir()

    result = _run_hook(
        tmp_path, [uv_dir, stub_bin],
        {"STUB_IMPORT_OK": "0", "VIRTUAL_ENV": str(venv_dir)},
    )
    assert result.returncode == 0
    assert "uv pip install -e" in result.stdout


def test_no_venv_falls_back_to_pip_even_with_uv_on_path(tmp_path, stub_bin):
    """uv present but no venv active -> must use pip, never uv (spec: "prefers
    uv pip install -e when uv exists and a venv is active, else pip
    install -e"). The stub uv errors loudly if the hook calls it wrongly."""
    uv_dir = tmp_path / "uvbin"
    uv_dir.mkdir()
    _write_stub(uv_dir / "uv", _STUB_UV_MUST_NOT_RUN)

    result = _run_hook(
        tmp_path, [uv_dir, stub_bin],
        {"STUB_IMPORT_OK": "0", "STUB_INSTALL_OK": "1", "VIRTUAL_ENV": ""},
    )
    assert result.returncode == 0
    assert "python3 -m pip install -e" in result.stdout
    assert "uv should not have been invoked" not in result.stderr
