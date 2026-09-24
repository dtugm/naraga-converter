"""Cancellable subprocess execution with one POSIX process group per job."""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProcessResult:
    stdout: str
    stderr: str


class ProcessFailed(RuntimeError):
    def __init__(self, message: str, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


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


_STDERR_TAIL_BYTES = 64 * 1024


async def _read_lines(
    stream: asyncio.StreamReader, sink: list[str], on_line: Callable[[str], Awaitable[None]] | None
) -> None:
    async for raw in stream:
        line = raw.decode(errors="replace").rstrip("\n")
        sink.append(line)
        if on_line is not None:
            await on_line(line)


async def _read_tail(stream: asyncio.StreamReader, tail: bytearray) -> None:
    # ponytail: bounded tail only — mago/tippecanoe can log hundreds of MB on big inputs.
    while chunk := await stream.read(65536):
        tail.extend(chunk)
        del tail[:-_STDERR_TAIL_BYTES]


async def run_process(
    command: Sequence[str],
    cwd: Path,
    timeout: float,
    on_line: Callable[[str], Awaitable[None]] | None = None,
) -> ProcessResult:
    """Run one command in its own process group; stream stdout lines to ``on_line``."""
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    stdout, stderr = process.stdout, process.stderr
    assert stdout is not None and stderr is not None
    lines: list[str] = []
    tail = bytearray()

    async def _communicate() -> None:
        await asyncio.gather(
            _read_lines(stdout, lines, on_line), _read_tail(stderr, tail)
        )
        await process.wait()

    try:
        await asyncio.wait_for(_communicate(), timeout=timeout)
    except BaseException:  # timeout, cancel, or a failing on_line: never orphan the group
        await _stop_group(process)
        raise

    decoded_out = "\n".join(lines)
    decoded_err = tail.decode(errors="replace")
    if process.returncode != 0:
        raise ProcessFailed(
            f"process failed with exit {process.returncode}: {decoded_err[-2000:]}",
            returncode=process.returncode,
        )
    return ProcessResult(decoded_out, decoded_err)
