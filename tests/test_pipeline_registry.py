from __future__ import annotations

import pytest

from converter.pipeline_registry import (
    UnsupportedConversion,
    validate_pipeline_request,
)


def test_registry_routes_every_contract_conversion() -> None:
    expected = {
        ("geojson", "pmtiles"): "pipeline vector tiles",
        ("shp", "pmtiles"): "pipeline vector tiles",
        ("las", "3dtiles"): "pipeline point cloud tiles",
        ("laz", "3dtiles"): "pipeline point cloud tiles",
        ("las", "cog"): "pipeline point cloud to DEM",
        ("laz", "cog"): "pipeline point cloud to DEM",
    }

    for pair, pipeline_name in expected.items():
        assert validate_pipeline_request(*pair, {}) == pipeline_name


def test_registry_rejects_unknown_conversion() -> None:
    with pytest.raises(UnsupportedConversion):
        validate_pipeline_request("gml", "pmtiles", {})


def test_registry_rejects_irrelevant_parameters() -> None:
    with pytest.raises(ValueError, match="irrelevant parameters: min_zoom"):
        validate_pipeline_request("geojson", "pmtiles", {"min_zoom": 3})
