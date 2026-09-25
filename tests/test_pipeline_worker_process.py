"""Run the worker exactly as production does (python -m, i.e. as __main__).

Regression: when ConversionInputError lived in pipeline_worker.py, the __main__ copy of
the class never matched the one pipelines raise, so every bad user file was reported
as INTERNAL_ERROR (full refund) with a traceback as the message.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run_worker(tmp_path: Path, spec: dict[str, object]) -> tuple[int, dict[str, str]]:
    request, result = tmp_path / "req.json", tmp_path / "res.json"
    request.write_text(json.dumps(spec))
    proc = subprocess.run(
        [sys.executable, "-m", "converter.pipeline_worker", str(request), str(result)],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, json.loads(result.read_text())


def _spec(tmp_path: Path, **overrides: object) -> dict[str, object]:
    spec: dict[str, object] = {
        "pipeline": "pipeline point cloud tiles",
        "source_format": "laz",
        "target_format": "3dtiles",
        "input_paths": [],
        "source_name": "x.laz",
        "source_crs": "EPSG:32749",
        "source_bbox": None,
        "params": {},
        "output_dir": str(tmp_path / "out"),
    }
    spec.update(overrides)
    return spec


def test_input_error_exits_3_under_main(tmp_path: Path) -> None:
    code, result = _run_worker(tmp_path, _spec(tmp_path))  # no input file -> user's fault
    assert code == 3
    assert result["error"] == "input"


def test_internal_error_is_short_and_traceback_free(tmp_path: Path) -> None:
    code, result = _run_worker(tmp_path, _spec(tmp_path, pipeline="pipeline nope"))
    assert code == 1
    assert result["error"] == "internal"
    assert "Traceback" not in result["message"] and len(result["message"]) <= 500
