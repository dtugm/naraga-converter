"""Pipeline M — point cloud (LAS/LAZ) -> 3D Tiles.

Runs the mago-3d-tiler JAR as a blocking subprocess and zips the resulting
tileset directory into a single downloadable archive.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from converter.config import get_settings
from converter.pipeline_worker import ConversionInputError, report_progress

PIPELINE_NAME = "pipeline point cloud tiles"

_INPUT_TYPE_MAP = {"las": "Las", "laz": "Laz"}


def select_input_path(input_paths: list[str]) -> Path:
    """Pick the single .las/.laz source file, or raise."""
    candidates = [Path(p) for p in input_paths if p.lower().endswith((".las", ".laz"))]
    if len(candidates) != 1:
        raise ConversionInputError("expected exactly one .las or .laz input file")
    return candidates[0]


def input_type_for(source_format: str) -> str:
    input_type = _INPUT_TYPE_MAP.get(source_format.lower())
    if input_type is None:
        raise ConversionInputError(f"unsupported point cloud format: {source_format}")
    return input_type


def normalize_crs(source_crs: str) -> str:
    """Extract the bare numeric EPSG code mago-3d-tiler's ``--crs`` expects."""
    digits = "".join(ch for ch in source_crs if ch.isdigit())
    if not digits:
        raise ConversionInputError("point cloud CRS must include a numeric EPSG code")
    return digits


def build_tiler_cmd(
    java_bin: str,
    jar_path: str,
    input_path: Path,
    input_type: str,
    tiles_dir: Path,
    crs_code: str,
) -> list[str]:
    # NEVER pass "-ge Ellipsoid": mago-3d-tiler 1.15.4 crashes on that exact flag.
    return [
        java_bin,
        "-jar",
        jar_path,
        "-i",
        str(input_path),
        "-it",
        input_type,
        "-o",
        str(tiles_dir),
        "--crs",
        crs_code,
    ]


def _run_tool(cmd: list[str], tool_name: str) -> None:
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        stderr = (completed.stderr or "")[-2000:]
        raise RuntimeError(f"{tool_name} failed (exit {completed.returncode}): {stderr}")


def zip_tiles_dir(tiles_dir: Path, zip_path: Path) -> None:
    """Zip ``tiles_dir`` with paths relative to it, so tileset.json sits at zip root."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in sorted(tiles_dir.rglob("*")):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(tiles_dir))


def _header_crs(path: Path) -> str | None:
    """EPSG code from the LAS/LAZ header, used when the dataset declares none."""
    try:
        import laspy

        with laspy.open(path) as reader:
            crs = reader.header.parse_crs()
        epsg = crs.to_epsg() if crs is not None else None
    except Exception:
        return None
    return f"EPSG:{epsg}" if epsg else None


def run(spec: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    source_format = spec["source_format"]
    input_type = input_type_for(source_format)
    input_paths = spec.get("input_paths") or []
    source = select_input_path(input_paths)

    source_crs = spec.get("source_crs") or _header_crs(source)
    if not source_crs:
        raise ConversionInputError("point cloud has no CRS")
    crs_code = normalize_crs(source_crs)

    output_dir = Path(spec["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(spec.get("source_name") or source.name).stem

    work_root = Path(tempfile.mkdtemp(prefix="point-cloud-tiles-", dir=str(output_dir.parent)))
    try:
        tiles_dir = work_root / "tiles"
        report_progress(10)
        _run_tool(
            build_tiler_cmd(
                settings.java_bin, settings.mago_tiler_jar, source, input_type, tiles_dir, crs_code
            ),
            "mago-3d-tiler",
        )
        report_progress(85)

        tileset_json = tiles_dir / "tileset.json"
        if not tileset_json.is_file():
            raise RuntimeError("mago-3d-tiler produced no tileset.json")

        zip_path = output_dir / f"{stem}_3dtiles.zip"
        zip_tiles_dir(tiles_dir, zip_path)
        report_progress(95)

        result: dict[str, Any] = {
            "artifact": str(zip_path.resolve()),
            "name": f"{stem}_3dtiles.zip",
        }
        source_bbox = spec.get("source_bbox")
        if source_bbox is not None:
            result["bbox"] = source_bbox
        return result
    finally:
        shutil.rmtree(work_root, ignore_errors=True)
