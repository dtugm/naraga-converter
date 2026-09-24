from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

import pytest

from converter.processes import ProcessFailed, run_process


def test_run_process_captures_output(tmp_path: Path) -> None:
    result = asyncio.run(
        run_process([sys.executable, "-c", "print('done')"], cwd=tmp_path, timeout=5)
    )
    assert result.stdout.strip() == "done"


def test_run_process_reports_nonzero_exit(tmp_path: Path) -> None:
    with pytest.raises(ProcessFailed, match="exit 7"):
        asyncio.run(run_process([sys.executable, "-c", "raise SystemExit(7)"], tmp_path, 5))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_timeout_kills_child_process_group(tmp_path: Path) -> None:
    child_pid_file = tmp_path / "child.pid"
    script = (
        "import pathlib,subprocess,sys,time;"
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']);"
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(p.pid));"
        "time.sleep(60)"
    )
    with pytest.raises(TimeoutError):
        asyncio.run(run_process([sys.executable, "-c", script], tmp_path, 0.3))

    child_pid = int(child_pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, signal.SIGCONT)
