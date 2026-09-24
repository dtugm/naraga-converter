"""Local dev CLI: convert one file without the gateway.

    python -m converter.cli --input area.laz --to 3dtiles --out ./out [--crs EPSG:32749]

Runs the same pipeline code as a job (pipeline_worker.execute) in this process, so
progress lines and errors look exactly like the worker's. Not used in production.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from .archives import extract_zip
from .pipeline_registry import validate_pipeline_request
from .pipeline_worker import execute
from .worker_api import ConversionInputError

_FORMAT_BY_SUFFIX = {".geojson": "geojson", ".json": "geojson", ".zip": "shp",
                     ".las": "las", ".laz": "laz"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m converter.cli")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--to", required=True, choices=["pmtiles", "3dtiles", "cog"])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--crs", default=None, help="source CRS if the file lacks one")
    parser.add_argument("--format", default=None, help="override source format detection")
    args = parser.parse_args(argv)

    source = args.format or _FORMAT_BY_SUFFIX.get(args.input.suffix.lower())
    if source is None:
        parser.error(f"cannot detect format of {args.input.name}; pass --format")
    pipeline = validate_pipeline_request(source, args.to, {"target_format": args.to})

    work = Path(tempfile.mkdtemp(prefix="converter-cli-"))
    try:
        inputs = extract_zip(args.input, work / "in") if source == "shp" else [args.input]
        spec = {
            "pipeline": pipeline,
            "source_format": source,
            "target_format": args.to,
            "input_paths": [str(p.resolve()) for p in inputs],
            "source_name": args.input.name,
            "source_crs": args.crs,
            "source_bbox": None,
            "params": {"target_format": args.to},
            "output_dir": str(work / "out"),
        }
        try:
            result = execute(spec)
        except ConversionInputError as exc:
            print(f"input error: {exc}", file=sys.stderr)
            return 3
        args.out.mkdir(parents=True, exist_ok=True)
        final = args.out / result["name"]
        shutil.move(result["artifact"], final)
        result["artifact"] = str(final)
        print(json.dumps(result), file=sys.stderr)
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
