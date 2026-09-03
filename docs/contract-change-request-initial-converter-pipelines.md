# RFC: Contract for Initial Converter Pipelines

**Status:** Proposed  
**Reviewers:** Backend, DevOps, Frontend  
**Implementation repository:** `dtugm/naraga-converter`  
**Algorithm baseline:** `convertwin@a3917e5`

## Decision requested

Approve the contract and runtime requirements for the first five production
pipelines in `naraga-converter`:

1. `pipeline vector tiles`
2. `pipeline point cloud tiles`
3. `pipeline cityjson to citygml`
4. `pipeline citygml to 3dtiles`
5. `pipeline cityjson to 3dtiles`

This RFC does not change the OpenAPI contract or runtime. Implementation starts
only after the proposed contract has been approved and its generated artifacts
have been synchronized to every consumer.

## Context

`naraga-converter` currently exposes the standard NARAGA service lifecycle but
its `run_job()` function is still a simulation. The current contract can express
GeoJSON to PMTiles, LAS/LAZ to 3D Tiles, and GML to 3D Tiles, but it cannot fully
express the requested pipeline set:

- `DatasetFormat` has no `cityjson`, `gpkg`, or `mbtiles` values.
- `ConverterParams.target_format` has no `gml` or `mbtiles` values.
- `ConverterParams` forbids the pipeline-specific tuning parameters because it
  has `additionalProperties: false`.
- Converter capabilities advertise placeholder outputs that the initial runtime
  will not implement.
- ZIP packaging and output CRS semantics are not defined precisely enough for
  independently implemented gateway and converter components.

There is no separate `naraga-contract` repository. The team must therefore
agree which existing copy is authoritative before synchronization. This RFC
recommends treating `naraga-gateway/contract/openapi.yaml` as the source of truth
because the gateway owns external request validation and job orchestration.

## Goals and non-goals

### Goals

- Represent all five conversions without adding parallel service endpoints.
- Preserve the existing internal job, callback, timeout, and cancellation
  lifecycle.
- Expose the currently supported ConvertWin quality and tuning controls.
- Keep source and output packaging unambiguous.
- Allow the gateway and UI to derive valid conversion choices from
  `/v1/internal/converter/capabilities`.
- Port the algorithms without changing geometry, metadata, LOD, materials,
  reprojection, or default quality.

### Non-goals

- Adding IFC, terrain, raster-tile, Gaussian-splat, or optimization pipelines.
- Introducing a second upload API or multipart endpoint.
- Changing pricing beyond the existing size-based estimate.
- Changing public job lifecycle endpoints or callback delivery semantics.
- Treating ZIP as a new logical dataset format.

## Proposed conversion registry

| Pipeline name | Source formats | Target format | Output packaging |
| --- | --- | --- | --- |
| `pipeline vector tiles` | `geojson`, `gpkg`, `shp` | `pmtiles`, `mbtiles` | Raw single file |
| `pipeline point cloud tiles` | `las`, `laz` | `3dtiles` | ZIP |
| `pipeline cityjson to citygml` | `cityjson` | `gml` | Raw single file |
| `pipeline citygml to 3dtiles` | `gml` | `3dtiles` | ZIP |
| `pipeline cityjson to 3dtiles` | `cityjson` | `3dtiles` | ZIP |

The service selects a pipeline from
`input_datasets.input.dataset_format -> params.target_format`. A separate
`pipeline_id` request property is deliberately not introduced because it would
duplicate the source/target pair and create a conflict-precedence problem.

## Proposed OpenAPI changes

### Dataset formats

Add these values to `DatasetFormat`:

```yaml
- cityjson
- gpkg
- mbtiles
```

`gml`, `shp`, `geojson`, `las`, `laz`, `pmtiles`, and `3dtiles` already exist.

### Converter target formats

Add `gml` and `mbtiles` to `ConverterParams.target_format`. Existing enum values
may remain for backward compatibility, but the converter must advertise only
implemented pairs in its conversion matrix.

### Converter parameters

Extend `ConverterParams` with the following optional properties. The service
applies the defaults shown here and rejects parameters that are irrelevant to
the selected source/target pair.

```yaml
ConverterParams:
  type: object
  required: [target_format]
  additionalProperties: false
  properties:
    target_format:
      type: string
      enum: [3dtiles, pmtiles, mbtiles, gml, geojson, cog, las, laz, gltf]
    output_crs:
      oneOf: [{ $ref: "#/components/schemas/Crs" }, { type: "null" }]

    # pipeline vector tiles
    min_zoom: { type: integer, minimum: 0, maximum: 22, default: 0 }
    max_zoom:
      oneOf:
        - { type: integer, minimum: 0, maximum: 22 }
        - { type: "null" }
    layer_name:
      oneOf: [{ type: string, minLength: 1 }, { type: "null" }]
    drop_densest: { type: boolean, default: true }

    # pipeline point cloud tiles
    geoid: { type: string, enum: [Ellipsoid, EGM96], default: Ellipsoid }
    proj:
      oneOf: [{ type: string, minLength: 1 }, { type: "null" }]
    point_ratio:
      oneOf:
        - { type: number, exclusiveMinimum: 0, maximum: 1 }
        - { type: "null" }
    max_points:
      oneOf: [{ type: integer, minimum: 1 }, { type: "null" }]
    source_precision: { type: boolean, default: false }
    force_4byte_rgb: { type: boolean, default: false }
    quantize: { type: boolean, default: false }
    min_lod:
      oneOf: [{ type: integer, minimum: 0 }, { type: "null" }]
    max_lod:
      oneOf: [{ type: integer, minimum: 0 }, { type: "null" }]

    # pipeline cityjson to citygml
    citygml_version:
      type: string
      enum: ["1.0", "2.0", "3.0"]
      default: "2.0"

    # CityGML-based 3D Tiles pipelines
    draco: { type: boolean, default: true }
    draco_level: { type: integer, minimum: 0, maximum: 10, default: 7 }
    draco_quantize: { type: integer, minimum: 8, maximum: 16, default: 14 }
    dropped_columns:
      oneOf: [{ type: string }, { type: "null" }]
    strip_attributes: { type: boolean, default: false }
    tile_max_mb:
      oneOf:
        - { type: number, exclusiveMinimum: 0, default: 48 }
        - { type: "null" }
    features_per_part:
      oneOf: [{ type: integer, minimum: 1 }, { type: "null" }]
    max_part_size_mb:
      oneOf:
        - { type: number, exclusiveMinimum: 0 }
        - { type: "null" }
```

Cross-field validation remains service-owned because OpenAPI cannot select a
parameter subset based on the format of `input_datasets.input`:

- `max_zoom` must be greater than or equal to `min_zoom`.
- `max_lod` must be greater than or equal to `min_lod` when both are present.
- `proj`, when present, overrides the source CRS for mago-3d-tiler.
- `max_part_size_mb`, when present, overrides `features_per_part`.
- `citygml_version` applies only to the standalone `cityjson -> gml` pipeline.
  The chained `cityjson -> 3dtiles` pipeline always uses CityGML 2.0 internally.
- Draco parameters have no effect when `draco` is false.

### Service registry and capabilities

Set the initial implemented output list to:

```yaml
converter:
  output_formats: [3dtiles, pmtiles, mbtiles, gml]
```

The converter must return this matrix from `GET
/v1/internal/converter/capabilities`:

```json
{
  "conversion_matrix": {
    "geojson": ["pmtiles", "mbtiles"],
    "gpkg": ["pmtiles", "mbtiles"],
    "shp": ["pmtiles", "mbtiles"],
    "las": ["3dtiles"],
    "laz": ["3dtiles"],
    "cityjson": ["gml", "3dtiles"],
    "gml": ["3dtiles"]
  }
}
```

The previous placeholder outputs `geojson`, `cog`, `las`, `laz`, and `gltf`
must not be advertised until a real conversion pair is implemented for them.

## Request examples

All examples below are fragments of `InternalJobRequest.params`. The surrounding
job envelope, authentication, signed URLs, callback URL, and output prefix remain
unchanged.

### Vector data to PMTiles

Input `dataset_format` is `geojson`, `gpkg`, or `shp`:

```json
{
  "target_format": "pmtiles",
  "output_crs": "EPSG:4326",
  "min_zoom": 0,
  "max_zoom": 16,
  "layer_name": "buildings",
  "drop_densest": true
}
```

### Point cloud to 3D Tiles

Input `dataset_format` is `las` or `laz`:

```json
{
  "target_format": "3dtiles",
  "geoid": "Ellipsoid",
  "proj": null,
  "point_ratio": 1.0,
  "max_points": 65536,
  "source_precision": false,
  "force_4byte_rgb": false,
  "quantize": false,
  "min_lod": null,
  "max_lod": null
}
```

### CityJSON to CityGML

Input `dataset_format` is `cityjson`:

```json
{
  "target_format": "gml",
  "citygml_version": "2.0"
}
```

### CityGML to 3D Tiles

Input `dataset_format` is `gml`:

```json
{
  "target_format": "3dtiles",
  "draco": true,
  "draco_level": 7,
  "draco_quantize": 14,
  "dropped_columns": "",
  "strip_attributes": false,
  "tile_max_mb": 48,
  "features_per_part": null,
  "max_part_size_mb": null
}
```

### CityJSON to 3D Tiles

Input `dataset_format` is `cityjson`. The intermediate CityGML version is fixed
to 2.0 and is not uploaded:

```json
{
  "target_format": "3dtiles",
  "draco": true,
  "draco_level": 7,
  "draco_quantize": 14,
  "dropped_columns": "",
  "strip_attributes": false,
  "tile_max_mb": 48,
  "features_per_part": null,
  "max_part_size_mb": null
}
```

## Packaging rules

ZIP is transport packaging, not a `DatasetFormat` value:

- A ZIP containing Shapefile sidecars is reported as `dataset_format: shp`.
- A ZIP containing one CityJSON document is reported as
  `dataset_format: cityjson`.
- A ZIP containing one CityGML document is reported as `dataset_format: gml`.
- A ZIP containing one LAS/LAZ file is reported as `las`/`laz`.
- PMTiles, MBTiles, and standalone CityGML outputs are uploaded as raw files.
- A 3D Tiles directory is uploaded as `<source-stem>_3dtiles.zip` and reported as
  `dataset_format: 3dtiles`.

For input, `ResolvedDataset.name` carries the `.zip` suffix. For output, the
gateway must issue a `storage_key` ending in `.zip` for `3dtiles`; no new schema
property is required. Archive extraction must reject absolute paths, `..` path
segments, and symlink entries.

## CRS and spatial metadata

Proposed normative behavior:

- PMTiles/MBTiles output: `crs = EPSG:4326`; vector sources are reprojected by
  `ogr2ogr` before tiling.
- CityGML output: use `params.output_crs` when explicitly supported by the
  converter; otherwise retain the source dataset CRS.
- 3D Tiles output: `crs = EPSG:4978`, reflecting the ECEF root transform used by
  the tileset. Preserve the source geographic extent in `bbox`, which is always
  EPSG:4326 under the existing contract.
- If `output_crs` requests a value a selected pipeline cannot produce, reject the
  job before acceptance with `VALIDATION_ERROR`; do not silently ignore it.

Backend reviewers should explicitly approve or revise this section because the
current contract does not define whether a 3D Tiles dataset records its source
CRS or its encoded ECEF CRS.

## Validation and errors

Before returning `202`, the converter validates:

- `service` is `converter` and `model` is null.
- `input_datasets` contains exactly one key named `input`.
- Exactly one `output_upload_urls` entry matches `params.target_format`.
- The source/target pair exists in the conversion matrix.
- Only parameters belonging to the selected pipeline are supplied.
- Input filename/packaging agrees with the logical dataset format.

An unsupported source/target pair returns `422` with
`UNSUPPORTED_CONVERSION`. Invalid parameters or packaging return `422` with
`VALIDATION_ERROR`. Runtime tool or storage failures produce a terminal failed
callback. The existing `JOB_TIMEOUT`, cancellation, callback sequencing,
idempotency, and durable outbox behavior remain unchanged.

## Runtime and DevOps impact

The production image must pin the same tool family used by the algorithm
baseline:

| Component | Purpose | Proposed version/source |
| --- | --- | --- |
| Java | Runs mago and citygml-tools | Temurin JRE 21 |
| mago-3d-tiler | LAS/LAZ to 3D Tiles | 1.15.4 |
| citygml-tools | CityJSON to CityGML | 2.5.0 |
| tippecanoe | Vector tiling | conda-forge package, lock resolved build |
| GDAL/ogr2ogr | GPKG/SHP conversion | 3.9 series |
| pmtiles CLI | MBTiles to PMTiles | Python package 3.7 series |
| Node.js + gltf-pipeline | Optional Draco compression | Node 18+; npm version pinned in image |
| Python geo libraries | CityGML tiler | NumPy, lxml, pyproj, pygltflib pinned by lockfile |

Operational requirements:

- Conversion runs in a dedicated process group, not the Uvicorn event loop.
- Cancel and timeout terminate the complete process group.
- Scratch space must accommodate input, intermediate CityGML, uncompressed
  3D Tiles, and final ZIP concurrently.
- Signed URLs are credentials and must never appear in logs or exception text.
- Parent process emits heartbeat callbacks even when an external tool reports no
  progress update.
- Readiness reports required binary/JAR availability; liveness remains
  dependency-free.

DevOps should approve image size, Java heap, worker memory/CPU, scratch volume,
maximum upload size, and timeout defaults before rollout.

## Compatibility and rollout

1. Approve this RFC and assign an authoritative OpenAPI copy.
2. Update that OpenAPI file and increment `x-contract-version`.
3. Regenerate Python and TypeScript contract artifacts.
4. Synchronize the same OpenAPI revision and generated artifacts to converter,
   gateway, frontend, and any service whose CI checks the shared contract.
5. Merge the synchronized contract before enabling new gateway/UI choices.
6. Implement the five converter pipelines in separate commits.
7. Deploy converter with the new capabilities while the gateway keeps choices
   capability-driven.
8. Run one small real fixture for each pipeline, followed by a representative
   production-size canary.

Rollback is capability-based: remove the affected pair from the converter's
reported conversion matrix so the gateway/UI stops offering it, then roll back
the converter image. Existing datasets and completed jobs remain valid.

## Acceptance criteria

- The generated contract accepts all five request shapes and rejects unknown
  parameters.
- Gateway, converter, and frontend use the same contract version.
- Capabilities expose exactly the implemented source/target pairs.
- Swagger UI renders typed fields and valid examples for all five pipelines.
- Cancellation stops child processes and sends no later callback.
- Timeout produces `JOB_TIMEOUT` and cleans staging data.
- PMTiles/MBTiles layer metadata and CityGML/3D Tiles geometry, material,
  feature, transform, and LOD invariants match `convertwin@a3917e5`.
- The chained CityJSON to 3D Tiles output matches standalone L followed by B.
- Docker build, unit/integration tests, Ruff, and mypy pass.

## Review checklist

### Backend

- [ ] Approve format enum additions and target formats.
- [ ] Approve parameter names, bounds, defaults, and irrelevant-parameter rejection.
- [ ] Approve conversion matrix and capabilities behavior.
- [ ] Approve ZIP and CRS semantics.
- [ ] Confirm which OpenAPI copy is authoritative.

### DevOps

- [ ] Approve pinned external tools and image build strategy.
- [ ] Approve Java heap, CPU/memory, scratch storage, and timeout budgets.
- [ ] Confirm process-group termination works in the deployment runtime.
- [ ] Confirm readiness checks and observability requirements.

### Frontend

- [ ] Confirm the UI derives available conversions from capabilities.
- [ ] Confirm advanced parameters can be rendered from the approved schema.
- [ ] Confirm `.zip`, `.gml`, `.pmtiles`, and `.mbtiles` download behavior.

