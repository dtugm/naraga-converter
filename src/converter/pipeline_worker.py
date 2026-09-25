"""Subprocess entry point for synchronous converter algorithms.

The parent passes only local paths and normalized parameters; signed URLs never cross
the process boundary or appear in tool command lines.

Exit codes: 0 = success (result JSON has "artifact"), 3 = the INPUT is unusable
(result JSON has {"error": "input", "message": ...}) — the parent maps that to a
no-refund VALIDATION_ERROR. Anything else is an internal failure.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any

from .pipeline_registry import POINT_CLOUD_TILES, POINT_CLOUD_TO_DEM, VECTOR_TILES
from .worker_api import INPUT_ERROR_EXIT, ConversionInputError, report_progress

__all__ = ["INPUT_ERROR_EXIT", "ConversionInputError", "execute", "report_progress"]

def execute(spec: dict[str, Any]) -> dict[str, Any]:
    pipeline = spec["pipeline"]
    if pipeline == VECTOR_TILES:
        from .pipelines.vector_tiles import run
    elif pipeline == POINT_CLOUD_TILES:
        from .pipelines.point_cloud_tiles import run
    elif pipeline == POINT_CLOUD_TO_DEM:
        from .pipelines.point_cloud_to_dem import run
    else:
        raise ValueError(f"unknown pipeline: {pipeline}")
    result: dict[str, Any] = run(spec)
    return result


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: python -m converter.pipeline_worker REQUEST RESULT")
    request_path, result_path = map(Path, sys.argv[1:])
    try:
        result = execute(json.loads(request_path.read_text()))
    except ConversionInputError as exc:
        result_path.write_text(json.dumps({"error": "input", "message": str(exc)}))
        raise SystemExit(INPUT_ERROR_EXIT) from None
    except Exception as exc:
        traceback.print_exc()  # full detail stays in the service log (stderr)
        message = f"{type(exc).__name__}: {exc}"[:500]
        result_path.write_text(json.dumps({"error": "internal", "message": message}))
        raise SystemExit(1) from None
    result_path.write_text(json.dumps(result))


if __name__ == "__main__":
    main()
