from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from converter.contract.models import InternalJobRequest
from converter.processes import ProcessResult
from converter.runtime import execute_conversion


def _request(staging_root: Path) -> InternalJobRequest:
    return InternalJobRequest.model_validate(
        {
            "job_id": "00000000-0000-4000-8000-000000000111",
            "service": "converter",
            "model": None,
            "input_datasets": {
                "input": {
                    "dataset_id": "99999999-9999-4999-8999-999999999999",
                    "name": "roads.geojson",
                    "dataset_format": "geojson",
                    "dataset_role": None,
                    "size_bytes": 2,
                    "crs": "EPSG:4326",
                    "bbox": [110.0, -8.0, 111.0, -7.0],
                    "signed_url": "https://storage.invalid/private-read-token",
                    "signed_url_expires_at": "2026-09-10T00:00:00.000Z",
                }
            },
            "params": {"target_format": "pmtiles"},
            "output_prefix": "jobs/111/outputs/",
            "output_upload_urls": [
                {
                    "output_format": "pmtiles",
                    "storage_key": "jobs/111/outputs/roads.pmtiles",
                    "url": "https://storage.invalid/private-write-token",
                    "expires_at": "2026-09-10T00:00:00.000Z",
                }
            ],
            "callback_url": "https://gateway.invalid/callback",
            "max_job_duration_seconds": 30,
            "heartbeat_interval_seconds": 1,
        }
    )


def test_runtime_downloads_runs_uploads_and_cleans(
    tmp_path: Path, monkeypatch: Any
) -> None:
    staging_root = tmp_path / "staging"
    monkeypatch.setenv("CONVERTER_STAGING_ROOT", str(staging_root))
    from converter.config import get_settings

    get_settings.cache_clear()
    uploaded: list[bytes] = []
    worker_specs: list[dict[str, Any]] = []

    async def storage(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, content=b"{}")
        uploaded.append(await request.aread())
        return httpx.Response(200)

    async def fake_process(command: list[str], cwd: Path, timeout: float) -> ProcessResult:
        del command, timeout
        spec = json.loads((cwd / "worker-request.json").read_text())
        worker_specs.append(spec)
        artifact = cwd / "output" / "roads.pmtiles"
        artifact.parent.mkdir()
        artifact.write_bytes(b"tiles")
        (cwd / "worker-result.json").write_text(
            json.dumps({"artifact": str(artifact), "name": artifact.name})
        )
        return ProcessResult("", "")

    monkeypatch.setattr("converter.runtime.run_process", fake_process)

    async def exercise() -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(storage)) as client:
            return await execute_conversion(_request(staging_root), client, lambda _pct: _noop())

    outputs, result = asyncio.run(exercise())
    assert uploaded == [b"tiles"]
    assert outputs[0]["dataset_format"] == "pmtiles"
    assert result == {"output_format": "pmtiles", "output_size_bytes": 5}
    assert "signed_url" not in json.dumps(worker_specs)
    assert list(staging_root.iterdir()) == []


async def _noop() -> None:
    return None
