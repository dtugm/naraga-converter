from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
from pyproj import CRS

pdal_available = shutil.which("pdal") is not None
laspy = pytest.importorskip("laspy")
rasterio = pytest.importorskip("rasterio")

from converter.pipelines.point_cloud_to_dem import (  # noqa: E402
    PointCloudToDemConfig,
    _inspect_point_cloud,
    generate_point_cloud_to_dem,
)


def _classified_fixture(path: Path) -> None:
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.add_crs(CRS.from_epsg(32649))
    header.scales = np.array([0.01, 0.01, 0.01])
    cloud = laspy.LasData(header)

    ground_xy = [(x, y) for x in range(500_000, 500_007) for y in range(9_000_000, 9_000_007)]
    building_xy = [
        (500_002.25, 9_000_002.25),
        (500_002.75, 9_000_002.25),
        (500_002.25, 9_000_002.75),
        (500_002.75, 9_000_002.75),
    ]
    road_xy = [(500_005.25, 9_000_001.25), (500_005.75, 9_000_001.25)]
    coordinates = ground_xy + building_xy + road_xy
    cloud.x = [item[0] for item in coordinates]
    cloud.y = [item[1] for item in coordinates]
    cloud.z = [10.0] * len(ground_xy) + [18.0] * len(building_xy) + [10.0] * len(road_xy)
    cloud.classification = np.array(
        [2] * len(ground_xy) + [6] * len(building_xy) + [11] * len(road_xy),
        dtype=np.uint8,
    )
    cloud.write(path)


@pytest.mark.parametrize("suffix", [".las", ".laz"])
def test_laspy_inspection_streams_class_counts_and_spatial_metadata(
    tmp_path: Path, suffix: str
) -> None:
    source = tmp_path / f"classified{suffix}"
    _classified_fixture(source)

    metadata = _inspect_point_cloud(source)

    assert metadata.crs == "EPSG:32649"
    assert metadata.class_counts == {2: 49, 6: 4, 11: 2}
    assert metadata.bounds == pytest.approx(
        (500_000.0, 9_000_000.0, 500_006.0, 9_000_006.0)
    )


@pytest.mark.skipif(not pdal_available, reason="PDAL CLI is only installed in the geo image")
@pytest.mark.parametrize("suffix", [".las", ".laz"])
def test_real_pdal_pipeline_generates_aligned_cogs(tmp_path: Path, suffix: str) -> None:
    source = tmp_path / f"classified{suffix}"
    _classified_fixture(source)

    result = generate_point_cloud_to_dem(
        source,
        tmp_path / "outputs",
        PointCloudToDemConfig(resolution_m=1.0, dtm_search_radius_m=2.0),
    )

    with rasterio.open(result.dem_path) as dem_ds:
        assert dem_ds.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG"
        assert dem_ds.crs == rasterio.crs.CRS.from_epsg(32649)
        assert dem_ds.count == 3
        bhm_values = dem_ds.read(3)
        assert np.count_nonzero(bhm_values > 0) > 0
        assert float(bhm_values.max()) == pytest.approx(8.0, abs=0.1)
