# CLAUDE.md

Guidance for Claude Code in this repository.

## What this is

NARAGA **Converter** — FastAPI service on port **8085**, package `converter`, path
segment `converter`. One of five Python AI/geo services in the NARAGA mesh, and the
only one with **no model**: it does format transcoding, not inference. The NestJS
gateway at **:8080** owns the database, mints signed URLs, and dispatches jobs here over
the internal contract; this service does the work and **calls back** to
`request.callback_url`. It never touches Postgres.

## ⚠️ Temporary file: `src/converter/e2e_diagnostics.py`

Added **2026-09-21** for end-to-end verification. It mounts two extra routes under the
same `PREFIX` with the same bearer auth:

- `GET  /v1/internal/converter/_e2e/selftest` — identity, contract version, store row
  counts. No side effects.
- `POST /v1/internal/converter/_e2e/roundtrip` — GETs a signed input URL and PUTs to a
  signed output URL, proving the object-storage data plane that the stubbed `run_job()`
  never exercises.

**Neither is in `contract/openapi.yaml`** — deliberate, hence the `_e2e` namespace.
**Removal when E2E verification is finished: delete the file plus the two lines in
`main.py`** that import it (`from . import e2e_diagnostics, jobs`) and include it
(`app.include_router(e2e_diagnostics.router)`). Do not build anything on top of it.

## Commands

```bash
uv sync                    # API layer only; geo/ML wheels: uv sync --extra geo
uv run pytest
uv run ruff check .        # CI runs this WITHOUT --fix: it must fail, not rewrite
uv run mypy                # strict; files = src, tests
uv run uvicorn converter.main:app --app-dir src --port 8085 --reload
docker compose up          # Dockerfile.dev: GDAL 3.9 + PDAL 2.8 + laspy from conda-forge
```

`INTERNAL_SERVICE_TOKEN` has no default, so a native run needs a `.env` with it set
(or the app raises at import). GDAL/PDAL are **not** pip-installable and this service
is nothing *but* GDAL/PDAL translation — in practice it only runs meaningfully in
Docker.

## Architecture

Five contract-mandated internal ops under `PREFIX = /v1/internal/converter`, **all** requiring
`Authorization: Bearer $INTERNAL_SERVICE_TOKEN`: `POST /jobs` (validate →
`store.insert_job` → 202 + `credits_estimated`, work runs as an asyncio task),
`DELETE /jobs/{id}` (mark cancelled, cancel the task, 202), `GET /jobs/{id}/status`,
`POST /estimate`, and `GET /capabilities` (contract_version 2.1.0, `MODELS` — empty here, `OUTPUT_FORMATS`).

`/health` and `/ready` on the app (not the router) are **unauthenticated** — `/health`
must never touch a dependency, since failing it restarts the container.

`run_job(request, report_progress)` in `jobs.py` is the seam. `_execute()` wraps it in
`asyncio.timeout(max_job_duration_seconds)` and maps every outcome to a callback.
`StateStore` (`store.py`) is a durable SQLite file at `STATE_DB_PATH` (default
`data/state.db`, WAL + `synchronous=FULL`) with two tables: `jobs` (idempotency PK,
status/progress, `last_sequence`, `callback_url`) and `outbox` (unacked terminal
callbacks). On startup `jobs.startup()` reports orphaned `processing` rows as `failed`
and launches `drain_outbox_forever`, which retries every
`OUTBOX_DRAIN_INTERVAL_SECONDS`.

## THE RULES — invariants that must not be "cleaned up"

- **`run_job()` is the ONLY thing to replace.** Idempotency, callback sequencing, the
  outbox, startup recovery and cancellation are contract rules that are easy to get
  wrong; they are already right.
- **Terminal callbacks go into the outbox BEFORE the first send attempt**
  (`callbacks.py` `CallbackSender.send`). Reversing the order means a crash between
  send and persist silently loses finished work. The gateway dedups on `sequence`, so a
  duplicate delivery is free; a lost one is not.
- **Idempotency on `job_id` via the SQLite PRIMARY KEY → 409 on replay.** The gateway
  treats 409 as **success** (it means "I already have it"), so never downgrade it to
  200 or re-accept the job.
- **Report ONLY `processing` | `complete` | `failed`.** `draft`/`queued`/`cancelled` are
  gateway-owned states; emitting one corrupts the gateway's state machine.
- **Progress callbacks are disposable** — one attempt, warn on failure; retrying only
  delays the next heartbeat. **Terminal callbacks retry forever until acked.** A **4xx
  is never retried** in either path: identical bytes fail identically.
- **`emitted_at` must be RFC3339 with exactly milliseconds and a literal `Z`.**
  `build_callback()` validates against `JobCallback` but **sends the hand-built dict**,
  because `model_dump()` would re-serialize to microseconds and `+00:00`, which the
  gateway rejects. Do not "simplify" this to `model_dump()`.
- **After a cancel, send NOTHING further** — not a `cancelled`, not a `failed`. The
  `asyncio.CancelledError` branch in `_execute` deliberately logs and returns.
- **Write outputs ONLY under `request.output_prefix`** (use the `storage_key` from
  `output_upload_urls[n]`). The gateway will not mint a dataset outside that prefix.
- **Conversion work runs in the `pipeline_worker` subprocess, never in the event loop.**
  A GDAL/PDAL/mago run on a multi-GB input would otherwise block the loop, freeze
  `/health`, and get the container killed mid-job; the process group is also what lets
  cancel/timeout kill child tools.
- **Never hand-edit `src/converter/contract/models.py` or `contract/openapi.yaml`** —
  generated/vendored, CI regenerates and diffs byte-for-byte. Change the canonical spec
  in `dtugm/naraga-contract` and let propagation regenerate here. (`docs/` holds a
  change *request*, not a change.)
- **Look up ports and package names in `contract/services.yaml`, never derive them from
  the repo name** — `landcover_detection` → `naraga-land-cover-detection` and
  `reconstruction_3d` → `naraga-3d-reconstruction` both break naive string transforms.
- Never log `signed_url` values or the internal token; signed URLs are credentials.

## Current state: three real conversions (contract 2.1.0)

`run_job()` → `runtime.execute_conversion()`: download the signed input into a per-job
scratch dir (zip inputs extracted safely by `archives.py`), run
`python -m converter.pipeline_worker` in **its own process group** (`processes.py`;
cancel/timeout kill the whole group), upload the single artifact to
`output_upload_urls[0]`, delete scratch. Signed URLs never reach the worker.

| source → target | module | tools | artifact / CRS recorded |
|---|---|---|---|
| geojson, shp(zip) → pmtiles | `pipelines/vector_tiles.py` | ogr2ogr → tippecanoe → pmtiles-convert | `<stem>.pmtiles`, EPSG:4326 |
| las, laz → 3dtiles | `pipelines/point_cloud_tiles.py` | mago-3d-tiler 1.15.4 (Java 21) | `<stem>_3dtiles.zip`, EPSG:4978 |
| las, laz → cog | `pipelines/point_cloud_to_dem.py` | PDAL + rasterio | `<stem>_dem.tif`, 3 bands DTM / DSM / BHM, source CRS |

- The matrix lives in `pipeline_registry.PIPELINES` and is published as
  `conversion_matrix` in `/capabilities`. Only `target_format`/`output_crs` params are
  accepted until contract 3.0.0 adds tuning params.
- **Never pass `-ge` to mago 1.15.4** — `-ge Ellipsoid` crashes it.
- Worker exit code **3** = unusable user input (`ConversionInputError`) → `failed` with
  `VALIDATION_ERROR` (gateway: no refund). Any other failure → `INTERNAL_ERROR` (refund).
- Workers print only `{"progress": n}` JSON lines on stdout; runtime maps them to 10–90.
- The gateway issues keys like `output.3dtiles` (no `.zip`); accept whatever key it sends.
- `src/converter/cli.py`, `scripts/fake_gateway.py`, `scripts/smoke_conversions.sh` and
  `tests/fixtures/make_fixtures.py` are dev/test tools, not production endpoints.

## Config (`config.py` — nothing reads `os.environ` directly)

`INTERNAL_SERVICE_TOKEN` (**no default — crashes at boot by design**),
`SERVICE_NAME` (`converter`), `LOG_LEVEL` (`INFO`), `STATE_DB_PATH`
(`data/state.db` — mount a volume), `OUTBOX_DRAIN_INTERVAL_SECONDS` (`30.0`),
`CONVERTER_STAGING_ROOT` (`/tmp/naraga-converter`), tool paths `JAVA_BIN`,
`MAGO_TILER_JAR`, `OGR2OGR_BIN`, `TIPPECANOE_BIN`, `PMTILES_BIN`, `PDAL_BIN` (all
checked by `/ready`).

## Testing

`uv run pytest`. `tests/conftest.py` sets `INTERNAL_SERVICE_TOKEN=test-token` at import
time and has an **autouse `_fresh_state` fixture** giving every test its own
`STATE_DB_PATH` under `tmp_path`, a 0.05s drain interval, and a cleared
`get_settings` cache.

- `_install_gateway_sink(events)` swaps `app.state.http` for an `httpx.MockTransport`
  client that asserts the bearer header and records payloads. **Call it INSIDE the
  `with TestClient(app)` block** — the lifespan overwrites `app.state.http` on entry,
  so installing it before the block is silently discarded.
- **Two sequential `TestClient(app)` blocks against the same state db simulate a
  restart** (`test_restart_recovers_orphans_and_keeps_idempotency`): the first exits
  mid-job, the second must 409 the resubmit, report the orphan failed, and drain it.

`tests/test_contract.py` cross-checks `CONTRACT_VERSION` against
`contract/openapi.yaml` and `MODELS`/`OUTPUT_FORMATS` against `contract/services.yaml`
— for this service that asserts `MODELS == []`.

## Service specifics

- **No models.** `MODELS: list[str] = []` and `Job.model` is **`null`** on the wire
  (CONTRACT-CHANGES A7) — the test `SAMPLE_REQUEST` has `"model": None`. Never invent a
  placeholder model name to make a shape "consistent" with the other four services.
- Advertised `output_formats` mirror `services.yaml` (contract test); the real, narrower
  set of supported pairs is `conversion_matrix`.
- Inputs: exactly **`{input}`**, required — a generic key, not a role-specific one.
  (`dataset_role` on the dataset still says what it is, e.g. `point_cloud`.)
- **`params` is the odd one out: `{target_format, output_crs?}` — `target_format`
  singular, required, and there is NO `output_formats` array and NO `model` field.**
  Every other service takes `{model, output_formats[], ...}`. Do not "harmonize" it.
- The legal `(source_format → target_format)` pairs are **not** in the spec (all 119
  were left unspecified, CONTRACT-CHANGES D11); they are meant to be served at runtime
  from `GET /v1/internal/converter/capabilities` so the UI never offers an impossible
  conversion. `/capabilities` publishes them as `conversion_matrix` (a field that
  already exists in contract 2.1.0).
