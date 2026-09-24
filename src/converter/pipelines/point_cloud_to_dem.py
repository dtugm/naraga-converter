"""Generate terrain and building-height rasters from classified LAS/LAZ input."""

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from pyproj import CRS, Transformer
from rasterio.shutil import copy as raster_copy
from scipy import ndimage

PIPELINE_NAME = "pipeline point cloud to DEM"
NODATA = -9999.0


@dataclass(frozen=True)
class PointCloudToDemConfig:
    resolution_m: float = 0.5
    dtm_search_radius_m: float = 10.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.resolution_m) or self.resolution_m <= 0:
            raise ValueError("resolution_m must be greater than zero")
        if not math.isfinite(self.dtm_search_radius_m) or self.dtm_search_radius_m <= 0:
            raise ValueError("dtm_search_radius_m must be greater than zero")


@dataclass(frozen=True)
class PointCloudToDemResult:
    dtm_path: Path
    bhm_path: Path
    crs: str
    bbox_wgs84: tuple[float, float, float, float]
    resolution_m: float
    building_components_total: int
    building_components_unresolved: int


@dataclass(frozen=True)
class RasterGrid:
    bounds: tuple[float, float, float, float]
    width: int
    height: int
    resolution: float


ProgressCallback = Callable[[int], None]


class PointCloudToDemError(RuntimeError):
    """Raised when an input cannot produce a trustworthy DTM and BHM."""


@dataclass(frozen=True)
class PointCloudMetadata:
    bounds: tuple[float, float, float, float]
    crs: str
    class_counts: dict[int, int]


def snap_bounds(bounds: tuple[float, float, float, float], resolution: float) -> RasterGrid:
    """Expand XY bounds to a stable grid aligned to multiples of ``resolution``."""
    if not math.isfinite(resolution) or resolution <= 0:
        raise ValueError("resolution must be greater than zero")
    min_x, min_y, max_x, max_y = bounds
    snapped = (
        math.floor(min_x / resolution) * resolution,
        math.floor(min_y / resolution) * resolution,
        math.ceil(max_x / resolution) * resolution,
        math.ceil(max_y / resolution) * resolution,
    )
    width = int(round((snapped[2] - snapped[0]) / resolution))
    height = int(round((snapped[3] - snapped[1]) / resolution))
    if width <= 0 or height <= 0:
        raise ValueError("point-cloud bounds must have positive width and height")
    return RasterGrid(snapped, width, height, resolution)


def build_building_mask(occupancy: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    """Create a one-cell-closed, hole-filled, 8-connected building mask."""
    raw_mask = np.asarray(occupancy) > 0
    structure = np.ones((3, 3), dtype=bool)
    padded = np.pad(raw_mask, 1, mode="constant", constant_values=False)
    closed = ndimage.binary_closing(padded, structure=structure, iterations=1)
    mask = ndimage.binary_fill_holes(closed)[1:-1, 1:-1]
    labels, count = ndimage.label(mask, structure=structure)
    return mask.astype(bool), labels.astype(np.int32), int(count)


def _valid_elevation(values: np.ndarray) -> np.ndarray:
    valid: np.ndarray = np.isfinite(values) & (values != NODATA)
    return valid


def flatten_building_sites(
    dtm: np.ndarray,
    labels: np.ndarray,
    component_count: int,
    resolution_m: float,
    search_radius_m: float,
) -> tuple[np.ndarray, int]:
    """Flatten each building component to median terrain from its exterior ring."""
    flattened = np.asarray(dtm, dtype=np.float32).copy()
    source_terrain = np.asarray(dtm, dtype=np.float32)
    all_buildings = labels > 0
    radius_cells = max(1, math.ceil(search_radius_m / resolution_m))
    structure = np.ones((3, 3), dtype=bool)
    unresolved = 0

    for component_id in range(1, component_count + 1):
        component = labels == component_id
        ring = ndimage.binary_dilation(
            component, structure=structure, iterations=radius_cells
        ) & ~all_buildings
        samples = source_terrain[ring & _valid_elevation(source_terrain)]
        if samples.size == 0:
            flattened[component] = NODATA
            unresolved += 1
            continue
        flattened[component] = np.float32(np.median(samples))
    return flattened, unresolved


def fill_roof_surface(
    roof: np.ndarray, labels: np.ndarray, component_count: int
) -> np.ndarray:
    """Fill roof holes from the nearest observed roof within each component."""
    filled = np.asarray(roof, dtype=np.float32).copy()
    for component_id in range(1, component_count + 1):
        component = labels == component_id
        valid = component & _valid_elevation(filled)
        if not np.any(valid):
            filled[component] = NODATA
            continue
        _, indices = ndimage.distance_transform_edt(~valid, return_indices=True)
        nearest = filled[tuple(indices)]
        fill_cells = component & ~valid
        filled[fill_cells] = nearest[fill_cells]
    return filled


def calculate_bhm(roof: np.ndarray, dtm: np.ndarray, building_mask: np.ndarray) -> np.ndarray:
    """Calculate non-negative building height with zero outside building cells."""
    roof_values = np.asarray(roof, dtype=np.float32)
    terrain = np.asarray(dtm, dtype=np.float32)
    mask = np.asarray(building_mask, dtype=bool)
    if roof_values.shape != terrain.shape or terrain.shape != mask.shape:
        raise ValueError("roof, dtm and building_mask must use the same grid")

    bhm = np.zeros(terrain.shape, dtype=np.float32)
    valid = mask & _valid_elevation(roof_values) & _valid_elevation(terrain)
    bhm[valid] = np.maximum(roof_values[valid] - terrain[valid], 0).astype(np.float32)
    bhm[mask & ~valid] = NODATA
    return bhm


def _inspect_point_cloud(input_path: Path) -> PointCloudMetadata:
    try:
        import laspy
    except ImportError as exc:  # pragma: no cover - exercised by the production image
        raise PointCloudToDemError("laspy is required to inspect LAS/LAZ input") from exc

    try:
        with laspy.open(input_path) as source:
            dimensions = set(source.header.point_format.dimension_names)
            if "classification" not in {str(name).lower() for name in dimensions}:
                raise PointCloudToDemError("input has no Classification dimension")
            source_crs = source.header.parse_crs()
            if source_crs is None:
                raise PointCloudToDemError("input has no CRS")
            counts: dict[int, int] = {}
            for chunk in source.chunk_iterator(1_000_000):
                values, frequencies = np.unique(
                    np.asarray(chunk.classification), return_counts=True
                )
                for value, frequency in zip(values, frequencies, strict=True):
                    class_id = int(value)
                    counts[class_id] = counts.get(class_id, 0) + int(frequency)
            min_x, min_y = map(float, source.header.mins[:2])
            max_x, max_y = map(float, source.header.maxs[:2])
    except PointCloudToDemError:
        raise
    except Exception as exc:
        raise PointCloudToDemError("unable to read LAS/LAZ input") from exc

    return PointCloudMetadata(
        bounds=(min_x, min_y, max_x, max_y), crs=source_crs.to_string(), class_counts=counts
    )


def _validate_metadata(metadata: PointCloudMetadata) -> CRS:
    try:
        crs = CRS.from_user_input(metadata.crs)
    except Exception as exc:
        raise PointCloudToDemError("input has an invalid CRS") from exc
    if not crs.is_projected:
        raise PointCloudToDemError("input must use a projected CRS")
    horizontal_axes = crs.axis_info[:2]
    if len(horizontal_axes) < 2 or any(
        axis.unit_conversion_factor is None
        or not math.isclose(float(axis.unit_conversion_factor), 1.0, rel_tol=0, abs_tol=1e-12)
        for axis in horizontal_axes
    ):
        raise PointCloudToDemError("input projected CRS must use metre units")
    if metadata.class_counts.get(2, 0) <= 0:
        raise PointCloudToDemError("input must contain ground class 2")
    if metadata.class_counts.get(6, 0) <= 0:
        raise PointCloudToDemError("input must contain building class 6")
    return crs


def _pdal_bounds(grid: RasterGrid) -> str:
    min_x, min_y, max_x, max_y = grid.bounds
    return f"([{min_x},{max_x}],[{min_y},{max_y}])"


def _dtm_pipeline(
    input_path: Path,
    output_path: Path,
    grid: RasterGrid,
    config: PointCloudToDemConfig,
    include_road: bool,
) -> dict[str, object]:
    expression = (
        "Classification == 2 || Classification == 11"
        if include_road
        else "Classification == 2"
    )
    return {
        "pipeline": [
            {"type": "readers.las", "filename": str(input_path)},
            {"type": "filters.expression", "expression": expression},
            {
                "type": "writers.gdal",
                "filename": str(output_path),
                "gdaldriver": "GTiff",
                "gdalopts": "TILED=YES,COMPRESS=DEFLATE,PREDICTOR=3",
                "output_type": "idw",
                "data_type": "float",
                "nodata": NODATA,
                "resolution": config.resolution_m,
                "radius": config.resolution_m * math.sqrt(2),
                "window_size": math.ceil(config.dtm_search_radius_m / config.resolution_m),
                "bounds": _pdal_bounds(grid),
            },
        ]
    }


def _building_pipeline(
    input_path: Path, output_path: Path, grid: RasterGrid, config: PointCloudToDemConfig
) -> dict[str, object]:
    return {
        "pipeline": [
            {"type": "readers.las", "filename": str(input_path)},
            {"type": "filters.expression", "expression": "Classification == 6"},
            {
                "type": "writers.gdal",
                "filename": str(output_path),
                "gdaldriver": "GTiff",
                "gdalopts": "TILED=YES,COMPRESS=DEFLATE,PREDICTOR=3",
                "output_type": "max,count",
                "data_type": "float",
                "nodata": NODATA,
                "resolution": config.resolution_m,
                "radius": config.resolution_m * math.sqrt(2),
                "window_size": 0,
                "bounds": _pdal_bounds(grid),
            },
        ]
    }


def _run_pdal_pipeline(pipeline: dict[str, object]) -> None:
    try:
        completed = subprocess.run(
            ["pdal", "pipeline", "--stdin"],
            input=json.dumps(pipeline),
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except FileNotFoundError as exc:
        raise PointCloudToDemError("PDAL executable is not available") from exc
    if completed.returncode != 0:
        raise PointCloudToDemError(
            f"PDAL rasterization failed with exit code {completed.returncode}"
        )


def _write_cog(array: np.ndarray, profile: dict[str, object], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cog-", dir=target.parent) as temp_dir:
        temp_root = Path(temp_dir)
        source_path = temp_root / "source.tif"
        staged_path = temp_root / "output.tif"
        output_profile = {
            "driver": "GTiff",
            "width": array.shape[1],
            "height": array.shape[0],
            "count": 1,
            "dtype": "float32",
            "crs": profile["crs"],
            "transform": profile["transform"],
            "nodata": NODATA,
        }
        with rasterio.open(source_path, "w", **output_profile) as dataset:
            dataset.write(array.astype(np.float32), 1)
        raster_copy(
            source_path,
            staged_path,
            driver="COG",
            compress="DEFLATE",
            blocksize=512,
            predictor="FLOATING_POINT",
            bigtiff="IF_SAFER",
            overview_resampling="AVERAGE",
        )
        os.replace(staged_path, target)


def _ensure_same_grid(dtm: rasterio.io.DatasetReader, building: rasterio.io.DatasetReader) -> None:
    if (
        dtm.width != building.width
        or dtm.height != building.height
        or dtm.transform != building.transform
        or dtm.crs != building.crs
    ):
        raise PointCloudToDemError("PDAL produced mismatched terrain and building grids")
    if building.count < 2:
        raise PointCloudToDemError("PDAL building raster is missing max/count bands")


def generate_point_cloud_to_dem(
    input_path: Path,
    output_dir: Path,
    config: PointCloudToDemConfig,
    progress: ProgressCallback | None = None,
) -> PointCloudToDemResult:
    """Generate aligned DTM and building-only BHM Cloud Optimized GeoTIFFs."""

    def report(percent: int) -> None:
        if progress is not None:
            progress(percent)

    source = Path(input_path)
    destination = Path(output_dir)
    if source.suffix.lower() not in {".las", ".laz"}:
        raise PointCloudToDemError("input must be a LAS or LAZ file")
    if not source.is_file():
        raise PointCloudToDemError("input LAS/LAZ file does not exist")

    metadata = _inspect_point_cloud(source)
    crs = _validate_metadata(metadata)
    grid = snap_bounds(metadata.bounds, config.resolution_m)
    report(5)

    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="point-cloud-to-dem-", dir=destination) as temp_dir:
        scratch = Path(temp_dir)
        dtm_intermediate = scratch / "dtm.tif"
        building_intermediate = scratch / "building.tif"
        _run_pdal_pipeline(
            _dtm_pipeline(
                source,
                dtm_intermediate,
                grid,
                config,
                include_road=metadata.class_counts.get(11, 0) > 0,
            )
        )
        report(35)
        _run_pdal_pipeline(_building_pipeline(source, building_intermediate, grid, config))
        report(55)

        try:
            with rasterio.open(dtm_intermediate) as dtm_dataset, rasterio.open(
                building_intermediate
            ) as building_dataset:
                _ensure_same_grid(dtm_dataset, building_dataset)
                dtm = dtm_dataset.read(1).astype(np.float32)
                roof = building_dataset.read(1).astype(np.float32)
                occupancy = building_dataset.read(2)
                profile: dict[str, object] = {
                    "crs": dtm_dataset.crs,
                    "transform": dtm_dataset.transform,
                }
        except PointCloudToDemError:
            raise
        except Exception as exc:
            raise PointCloudToDemError("unable to read PDAL raster output") from exc

        mask, labels, component_count = build_building_mask(occupancy)
        flattened, unresolved = flatten_building_sites(
            dtm,
            labels,
            component_count,
            config.resolution_m,
            config.dtm_search_radius_m,
        )
        filled_roof = fill_roof_surface(roof, labels, component_count)
        bhm = calculate_bhm(filled_roof, flattened, mask)
        report(75)

        dtm_path = destination / f"{source.stem}_dtm.tif"
        bhm_path = destination / f"{source.stem}_bhm.tif"
        _write_cog(flattened, profile, dtm_path)
        _write_cog(bhm, profile, bhm_path)
        report(90)

    transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    bbox = transformer.transform_bounds(*grid.bounds, densify_pts=21)
    bbox_wgs84 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    report(100)
    return PointCloudToDemResult(
        dtm_path=dtm_path,
        bhm_path=bhm_path,
        crs=crs.to_string(),
        bbox_wgs84=bbox_wgs84,
        resolution_m=config.resolution_m,
        building_components_total=component_count,
        building_components_unresolved=unresolved,
    )
