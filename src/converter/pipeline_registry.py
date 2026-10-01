"""Source/target routing and service-owned converter parameter validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class UnsupportedConversion(ValueError):
    """Raised when no production pipeline implements a format pair."""


@dataclass(frozen=True)
class PipelineSpec:
    name: str
    parameters: frozenset[str]


# ponytail: contract 2.1.0 only allows target_format/output_crs; tuning params wait for 3.0.0.
_COMMON = frozenset({"target_format", "output_crs"})

VECTOR_TILES = "pipeline vector tiles"
POINT_CLOUD_TILES = "pipeline point cloud tiles"
POINT_CLOUD_TO_DEM = "pipeline point cloud to DEM"

PIPELINES: dict[tuple[str, str], PipelineSpec] = {
    ("geojson", "pmtiles"): PipelineSpec(VECTOR_TILES, _COMMON),
    ("shp", "pmtiles"): PipelineSpec(VECTOR_TILES, _COMMON),
    ("las", "3dtiles"): PipelineSpec(POINT_CLOUD_TILES, _COMMON),
    ("laz", "3dtiles"): PipelineSpec(POINT_CLOUD_TILES, _COMMON),
    ("las", "cog"): PipelineSpec(POINT_CLOUD_TO_DEM, _COMMON),
    ("laz", "cog"): PipelineSpec(POINT_CLOUD_TO_DEM, _COMMON),
}


def conversion_matrix() -> dict[str, list[str]]:
    matrix: dict[str, list[str]] = {}
    for source, target in PIPELINES:
        matrix.setdefault(source, []).append(target)
    return matrix


def validate_pipeline_request(source: str, target: str, params: dict[str, Any]) -> str:
    spec = PIPELINES.get((source, target))
    if spec is None:
        raise UnsupportedConversion(f"unsupported conversion: {source} -> {target}")

    irrelevant = sorted(set(params) - spec.parameters)
    if irrelevant:
        raise ValueError(f"irrelevant parameters: {', '.join(irrelevant)}")

    return spec.name
