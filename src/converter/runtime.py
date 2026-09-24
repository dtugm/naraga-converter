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
from .pipeline_worker import INPUT_ERROR_EXIT, ConversionInputError
from .processes import ProcessFailed, run_process
from .transfer import download_file, upload_file

# Output CRS per target (contract: bbox is always EPSG:4326 regardless).
# 3D Tiles encode an ECEF root transform, so the dataset honestly records EPSG:4978.
_TARGET_CRS = {"pmtiles": "EPSG:4326", "3dtiles": "EPSG:4978"}


class _Progress:
    """Maps worker progress (0-100) into the 10-90 band between download and upload."""

    def __init__(self, report: Callable[[int], Awaitable[None]]) -> None:
        self.report = report
        self.last = 10

    async def on_line(self, line: str) -> None:
        try:
            pct = int(json.loads(line)["progress"])
        except (ValueError, KeyError, TypeError):
            return  # tool chatter, not a progress line
        mapped = 10 + max(0, min(pct, 100)) * 80 // 100
        if mapped > self.last:
            self.last = mapped
            await self.report(mapped)

    async def heartbeat(self, interval: int) -> None:
        while True:
            await asyncio.sleep(interval)
            await self.report(self.last)


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

        progress = _Progress(report_progress)
        heartbeat = asyncio.create_task(progress.heartbeat(request.heartbeat_interval_seconds))
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
                on_line=progress.on_line,
            )
        except ProcessFailed as exc:
            if exc.returncode == INPUT_ERROR_EXIT and result_path.is_file():
                raise ConversionInputError(json.loads(result_path.read_text())["message"]) from None
            raise
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
            _TARGET_CRS.get(target_format) or worker_result.get("crs") or dataset_json["crs"]
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
