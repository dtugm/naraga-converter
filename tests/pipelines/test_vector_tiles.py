from __future__ import annotations

from pathlib import Path

import pytest

from converter.pipelines import vector_tiles as vt
from converter.worker_api import ConversionInputError


def test_select_source_path_shp_requires_exactly_one() -> None:
    assert vt.select_source_path("shp", ["/tmp/a.shp"]) == Path("/tmp/a.shp")

    with pytest.raises(ConversionInputError):
        vt.select_source_path("shp", [])

    with pytest.raises(ConversionInputError):
        vt.select_source_path("shp", ["/tmp/a.shp", "/tmp/b.shp"])

    with pytest.raises(ConversionInputError):
        vt.select_source_path("shp", ["/tmp/a.geojson"])


def test_select_source_path_geojson_accepts_json_and_geojson() -> None:
    assert vt.select_source_path("geojson", ["/tmp/a.geojson"]) == Path("/tmp/a.geojson")
    assert vt.select_source_path("geojson", ["/tmp/a.json"]) == Path("/tmp/a.json")

    with pytest.raises(ConversionInputError):
        vt.select_source_path("geojson", ["/tmp/a.shp"])

    with pytest.raises(ConversionInputError):
        vt.select_source_path("geojson", ["/tmp/a.geojson", "/tmp/b.geojson"])


@pytest.mark.parametrize(
    ("source_name", "expected"),
    [
        ("My Layer.geojson", "my_layer"),
        ("Weird-Name!!.shp", "weird_name"),
        ("___", "layer"),
        (None, "layer"),
        ("", "layer"),
        ("42.geojson", "42"),
    ],
)
def test_sanitize_layer_name(source_name: str | None, expected: str) -> None:
    assert vt.sanitize_layer_name(source_name) == expected


def test_build_ogr2ogr_cmd_without_s_srs() -> None:
    cmd = vt.build_ogr2ogr_cmd("ogr2ogr", Path("/in/a.shp"), Path("/work/out.geojson"))
    assert cmd == [
        "ogr2ogr",
        "-f",
        "GeoJSON",
        "-t_srs",
        "EPSG:4326",
        "/work/out.geojson",
        "/in/a.shp",
    ]


def test_build_ogr2ogr_cmd_with_s_srs() -> None:
    cmd = vt.build_ogr2ogr_cmd(
        "ogr2ogr", Path("/in/a.shp"), Path("/work/out.geojson"), "EPSG:32749"
    )
    assert cmd == [
        "ogr2ogr",
        "-f",
        "GeoJSON",
        "-t_srs",
        "EPSG:4326",
        "-s_srs",
        "EPSG:32749",
        "/work/out.geojson",
        "/in/a.shp",
    ]


def test_build_tippecanoe_cmd() -> None:
    cmd = vt.build_tippecanoe_cmd(
        "tippecanoe", Path("/work/tiles.mbtiles"), Path("/work/out.geojson"), "my_layer"
    )
    assert cmd == [
        "tippecanoe",
        "-o",
        "/work/tiles.mbtiles",
        "--force",
        "-Z0",
        "-zg",
        "--drop-densest-as-needed",
        "-l",
        "my_layer",
        "/work/out.geojson",
    ]


def test_build_pmtiles_cmd() -> None:
    cmd = vt.build_pmtiles_cmd(
        "pmtiles-convert", Path("/work/tiles.mbtiles"), Path("/out/my_layer.pmtiles")
    )
    assert cmd == [
        "pmtiles-convert",
        "--overwrite",
        "/work/tiles.mbtiles",
        "/out/my_layer.pmtiles",
    ]
