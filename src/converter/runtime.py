"""Parent-side converter lifecycle: transfer, worker process, upload, cleanup."""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import sys
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from .archives import extract_zip
from .config import get_settings
from .contract.models import InternalJobRequest
from .pipeline_registry import validate_pipeline_request
from .processes import run_process
from .transfer import download_file, upload_file


async def _heartbeat(interval: int, report_progress: Callable[[int], Awaitable[None]]) -> None:
    while True:
        await asyncio.sleep(interval)
        await report_progress(10)


def _inside(path: Path, parent: Path) -> bool:
    resolved = path.resolve()
    root = parent.resolve()
    return resolved == root or root in resolved.parents


async def execute_conversion(
    request: InternalJobRequest,
    client: httpx.AsyncClient,
    report_progress: Callable[[int], Awaitable[None]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Execute one validated conversion without exposing storage credentials to workers."""
    root = Path(get_settings().converter_staging_root)
    root.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix=f"{request.job_id}-", dir=root))
    try:
        dataset = request.input_datasets["input"]
        dataset_json = dataset.model_dump(mode="json")
        output_upload = request.output_upload_urls[0]
        source_format = dataset.dataset_format.value
        target_format = request.params.target_format.value
        explicit_params = request.params.model_dump(
            mode="json", include=request.params.model_fields_set, exclude_none=True
        )
        pipeline_name = validate_pipeline_request(source_format, target_format, explicit_params)

        input_path = workspace / "input" / Path(dataset.name).name
        await download_file(client, dataset.signed_url, input_path)
        await report_progress(5)

        if input_path.suffix.lower() == ".zip":
            extracted_dir = workspace / "extracted"
            input_paths = extract_zip(input_path, extracted_dir)
        else:
            input_paths = [input_path]

        worker_request = {
            "pipeline": pipeline_name,
            "source_format": source_format,
            "target_format": target_format,
            "input_paths": [str(path) for path in input_paths],
            "source_name": dataset.name,
            "source_crs": dataset_json["crs"],
            "source_bbox": dataset_json["bbox"],
            "params": explicit_params,
            "output_dir": str(workspace / "output"),
        }
        request_path = workspace / "worker-request.json"
        result_path = workspace / "worker-result.json"
        request_path.write_text(json.dumps(worker_request))

        heartbeat = asyncio.create_task(
            _heartbeat(request.heartbeat_interval_seconds, report_progress)
        )
        try:
            await run_process(
                [
                    sys.executable,
                    "-m",
                    "converter.pipeline_worker",
                    str(request_path),
                    str(result_path),
                ],
                workspace,
                float(request.max_job_duration_seconds),
            )
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

        worker_result = json.loads(result_path.read_text())
        artifact = Path(worker_result["artifact"])
        if not _inside(artifact, workspace) or not artifact.is_file():
            raise RuntimeError("pipeline worker returned an invalid artifact path")
        await report_progress(90)
        await upload_file(client, output_upload.url, artifact)

        size = artifact.stat().st_size
        output_crs = (
            "EPSG:4326" if target_format in {"pmtiles", "mbtiles"} else dataset_json["crs"]
        )
        draft = {
            "name": worker_result.get("name", artifact.name),
            "dataset_format": target_format,
            "dataset_role": dataset.dataset_role,
            "storage_key": output_upload.storage_key,
            "size_bytes": size,
            "crs": output_crs,
            "bbox": worker_result.get("bbox", dataset_json["bbox"]),
        }
        summary = {"output_format": target_format, "output_size_bytes": size}
        return [draft], summary
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
