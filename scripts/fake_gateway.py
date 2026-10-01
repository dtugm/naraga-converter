"""Local stand-in for the NARAGA gateway + object storage (dev only, stdlib only).

Serves the input file (GET /input), accepts the output upload (PUT /output -> ./out/),
prints every callback (POST /callback), and submits one job to the converter.

    python scripts/fake_gateway.py --input data-io/area.laz --format laz --to 3dtiles \
        --crs EPSG:32749
    # add --serve-only to send the POST yourself from Postman or Swagger (/docs)

The converter runs in Docker, so by default it reaches this script through
host.docker.internal (Docker Desktop). Ctrl-C to stop after the terminal callback.
"""

from __future__ import annotations

import argparse
import json
import threading
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ARGS: argparse.Namespace
DONE = threading.Event()


def _ts(delta: timedelta = timedelta(hours=2)) -> str:
    return (datetime.now(UTC) + delta).strftime("%Y-%m-%dT%H:%M:%S.") + "000Z"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_: object) -> None:  # keep the console to callbacks only
        pass

    def _reply(self, code: int, body: bytes = b"") -> None:
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        data = ARGS.input.read_bytes()
        self._reply(200, data) if self.path == "/input" else self._reply(404)

    def do_PUT(self) -> None:
        size = int(self.headers.get("Content-Length", 0))
        out = Path("out") / ARGS.output_name
        out.parent.mkdir(exist_ok=True)
        remaining = size
        with out.open("wb") as fh:
            while remaining:
                chunk = self.rfile.read(min(remaining, 1 << 20))
                if not chunk:
                    break
                fh.write(chunk)
                remaining -= len(chunk)
        print(f"[upload] {out} ({size} bytes)")
        self._reply(200)

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        status = body.get("status")
        print(f"[callback] seq={body.get('sequence')} status={status} "
              f"progress={body.get('progress_percent')} "
              f"error={body.get('error_code')} {body.get('error_message') or ''}")
        if status == "complete":
            print(json.dumps(body.get("output_datasets"), indent=2))
        self._reply(200, b'{"received": true}')
        if status in {"complete", "failed"}:
            DONE.set()


def main() -> None:
    global ARGS
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--format", required=True, help="geojson | shp | las | laz")
    p.add_argument("--to", required=True, help="pmtiles | 3dtiles | cog")
    p.add_argument("--crs", default=None)
    p.add_argument("--converter", default="http://localhost:8085")
    p.add_argument("--token", default="dev-internal-token")
    p.add_argument("--port", type=int, default=8099)
    p.add_argument("--host-url", default=None, help="URL the converter uses to reach us")
    p.add_argument(
        "--serve-only",
        action="store_true",
        help="don't submit: print the request body for Postman/Swagger and wait for callbacks",
    )
    ARGS = p.parse_args()
    if ARGS.crs is None:  # the contract requires a declared CRS, as the real gateway sends
        if ARGS.format not in {"geojson", "shp"}:
            p.error("--crs is required for point clouds (e.g. --crs EPSG:32749)")
        ARGS.crs = "EPSG:4326"
    ext = {"cog": "tif", "3dtiles": "zip"}.get(ARGS.to, ARGS.to)  # openable locally
    ARGS.output_name = f"{ARGS.input.stem}.{ext}"
    base = ARGS.host_url or f"http://host.docker.internal:{ARGS.port}"

    server = ThreadingHTTPServer(("0.0.0.0", ARGS.port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    job_id = str(uuid.uuid4())
    prefix = f"jobs/{job_id}/outputs/"
    request = {
        "job_id": job_id,
        "service": "converter",
        "model": None,
        "input_datasets": {"input": {
            "dataset_id": str(uuid.uuid4()),
            "name": ARGS.input.name,
            "dataset_format": ARGS.format,
            "dataset_role": None,
            "size_bytes": ARGS.input.stat().st_size,
            "crs": ARGS.crs,
            "bbox": None,
            "signed_url": f"{base}/input",
            "signed_url_expires_at": _ts(),
        }},
        "params": {"target_format": ARGS.to},
        "output_prefix": prefix,
        "output_upload_urls": [{
            "output_format": ARGS.to,
            "storage_key": f"{prefix}output.{ARGS.to}",
            "url": f"{base}/output",
            "expires_at": _ts(),
        }],
        "callback_url": f"{base}/callback",
        "max_job_duration_seconds": 7200,
        "heartbeat_interval_seconds": 10,
    }
    if ARGS.serve_only:
        print(f"POST {ARGS.converter}/v1/internal/converter/jobs")
        print(f"Authorization: Bearer {ARGS.token}")
        print("Body (paste into Postman/Swagger; job_id must be new each time):")
        print(json.dumps(request, indent=2))
        print("\nWaiting for callbacks... (Ctrl-C to quit)")
        DONE.wait()
        server.shutdown()
        return
    req = urllib.request.Request(
        f"{ARGS.converter}/v1/internal/converter/jobs",
        data=json.dumps(request).encode(),
        headers={"Authorization": f"Bearer {ARGS.token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            print(f"[submit] {resp.status} {resp.read().decode()}")
    except urllib.error.HTTPError as exc:
        print(f"[submit] {exc.code} {exc.read().decode()}")
        return
    DONE.wait()
    server.shutdown()


if __name__ == "__main__":
    main()
