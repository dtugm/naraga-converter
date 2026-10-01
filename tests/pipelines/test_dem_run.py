from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from converter.pipelines.point_cloud_to_dem import PointCloudToDemResult, run
from converter.worker_api import ConversionInputError


def test_dem_run_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    input_path = tmp_path / "test.laz"
    input_path.write_bytes(b"")
    output_dir = tmp_path / "out"

    def fake_generate(*args: Any, **kwargs: Any) -> PointCloudToDemResult:
        stem = kwargs.get('input_path', input_path).stem
        dem_path = kwargs.get("output_dir", output_dir) / f"{stem}_dem.tif"
        dem_path.parent.mkdir(parents=True, exist_ok=True)
        dem_path.write_bytes(b"fake cog")
        return PointCloudToDemResult(
            dem_path=dem_path,
            crs="EPSG:32649",
            bbox_wgs84=(1.0, 2.0, 3.0, 4.0),
            resolution_m=1.0,
            building_components_total=1,
            building_components_unresolved=0,
        )

    monkeypatch.setattr(
        "converter.pipelines.point_cloud_to_dem.generate_point_cloud_to_dem",
        fake_generate
    )

    spec = {
        "input_paths": [str(input_path)],
        "output_dir": str(output_dir)
    }

    result = run(spec)
    assert result["artifact"] == str(output_dir / "test_dem.tif")
    assert result["name"] == "test_dem.tif"
    assert result["bbox"] == [1.0, 2.0, 3.0, 4.0]
    assert result["crs"] == "EPSG:32649"


def test_dem_run_fails_without_input_paths() -> None:
    spec = {
        "input_paths": [],
        "output_dir": "/tmp"
    }
    with pytest.raises(ConversionInputError, match="No input paths provided"):
        run(spec)
