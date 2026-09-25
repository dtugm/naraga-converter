from __future__ import annotations

import numpy as np
import pytest

from converter.pipelines.point_cloud_to_dem import (
    NODATA,
    PointCloudToDemConfig,
    build_building_mask,
    calculate_bhm,
    fill_roof_surface,
    flatten_building_sites,
    snap_bounds,
)


def test_config_rejects_non_positive_grid_settings() -> None:
    with pytest.raises(ValueError, match="resolution_m must be greater than zero"):
        PointCloudToDemConfig(resolution_m=0)
    with pytest.raises(ValueError, match="dtm_search_radius_m must be greater than zero"):
        PointCloudToDemConfig(dtm_search_radius_m=-1)


def test_snap_bounds_expands_to_resolution_aligned_grid() -> None:
    grid = snap_bounds((1.1, 2.2, 4.9, 6.1), resolution=2.0)

    assert grid.bounds == (0.0, 2.0, 6.0, 8.0)
    assert grid.width == 3
    assert grid.height == 3


def test_building_mask_closes_one_cell_gap_and_fills_internal_hole() -> None:
    occupancy = np.array(
        [
            [0, 0, 0, 0, 0, 0, 0],
            [0, 1, 1, 1, 1, 1, 0],
            [0, 1, 1, 0, 1, 1, 0],
            [0, 1, 1, 0, 1, 1, 0],
            [0, 1, 1, 1, 1, 1, 0],
            [0, 0, 0, 0, 0, 0, 0],
        ],
        dtype=np.uint8,
    )

    mask, labels, count = build_building_mask(occupancy)

    assert count == 1
    assert mask[2, 3]
    assert mask[3, 3]
    assert labels[2, 3] == 1


def test_building_mask_preserves_building_touching_raster_edge() -> None:
    occupancy = np.zeros((5, 5), dtype=np.uint8)
    occupancy[0:2, 0:2] = 1

    mask, _, count = build_building_mask(occupancy)

    assert count == 1
    assert np.all(mask[0:2, 0:2])


def test_flatten_building_sites_uses_median_exterior_terrain() -> None:
    dtm = np.array(
        [
            [10, 10, 10, 10, 10],
            [10, 10, 11, 10, 10],
            [10, 9, 99, 11, 10],
            [10, 10, 9, 10, 10],
            [10, 10, 10, 10, 10],
        ],
        dtype=np.float32,
    )
    labels = np.zeros((5, 5), dtype=np.int32)
    labels[2, 2] = 1

    flattened, unresolved = flatten_building_sites(
        dtm, labels, component_count=1, resolution_m=1.0, search_radius_m=1.0
    )

    assert unresolved == 0
    assert flattened[2, 2] == pytest.approx(10.0)
    np.testing.assert_array_equal(flattened[labels == 0], dtm[labels == 0])


def test_flatten_marks_component_nodata_when_perimeter_has_no_terrain() -> None:
    dtm = np.full((5, 5), NODATA, dtype=np.float32)
    labels = np.zeros((5, 5), dtype=np.int32)
    labels[2, 2] = 1

    flattened, unresolved = flatten_building_sites(
        dtm, labels, component_count=1, resolution_m=1.0, search_radius_m=1.0
    )

    assert unresolved == 1
    assert flattened[2, 2] == NODATA


def test_flatten_perimeter_never_samples_another_building() -> None:
    dtm = np.full((5, 7), NODATA, dtype=np.float32)
    dtm[2, 2] = 100
    dtm[2, 4] = 200
    dtm[0, 3] = 10
    labels = np.zeros((5, 7), dtype=np.int32)
    labels[2, 2] = 1
    labels[2, 4] = 2

    flattened, unresolved = flatten_building_sites(
        dtm, labels, component_count=2, resolution_m=1.0, search_radius_m=2.0
    )

    assert unresolved == 0
    assert flattened[2, 2] == 10
    assert flattened[2, 4] == 10


def test_roof_fill_stays_within_component_and_bhm_is_zero_elsewhere() -> None:
    labels = np.zeros((4, 5), dtype=np.int32)
    labels[1:3, 1:4] = 1
    roof = np.full((4, 5), NODATA, dtype=np.float32)
    roof[1, 1] = 18
    roof[1, 3] = 20
    dtm = np.full((4, 5), 10, dtype=np.float32)

    filled = fill_roof_surface(roof, labels, component_count=1)
    bhm = calculate_bhm(filled, dtm, labels > 0)

    assert filled[2, 2] in (18, 20)
    assert np.all(bhm[labels == 0] == 0)
    assert bhm[1, 1] == pytest.approx(8)
    assert bhm[1, 3] == pytest.approx(10)


def test_bhm_clamps_negative_heights_and_preserves_nodata_inside_building() -> None:
    roof = np.array([[9, 15]], dtype=np.float32)
    dtm = np.array([[10, NODATA]], dtype=np.float32)
    mask = np.array([[True, True]])

    bhm = calculate_bhm(roof, dtm, mask)

    assert bhm[0, 0] == 0
    assert bhm[0, 1] == NODATA

def test_calculate_dsm_combines_dtm_and_bhm_inside_building_and_keeps_dtm_outside() -> None:
    from converter.pipelines.point_cloud_to_dem import calculate_dsm

    dtm = np.array([
        [10.0, 10.0, 10.0],
        [10.0, 12.0, 10.0],
        [10.0, 10.0, NODATA],
    ], dtype=np.float32)

    bhm = np.array([
        [0.0, 0.0, 0.0],
        [0.0, 8.0, 0.0],
        [0.0, 0.0, 0.0],
    ], dtype=np.float32)

    mask = np.array([
        [False, False, False],
        [False, True, False],
        [False, False, False],
    ], dtype=bool)

    dsm = calculate_dsm(dtm, bhm, mask)

    assert dsm[0, 0] == 10.0
    assert dsm[2, 2] == NODATA
    assert dsm[1, 1] == 20.0


def test_calculate_dsm_propagates_nodata_inside_building() -> None:
    from converter.pipelines.point_cloud_to_dem import calculate_dsm

    dtm = np.array([
        [NODATA, 10.0],
        [10.0, 10.0]
    ], dtype=np.float32)

    bhm = np.array([
        [8.0, NODATA],
        [NODATA, NODATA]
    ], dtype=np.float32)

    mask = np.array([
        [True, True],
        [True, False]
    ], dtype=bool)

    dsm = calculate_dsm(dtm, bhm, mask)

    assert dsm[0, 0] == NODATA
    assert dsm[0, 1] == NODATA
    assert dsm[1, 0] == NODATA
    assert dsm[1, 1] == 10.0
