"""Generate tiny synthetic inputs for smoke tests of the three priority conversions.

    python tests/fixtures/make_fixtures.py [OUT_DIR]      (default: data-io/fixtures)

Writes (all < 1 MB):
  area.geojson      3 building footprints in Yogyakarta (EPSG:4326)   -> pmtiles
  area_shp.zip      same features as a zipped shapefile (needs GDAL)   -> pmtiles
  classified.laz    60x60 m ASPRS-classified cloud, EPSG:32749:
                    ground (2) at ~100 m, a 10x10 m building (6) 12 m tall,
                    a road strip (11)                                  -> 3dtiles, cog
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import numpy as np


def _geojson(out: Path) -> Path:
    def square(lon: float, lat: float, d: float = 0.0002) -> list[list[list[float]]]:
        return [[[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]]

    features = [
        {
            "type": "Feature",
            "properties": {"id": i, "height": 6 + 3 * i},
            "geometry": {"type": "Polygon", "coordinates": square(110.3700 + i * 0.0005, -7.7700)},
        }
        for i in range(3)
    ]
    path = out / "area.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return path


def _shapefile_zip(geojson: Path, out: Path) -> Path | None:
    try:
        from osgeo import gdal
    except ImportError:
        print("skip area_shp.zip (GDAL not installed; run inside the dev image)")
        return None
    shp_dir = out / "area_shp"
    shp_dir.mkdir(exist_ok=True)
    gdal.VectorTranslate(str(shp_dir / "area.shp"), str(geojson), format="ESRI Shapefile")
    path = out / "area_shp.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for part in sorted(shp_dir.iterdir()):
            zf.write(part, part.name)
            part.unlink()
    shp_dir.rmdir()
    return path


def _classified_laz(out: Path) -> Path:
    import laspy
    from pyproj import CRS

    rng = np.random.default_rng(42)
    x0, y0, z0 = 430000.0, 9140000.0, 100.0
    gx, gy = np.meshgrid(np.arange(0, 60, 0.5), np.arange(0, 60, 0.5))
    gx, gy = gx.ravel(), gy.ravel()
    in_building = (gx >= 25) & (gx < 35) & (gy >= 25) & (gy < 35)
    on_road = (gy >= 5) & (gy < 9)
    ground = ~in_building & ~on_road

    bx, by = np.meshgrid(np.arange(25, 35, 0.4), np.arange(25, 35, 0.4))
    xs = np.concatenate([gx[ground], gx[on_road], bx.ravel()])
    ys = np.concatenate([gy[ground], gy[on_road], by.ravel()])
    zs = np.concatenate([
        z0 + 0.02 * gx[ground] + rng.normal(0, 0.03, ground.sum()),
        z0 + 0.02 * gx[on_road] - 0.1,
        np.full(bx.size, z0 + 0.6 + 12.0),
    ])
    cls = np.concatenate([
        np.full(ground.sum(), 2), np.full(on_road.sum(), 11), np.full(bx.size, 6)
    ]).astype(np.uint8)

    header = laspy.LasHeader(point_format=6, version="1.4")
    header.offsets = [x0, y0, 0.0]
    header.scales = [0.01, 0.01, 0.01]
    header.add_crs(CRS.from_epsg(32749))
    las = laspy.LasData(header)
    las.x, las.y, las.z = x0 + xs, y0 + ys, zs
    las.classification = cls
    path = out / "classified.laz"
    las.write(path)
    return path


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "data-io/fixtures")
    out.mkdir(parents=True, exist_ok=True)
    geojson = _geojson(out)
    made = [geojson, _shapefile_zip(geojson, out), _classified_laz(out)]
    for path in filter(None, made):
        print(f"{path}  {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
