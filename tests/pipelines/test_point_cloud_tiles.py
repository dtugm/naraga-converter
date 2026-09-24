from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest

from converter.pipeline_worker import ConversionInputError
from converter.pipelines import point_cloud_tiles as pct


def test_select_input_path_requires_exactly_one_las_or_laz() -> None:
    assert pct.select_input_path(["/tmp/a.las"]) == Path("/tmp/a.las")
    assert pct.select_input_path(["/tmp/a.laz"]) == Path("/tmp/a.laz")

    with pytest.raises(ConversionInputError):
        pct.select_input_path([])

    with pytest.raises(ConversionInputError):
        pct.select_input_path(["/tmp/a.las", "/tmp/b.laz"])

    with pytest.raises(ConversionInputError):
        pct.select_input_path(["/tmp/a.geojson"])


def test_input_type_for() -> None:
    assert pct.input_type_for("las") == "Las"
    assert pct.input_type_for("Las") == "Las"
    assert pct.input_type_for("laz") == "Laz"

    with pytest.raises(ConversionInputError):
        pct.input_type_for("shp")


def test_normalize_crs_extracts_digits_only() -> None:
    assert pct.normalize_crs("EPSG:32749") == "32749"
    assert pct.normalize_crs("32749") == "32749"
    assert pct.normalize_crs(" epsg:4326 ") == "4326"


def test_normalize_crs_rejects_non_numeric() -> None:
    with pytest.raises(ConversionInputError):
        pct.normalize_crs("not-a-crs")


def test_run_raises_conversion_input_error_when_crs_missing(tmp_path: Path) -> None:
    input_path = tmp_path / "a.las"
    input_path.write_bytes(b"fixture")
    spec: dict[str, Any] = {
        "pipeline": pct.PIPELINE_NAME,
        "source_format": "las",
        "target_format": "3dtiles",
        "input_paths": [str(input_path)],
        "source_name": "a.las",
        "source_crs": None,
        "source_bbox": None,
        "params": {},
        "output_dir": str(tmp_path / "out"),
    }
    with pytest.raises(ConversionInputError, match="no CRS"):
        pct.run(spec)


def test_build_tiler_cmd_never_passes_ge_flag() -> None:
    cmd = pct.build_tiler_cmd(
        "java",
        "/opt/mago-3d-tiler/mago-3d-tiler.jar",
        Path("/in/a.laz"),
        "Laz",
        Path("/work/tiles"),
        "32749",
    )
    assert cmd == [
        "java",
        "-jar",
        "/opt/mago-3d-tiler/mago-3d-tiler.jar",
        "-i",
        "/in/a.laz",
        "-it",
        "Laz",
        "-o",
        "/work/tiles",
        "--crs",
        "32749",
    ]
    assert "-ge" not in cmd


def test_zip_tiles_dir_places_tileset_at_root(tmp_path: Path) -> None:
    tiles_dir = tmp_path / "tiles"
    tiles_dir.mkdir()
    (tiles_dir / "tileset.json").write_text("{}")
    content_dir = tiles_dir / "content"
    content_dir.mkdir()
    (content_dir / "tile.b3dm").write_bytes(b"fake-b3dm")

    zip_path = tmp_path / "out.zip"
    pct.zip_tiles_dir(tiles_dir, zip_path)

    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
    assert names == {"tileset.json", "content/tile.b3dm"}


def test_run_end_to_end_with_monkeypatched_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "a.laz"
    input_path.write_bytes(b"fixture")
    output_dir = tmp_path / "out"

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        # mago-3d-tiler is invoked with -o <tiles_dir>; produce its expected output.
        tiles_dir = Path(cmd[cmd.index("-o") + 1])
        tiles_dir.mkdir(parents=True, exist_ok=True)
        (tiles_dir / "tileset.json").write_text("{}")
        (tiles_dir / "tile.b3dm").write_bytes(b"fake-b3dm")

        class _Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Completed()

    monkeypatch.setattr(subprocess, "run", fake_run)

    spec: dict[str, Any] = {
        "pipeline": pct.PIPELINE_NAME,
        "source_format": "laz",
        "target_format": "3dtiles",
        "input_paths": [str(input_path)],
        "source_name": "a.laz",
        "source_crs": "EPSG:32749",
        "source_bbox": [106.0, -6.5, 107.0, -6.0],
        "params": {},
        "output_dir": str(output_dir),
    }
    result = pct.run(spec)

    assert result["name"] == "a_3dtiles.zip"
    assert result["bbox"] == [106.0, -6.5, 107.0, -6.0]
    artifact = Path(result["artifact"])
    assert artifact.is_file()
    with zipfile.ZipFile(artifact) as archive:
        assert set(archive.namelist()) == {"tileset.json", "tile.b3dm"}


def test_header_crs_fallback(tmp_path: Path) -> None:
    import laspy
    from pyproj import CRS

    from converter.pipelines.point_cloud_tiles import _header_crs

    header = laspy.LasHeader(point_format=6, version="1.4")
    header.add_crs(CRS.from_epsg(32749))
    las = laspy.LasData(header)
    las.x, las.y, las.z = [430000.0], [9140000.0], [100.0]
    path = tmp_path / "p.laz"
    las.write(path)
    assert _header_crs(path) == "EPSG:32749"
    assert _header_crs(tmp_path / "missing.laz") is None
