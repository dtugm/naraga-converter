"""Subprocess entry point for synchronous converter algorithms.

The parent passes only local paths and normalized parameters; signed URLs never cross
the process boundary or appear in tool command lines.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def execute(spec: dict[str, Any]) -> dict[str, Any]:
    pipeline = spec["pipeline"]
    if pipeline == "pipeline vector tiles":
        from .pipelines.vector_tiles import run

        return run(spec)
    if pipeline == "pipeline point cloud tiles":
        from .pipelines.point_cloud_tiles import run

        return run(spec)
    if pipeline == "pipeline cityjson to citygml":
        from .pipelines.cityjson_to_citygml import run

        return run(spec)
    if pipeline == "pipeline citygml to 3dtiles":
        from .pipelines.citygml_to_3dtiles import run

        return run(spec)
    if pipeline == "pipeline cityjson to 3dtiles":
        from .pipelines.cityjson_to_3dtiles import run

        return run(spec)
    raise ValueError(f"unknown pipeline: {pipeline}")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: python -m converter.pipeline_worker REQUEST RESULT")
    request_path, result_path = map(Path, sys.argv[1:])
    result = execute(json.loads(request_path.read_text()))
    result_path.write_text(json.dumps(result))


if __name__ == "__main__":
    main()
