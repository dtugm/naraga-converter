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


_COMMON = frozenset({"target_format", "output_crs"})
_VECTOR = frozenset({"min_zoom", "max_zoom", "layer_name", "drop_densest"})
_POINT_CLOUD = frozenset(
    {
        "geoid",
        "proj",
        "point_ratio",
        "max_points",
        "source_precision",
        "force_4byte_rgb",
        "quantize",
        "min_lod",
        "max_lod",
    }
)
_CITYGML_TILES = frozenset(
    {
        "draco",
        "draco_level",
        "draco_quantize",
        "dropped_columns",
        "strip_attributes",
        "tile_max_mb",
        "features_per_part",
        "max_part_size_mb",
    }
)

PIPELINES: dict[tuple[str, str], PipelineSpec] = {
    **{
        (source, target): PipelineSpec("pipeline vector tiles", _COMMON | _VECTOR)
        for source in ("geojson", "gpkg", "shp")
        for target in ("pmtiles", "mbtiles")
    },
    **{
        (source, "3dtiles"): PipelineSpec(
            "pipeline point cloud tiles", _COMMON | _POINT_CLOUD
        )
        for source in ("las", "laz")
    },
    ("cityjson", "gml"): PipelineSpec(
        "pipeline cityjson to citygml", _COMMON | frozenset({"citygml_version"})
    ),
    ("gml", "3dtiles"): PipelineSpec("pipeline citygml to 3dtiles", _COMMON | _CITYGML_TILES),
    ("cityjson", "3dtiles"): PipelineSpec(
        "pipeline cityjson to 3dtiles", _COMMON | _CITYGML_TILES
    ),
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

    min_zoom = params.get("min_zoom")
    max_zoom = params.get("max_zoom")
    if min_zoom is not None and max_zoom is not None and max_zoom < min_zoom:
        raise ValueError("max_zoom must be greater than or equal to min_zoom")

    min_lod = params.get("min_lod")
    max_lod = params.get("max_lod")
    if min_lod is not None and max_lod is not None and max_lod < min_lod:
        raise ValueError("max_lod must be greater than or equal to min_lod")

    return spec.name
