"""Streaming storage transfers. URLs are intentionally never logged."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import anyio
import httpx

_CHUNK_SIZE = 1024 * 1024


async def download_file(client: httpx.AsyncClient, url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        async with await anyio.open_file(destination, "xb") as output:
            async for chunk in response.aiter_bytes(_CHUNK_SIZE):
                await output.write(chunk)


async def _file_chunks(path: Path) -> AsyncIterator[bytes]:
    async with await anyio.open_file(path, "rb") as source:
        while chunk := await source.read(_CHUNK_SIZE):
            yield chunk


async def upload_file(client: httpx.AsyncClient, url: str, source: Path) -> None:
    response = await client.put(
        url,
        content=_file_chunks(source),
        headers={"Content-Length": str(source.stat().st_size)},
    )
    response.raise_for_status()
