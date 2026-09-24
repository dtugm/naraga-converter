"""Pipeline M — vector data (GeoJSON/Shapefile) -> PMTiles.

ogr2ogr reprojects the source layer to EPSG:4326 GeoJSON, tippecanoe tiles it
into an intermediate ``.mbtiles``, and ``pmtiles-convert`` repacks that into
the final ``.pmtiles`` archive. Intermediate files live in a scratch
directory next to ``output_dir`` and are always removed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from converter.config import get_settings
from converter.pipeline_worker import ConversionInputError, report_progress

PIPELINE_NAME = "pipeline vector tiles"

_GEOJSON_EXTS = (".geojson", ".json")


def select_source_path(source_format: str, input_paths: list[str]) -> Path:
    """Pick the single source file for the given format, or raise."""
    if source_format == "shp":
        candidates = [Path(p) for p in input_paths if p.lower().endswith(".shp")]
        if len(candidates) != 1:
            raise ConversionInputError("expected exactly one .shp input file")
        return candidates[0]

    candidates = [Path(p) for p in input_paths if p.lower().endswith(_GEOJSON_EXTS)]
    if len(candidates) != 1:
        raise ConversionInputError("expected exactly one geojson input file")
    return candidates[0]


def sanitize_layer_name(source_name: str | None) -> str:
    """Lowercase, [a-z0-9_]-only stem of ``source_name``; falls back to "layer"."""
    if not source_name:
        return "layer"
    stem = Path(source_name).stem.lower()
    cleaned = re.sub(r"[^a-z0-9_]", "_", stem).strip("_")
    return cleaned or "layer"


def build_ogr2ogr_cmd(
    ogr2ogr_bin: str, src: Path, dest: Path, s_srs: str | None = None
) -> list[str]:
    cmd = [ogr2ogr_bin, "-f", "GeoJSON", "-t_srs", "EPSG:4326"]
    if s_srs:
        cmd += ["-s_srs", s_srs]
    cmd += [str(dest), str(src)]
    return cmd


def build_tippecanoe_cmd(
    tippecanoe_bin: str, mbtiles: Path, geojson: Path, layer: str
) -> list[str]:
    return [
        tippecanoe_bin,
        "-o",
        str(mbtiles),
        "--force",
        "-Z0",
        "-zg",
        "--drop-densest-as-needed",
        "-l",
        layer,
        str(geojson),
    ]


def build_pmtiles_cmd(pmtiles_bin: str, mbtiles: Path, pmtiles_out: Path) -> list[str]:
    return [pmtiles_bin, "--overwrite", str(mbtiles), str(pmtiles_out)]


def _run_tool(cmd: list[str], tool_name: str) -> None:
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        stderr = (completed.stderr or "")[-2000:]
        raise RuntimeError(f"{tool_name} failed (exit {completed.returncode}): {stderr}")


def _source_has_crs(path: Path) -> bool:
    from osgeo import ogr  # local import: GDAL is heavy and only needed here

    dataset = ogr.Open(str(path))
    if dataset is None:
        return False
    layer = dataset.GetLayer(0)
    if layer is None:
        return False
    return layer.GetSpatialRef() is not None


def _compute_bbox(geojson_path: Path) -> list[float]:
    from osgeo import ogr  # local import: GDAL is heavy and only needed here

    dataset = ogr.Open(str(geojson_path))
    if dataset is None:
        raise ConversionInputError("input contains no features")
    layer = dataset.GetLayer(0)
    if layer is None or layer.GetFeatureCount() == 0:
        raise ConversionInputError("input contains no features")
    min_x, max_x, min_y, max_y = layer.GetExtent()
    return [min_x, min_y, max_x, max_y]


def run(spec: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    source_format = spec["source_format"]
    input_paths = spec.get("input_paths") or []
    source = select_source_path(source_format, input_paths)

    output_dir = Path(spec["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = sanitize_layer_name(spec.get("source_name"))

    work_dir = Path(tempfile.mkdtemp(prefix="vector-tiles-", dir=str(output_dir.parent)))
    try:
        reprojected = work_dir / "source_4326.geojson"
        source_crs = spec.get("source_crs")
        s_srs = source_crs if source_crs and not _source_has_crs(source) else None
        _run_tool(build_ogr2ogr_cmd(settings.ogr2ogr_bin, source, reprojected, s_srs), "ogr2ogr")
        report_progress(20)

        bbox = _compute_bbox(reprojected)

        mbtiles = work_dir / "tiles.mbtiles"
        _run_tool(
            build_tippecanoe_cmd(settings.tippecanoe_bin, mbtiles, reprojected, stem),
            "tippecanoe",
        )
        report_progress(70)

        pmtiles_out = output_dir / f"{stem}.pmtiles"
        _run_tool(
            build_pmtiles_cmd(settings.pmtiles_bin, mbtiles, pmtiles_out), "pmtiles-convert"
        )
        report_progress(95)

        return {
            "artifact": str(pmtiles_out.resolve()),
            "name": f"{stem}.pmtiles",
            "bbox": bbox,
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
