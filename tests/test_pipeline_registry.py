from __future__ import annotations

import pytest

from converter.pipeline_registry import (
    UnsupportedConversion,
    validate_pipeline_request,
)


def test_registry_routes_every_contract_conversion() -> None:
    expected = {
        ("geojson", "pmtiles"): "pipeline vector tiles",
        ("gpkg", "mbtiles"): "pipeline vector tiles",
        ("shp", "pmtiles"): "pipeline vector tiles",
        ("las", "3dtiles"): "pipeline point cloud tiles",
        ("laz", "3dtiles"): "pipeline point cloud tiles",
        ("cityjson", "gml"): "pipeline cityjson to citygml",
        ("gml", "3dtiles"): "pipeline citygml to 3dtiles",
        ("cityjson", "3dtiles"): "pipeline cityjson to 3dtiles",
    }

    for pair, pipeline_name in expected.items():
        assert validate_pipeline_request(*pair, {}) == pipeline_name


def test_registry_rejects_unknown_conversion() -> None:
    with pytest.raises(UnsupportedConversion):
        validate_pipeline_request("gml", "pmtiles", {})


def test_registry_rejects_irrelevant_parameters() -> None:
    with pytest.raises(ValueError, match="irrelevant parameters: geoid"):
        validate_pipeline_request("geojson", "pmtiles", {"geoid": "EGM96"})


def test_registry_validates_cross_field_ranges() -> None:
    with pytest.raises(ValueError, match="max_zoom"):
        validate_pipeline_request("geojson", "pmtiles", {"min_zoom": 12, "max_zoom": 8})

    with pytest.raises(ValueError, match="max_lod"):
        validate_pipeline_request("las", "3dtiles", {"min_lod": 8, "max_lod": 3})
