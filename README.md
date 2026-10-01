# naraga-converter

NARAGA **Converter** service — FastAPI, port `8085`.

Converts geospatial formats for the NARAGA gateway: health probes, the five internal endpoints the contract mandates, a job runner with **durable state** (SQLite: restart-proof idempotency, orphan recovery, and a terminal-callback outbox), and a contract-correct callback client (auth, monotonic `sequence`, RFC3339-ms-`Z` timestamps).

## Conversions (contract 2.1.0)

| Source → target | Output | Tools |
| --- | --- | --- |
| `geojson`, `shp` (zipped) → `pmtiles` | `<name>.pmtiles`, EPSG:4326 | ogr2ogr → tippecanoe → pmtiles-convert |
| `las`, `laz` → `3dtiles` | `<name>_3dtiles.zip` (tileset.json at root), EPSG:4978 | mago-3d-tiler 1.15.4 (Java 21) |
| `las`, `laz` → `cog` | `<name>_dem.tif`: band 1 DTM, 2 DSM (terrain + buildings), 3 building height | PDAL + rasterio |

`las/laz → cog` needs an ASPRS-classified cloud (ground `2` and building `6`) in a projected, metre-based CRS. The supported pairs are published as `conversion_matrix` in `GET /capabilities`. An unusable input fails with `VALIDATION_ERROR` (no refund); anything else fails with `INTERNAL_ERROR`.

## Run

```bash
docker compose up
```

Docker is the supported path — the image installs GDAL and PDAL from conda-forge.
The PyPI `pdal` package is only a binding and won't build without them.

Native (API layer only, no GDAL/PDAL):

```bash
uv sync && uv run uvicorn converter.main:app --app-dir src --port 8085 --reload
```

<http://localhost:8085/health> · docs at `/docs`

## Test a conversion locally

Put input files in `./data-io/` (git-ignored, mounted at `/data`), then:

```bash
# direct, no API
docker compose run --rm converter python -m converter.cli --input /data/area.laz --to 3dtiles --out /data/out

# through the real job API: serves the input, receives the upload and prints callbacks
cd data-io && python3 ../scripts/fake_gateway.py --input area.laz --format las --to cog --crs EPSG:32748
# add --serve-only to send the POST yourself from Swagger (/docs) or Postman

# synthetic fixtures + all conversions (what CI runs)
docker compose run --rm converter bash scripts/smoke_conversions.sh
```

## Commands

| Task                  | Command                                                        |
| --------------------- | -------------------------------------------------------------- |
| Test                  | `uv run pytest`                                                |
| Lint / format / types | `uv run ruff check .` · `uv run ruff format .` · `uv run mypy` |
| Add a dependency      | `uv add <pkg>`                                                 |

## What's implemented

All five mandatory internal operations, under `PREFIX` (defined in `jobs.py`):

| Method | Path                            | Returns                                                                  |
| ------ | ------------------------------- | ------------------------------------------------------------------------ |
| POST   | `{PREFIX}/jobs`                 | `202 {accepted, job_id, credits_estimated}`; `409` on duplicate `job_id` |
| DELETE | `{PREFIX}/jobs/{job_id}`        | `202` — stops work, no further callbacks                                 |
| GET    | `{PREFIX}/jobs/{job_id}/status` | `{job_id, status, progress_percent}`                                     |
| POST   | `{PREFIX}/estimate`             | credits + breakdown (naive size-based — replace)                         |
| GET    | `{PREFIX}/capabilities`         | models, formats, contract version                                        |

The gateway calls `POST /jobs` and does **not** wait. The service accepts, returns 202, runs the conversion in a worker process, and reports back by POSTing to the job's `callback_url` via `callbacks.CallbackSender`:

```json
{
  "job_id": "...",
  "sequence": 2,
  "emitted_at": "2026-09-01T03:00:00.000Z",
  "status": "processing",
  "progress_percent": 30,
  "output_datasets": [],
  "result_summary": null,
  "credits_used": null,
  "error_code": null,
  "error_message": null
}
```

## Contract

`contract/openapi.yaml` is vendored from
[naraga-contract](https://github.com/dtugm/naraga-contract) and
`src/converter/contract/models.py` is generated from it. **Never hand-edit either** — CI regenerates and fails on any difference. Change the contract there; it arrives here as a sync PR.
