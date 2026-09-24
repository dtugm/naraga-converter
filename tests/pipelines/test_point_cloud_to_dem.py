from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from converter.pipelines import point_cloud_to_dem as dem


def _write_intermediate(path: Path, arrays: list[np.ndarray]) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=arrays[0].shape[1],
        height=arrays[0].shape[0],
        count=len(arrays),
        dtype="float32",
        crs="EPSG:32649",
        transform=from_origin(500_000, 9_000_005, 1, 1),
        nodata=dem.NODATA,
    ) as dataset:
        for band, array in enumerate(arrays, start=1):
            dataset.write(array.astype(np.float32), band)


def test_generate_writes_aligned_cogs_and_building_only_bhm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "classified.laz"
    input_path.write_bytes(b"fixture is inspected by the test seam")
    metadata = dem.PointCloudMetadata(
        bounds=(500_000.0, 9_000_000.0, 500_005.0, 9_000_005.0),
        crs="EPSG:32649",
        class_counts={2: 20, 6: 4, 11: 2},
    )
    monkeypatch.setattr(dem, "_inspect_point_cloud", lambda _: metadata)

    captured: list[dict[str, Any]] = []

    def fake_pdal(pipeline: dict[str, Any]) -> None:
        captured.append(pipeline)
        writer = pipeline["pipeline"][-1]
        output = Path(writer["filename"])
        if writer["output_type"] == "idw":
            _write_intermediate(output, [np.full((5, 5), 10, dtype=np.float32)])
        else:
            roof = np.full((5, 5), dem.NODATA, dtype=np.float32)
            count = np.zeros((5, 5), dtype=np.float32)
            roof[2, 2] = 18
            count[2, 2] = 3
            _write_intermediate(output, [roof, count])

    monkeypatch.setattr(dem, "_run_pdal_pipeline", fake_pdal)
    progress: list[int] = []

    result = dem.generate_point_cloud_to_dem(
        input_path,
        tmp_path / "outputs",
        dem.PointCloudToDemConfig(resolution_m=1, dtm_search_radius_m=2),
        progress.append,
    )

    assert result.dem_path.name == "classified_dem.tif"
    assert result.crs == "EPSG:32649"
    assert result.building_components_total == 1
    assert result.building_components_unresolved == 0
    assert progress == sorted(set(progress))
    assert progress[-1] == 100
    assert len(captured) == 2
    assert captured[0]["pipeline"][1]["expression"] == (
        "Classification == 2 || Classification == 11"
    )
    assert captured[0]["pipeline"][-1]["window_size"] == 2
    assert captured[1]["pipeline"][1]["expression"] == "Classification == 6"

    with rasterio.open(result.dem_path) as dem_ds:
        assert dem_ds.profile["driver"] == "GTiff"
        assert dem_ds.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"
        assert dem_ds.count == 3
        assert dem_ds.descriptions[0] == "DTM"
        assert dem_ds.descriptions[1] == "DSM (terrain + buildings, no vegetation)"
        assert dem_ds.descriptions[2] == "BHM (building height above ground)"
        np.testing.assert_array_equal(dem_ds.read(1), np.full((5, 5), 10, dtype=np.float32))
        
        expected_dsm = np.full((5, 5), 10, dtype=np.float32)
        expected_dsm[2, 2] = 18
        np.testing.assert_array_equal(dem_ds.read(2), expected_dsm)

        expected_bhm = np.zeros((5, 5), dtype=np.float32)
        expected_bhm[2, 2] = 8
        np.testing.assert_array_equal(dem_ds.read(3), expected_bhm)


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        (
            dem.PointCloudMetadata((0, 0, 1, 1), "EPSG:4326", {2: 1, 6: 1}),
            "projected CRS",
        ),
        (
            dem.PointCloudMetadata((0, 0, 1, 1), "EPSG:2263", {2: 1, 6: 1}),
            "metre units",
        ),
        (
            dem.PointCloudMetadata((0, 0, 1, 1), "EPSG:32649", {6: 1}),
            "ground class 2",
        ),
        (
            dem.PointCloudMetadata((0, 0, 1, 1), "EPSG:32649", {2: 1}),
            "building class 6",
        ),
    ],
)
def test_generate_rejects_invalid_spatial_or_class_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    metadata: dem.PointCloudMetadata,
    message: str,
) -> None:
    input_path = tmp_path / "classified.las"
    input_path.write_bytes(b"invalid fixture")
    monkeypatch.setattr(dem, "_inspect_point_cloud", lambda _: metadata)

    with pytest.raises(dem.PointCloudToDemError, match=message):
        dem.generate_point_cloud_to_dem(input_path, tmp_path / "out", dem.PointCloudToDemConfig())


def test_generate_rejects_non_las_input(tmp_path: Path) -> None:
    input_path = tmp_path / "classified.xyz"
    input_path.write_text("0 0 0")

    with pytest.raises(dem.PointCloudToDemError, match="LAS or LAZ"):
        dem.generate_point_cloud_to_dem(input_path, tmp_path / "out", dem.PointCloudToDemConfig())
