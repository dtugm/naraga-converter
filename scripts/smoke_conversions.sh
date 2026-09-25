#!/usr/bin/env bash
# Run the three priority conversions on synthetic fixtures inside the geo image.
# Usage (inside the container): bash scripts/smoke_conversions.sh [WORK_DIR]
set -euo pipefail
# Settings() refuses to load without a token (crash-at-boot rule for the service). The
# smoke run never serves or calls back, so a throwaway value is fine here (e.g. in CI's
# bare `docker run`, which has no .env).
export INTERNAL_SERVICE_TOKEN="${INTERNAL_SERVICE_TOKEN:-smoke-test-only}"
work="${1:-/tmp/smoke}"
fx="$work/fixtures"; out="$work/out"
python tests/fixtures/make_fixtures.py "$fx"
python -m converter.cli --input "$fx/area.geojson" --to pmtiles --out "$out"
python -m converter.cli --input "$fx/area_shp.zip" --to pmtiles --out "$out"
python -m converter.cli --input "$fx/classified.laz" --to 3dtiles --out "$out"
python -m converter.cli --input "$fx/classified.laz" --to cog --out "$out"
python - "$out" <<'PY'
import sys, zipfile, pathlib, rasterio
out = pathlib.Path(sys.argv[1])
for name in ("area.pmtiles", "area_shp.pmtiles"):
    assert (out / name).read_bytes()[:7] == b"PMTiles", name
assert "tileset.json" in zipfile.ZipFile(out / "classified_3dtiles.zip").namelist()
with rasterio.open(out / "classified_dem.tif") as ds:
    assert ds.count == 3 and ds.descriptions[0] == "DTM", ds.descriptions
    assert ds.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG"
print("SMOKE OK:", sorted(p.name for p in out.iterdir()))
PY
