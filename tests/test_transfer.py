from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from converter.transfer import download_file, upload_file


def test_download_and_upload_stream_bytes(tmp_path: Path) -> None:
    uploaded: list[bytes] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, content=b"source-data")
        uploaded.append(await request.aread())
        return httpx.Response(200)

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            local = tmp_path / "input.bin"
            await download_file(client, "https://secret.invalid/read?signature=hidden", local)
            assert local.read_bytes() == b"source-data"
            await upload_file(client, "https://secret.invalid/write?signature=hidden", local)

    asyncio.run(exercise())
    assert uploaded == [b"source-data"]
