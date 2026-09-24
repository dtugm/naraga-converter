"""Cancellable subprocess execution with one POSIX process group per job."""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProcessResult:
    stdout: str
    stderr: str


class ProcessFailed(RuntimeError):
    pass


async def _stop_group(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=1)
    except TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


async def run_process(command: Sequence[str], cwd: Path, timeout: float) -> ProcessResult:
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except (TimeoutError, asyncio.CancelledError):
        await _stop_group(process)
        raise

    decoded_out = stdout.decode(errors="replace")
    decoded_err = stderr.decode(errors="replace")
    if process.returncode != 0:
        raise ProcessFailed(f"process failed with exit {process.returncode}: {decoded_err[-2000:]}")
    return ProcessResult(decoded_out, decoded_err)
