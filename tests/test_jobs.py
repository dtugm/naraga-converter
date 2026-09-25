"""End-to-end test of the worked example: submit -> callbacks -> terminal state.

The gateway is faked with an httpx.MockTransport swapped into app.state.http, so the
full callback path (auth header, sequencing, timestamps) is exercised for real.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from copy import deepcopy
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from converter.jobs import PREFIX
from converter.main import app

AUTH = {"Authorization": "Bearer test-token"}  # set by tests/conftest.py
RFC3339_MS_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


@pytest.fixture(autouse=True)
def _stub_pipeline_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_execute(
        request: Any, client: Any, report_progress: Any
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        del client
        for pct in (25, 50, 75):
            await asyncio.sleep(0.05)
            await report_progress(pct)
        upload = request.output_upload_urls[0]
        return (
            [
                {
                    "name": "example-output",
                    "dataset_format": upload.output_format,
                    "dataset_role": None,
                    "storage_key": upload.storage_key,
                    "size_bytes": 0,
                    "crs": "EPSG:4326",
                    "bbox": None,
                }
            ],
            {"output_format": upload.output_format, "output_size_bytes": 0},
        )

    monkeypatch.setattr("converter.jobs.execute_conversion", fake_execute)

SAMPLE_REQUEST: dict[str, Any] = {
    "job_id": "00000000-0000-4000-8000-000000000001",
    "service": "converter",
    "model": None,
    "input_datasets": {
        "input": {
            "dataset_id": "99999999-9999-4999-8999-999999999999",
            "name": "in",
            "dataset_format": "las",
            "dataset_role": "point_cloud",
            "size_bytes": 104857600,
            "crs": "EPSG:32749",
            "bbox": [110.36, -7.82, 110.42, -7.78],
            "signed_url": "http://s/i",
            "signed_url_expires_at": "2026-09-02T00:00:00.000Z",
        }
    },
    "params": {"target_format": "3dtiles"},
    "output_prefix": "jobs/00000000-0000-4000-8000-000000000001/outputs/",
    "output_upload_urls": [
        {
            "output_format": "3dtiles",
            "storage_key": "jobs/00000000-0000-4000-8000-000000000001/outputs/output.3dtiles",
            "url": "http://storage.local/put/out",
            "expires_at": "2026-09-02T00:00:00.000Z",
        }
    ],
    "callback_url": "http://gateway.invalid/v1/internal/jobs/00000000-0000-4000-8000-000000000001/callback",
    "max_job_duration_seconds": 3600,
    "heartbeat_interval_seconds": 30,
}


def _sample(job_id: str) -> dict[str, Any]:
    payload = deepcopy(SAMPLE_REQUEST)
    payload["job_id"] = job_id
    return payload


def _install_gateway_sink(events: list[dict[str, Any]]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-token"
        events.append(json.loads(request.content))
        return httpx.Response(200, json={"received": True})

    app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _wait_for_status(
    client: TestClient, job_id: str, wanted: str, timeout: float = 5.0
) -> dict[str, Any]:
    deadline = time.time() + timeout
    body: dict[str, Any] = {}
    while time.time() < deadline:
        body = client.get(f"{PREFIX}/jobs/{job_id}/status", headers=AUTH).json()
        if body["status"] == wanted:
            return body
        time.sleep(0.02)
    return body


def test_job_flow_end_to_end() -> None:
    events: list[dict[str, Any]] = []
    with TestClient(app) as client:
        _install_gateway_sink(events)
        job_id = str(SAMPLE_REQUEST["job_id"])

        r = client.post(f"{PREFIX}/jobs", json=SAMPLE_REQUEST, headers=AUTH)
        assert r.status_code == 202
        accepted = r.json()
        assert accepted["accepted"] is True
        assert accepted["job_id"] == job_id
        assert accepted["credits_estimated"] >= 1

        status = _wait_for_status(client, job_id, "complete")
        assert status == {"job_id": job_id, "status": "complete", "progress_percent": 100}

    # Callback stream: starts processing, ends complete, sequence strictly monotonic,
    # timestamps in the exact RFC3339-ms-Z shape the gateway accepts.
    assert [e["status"] for e in events][0] == "processing"
    assert events[-1]["status"] == "complete"
    sequences = [e["sequence"] for e in events]
    assert sequences == sorted(set(sequences))
    assert all(RFC3339_MS_Z.match(e["emitted_at"]) for e in events)

    terminal = events[-1]
    assert terminal["progress_percent"] == 100
    assert isinstance(terminal["credits_used"], int)
    assert len(terminal["output_datasets"]) == 1
    draft = terminal["output_datasets"][0]
    assert draft["storage_key"].startswith(SAMPLE_REQUEST["output_prefix"])
    assert set(draft) == {
        "name",
        "dataset_format",
        "dataset_role",
        "storage_key",
        "size_bytes",
        "crs",
        "bbox",
    }


def test_submit_is_idempotent_on_job_id() -> None:
    events: list[dict[str, Any]] = []
    with TestClient(app) as client:
        _install_gateway_sink(events)
        payload = _sample("00000000-0000-4000-8000-000000000002")
        assert client.post(f"{PREFIX}/jobs", json=payload, headers=AUTH).status_code == 202
        r = client.post(f"{PREFIX}/jobs", json=payload, headers=AUTH)
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "CONFLICT"


def test_cancel_stops_callbacks() -> None:
    events: list[dict[str, Any]] = []
    with TestClient(app) as client:
        _install_gateway_sink(events)
        payload = _sample("00000000-0000-4000-8000-000000000003")
        assert client.post(f"{PREFIX}/jobs", json=payload, headers=AUTH).status_code == 202
        r = client.delete(f"{PREFIX}/jobs/{payload['job_id']}", headers=AUTH)
        assert r.status_code == 202
        time.sleep(0.3)  # long enough for the job to have finished were it not cancelled
    assert all(e["status"] != "complete" for e in events)


def test_rejects_missing_or_wrong_token() -> None:
    with TestClient(app) as client:
        r = client.post(f"{PREFIX}/jobs", json=SAMPLE_REQUEST)
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "UNAUTHENTICATED"
        r = client.get(f"{PREFIX}/capabilities", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401


def test_validation_error_is_contract_shaped() -> None:
    with TestClient(app) as client:
        r = client.post(f"{PREFIX}/jobs", json={"job_id": "not-even-a-uuid"}, headers=AUTH)
        assert r.status_code == 422
        body = r.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert RFC3339_MS_Z.match(body["meta"]["timestamp"])


def test_estimate() -> None:
    with TestClient(app) as client:
        r = client.post(
            f"{PREFIX}/estimate",
            json={
                "input_datasets": SAMPLE_REQUEST["input_datasets"],
                "params": SAMPLE_REQUEST["params"],
            },
            headers=AUTH,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["credits_estimated"] >= 1
        assert body["breakdown"]["total"] == body["credits_estimated"]


def test_capabilities() -> None:
    with TestClient(app) as client:
        r = client.get(f"{PREFIX}/capabilities", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["service"] == "converter"
        assert body["output_formats"] == [
            "3dtiles",
            "pmtiles",
            "geojson",
            "cog",
            "las",
            "laz",
            "gltf",
        ]
        assert body["max_input_size_bytes"] > 0
        assert body["conversion_matrix"] == {
            "geojson": ["pmtiles"],
            "shp": ["pmtiles"],
            "las": ["3dtiles", "cog"],
            "laz": ["3dtiles", "cog"],
        }


def test_submit_rejects_unsupported_conversion_before_accepting() -> None:
    payload = _sample("00000000-0000-4000-8000-000000000006")
    payload["params"] = {"target_format": "pmtiles"}
    payload["output_upload_urls"] = [
        {
            **payload["output_upload_urls"][0],
            "output_format": "pmtiles",
            "storage_key": f"{payload['output_prefix']}out.pmtiles",
        }
    ]
    with TestClient(app) as client:
        r = client.post(f"{PREFIX}/jobs", json=payload, headers=AUTH)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "UNSUPPORTED_CONVERSION"


def test_submit_rejects_output_that_does_not_match_target() -> None:
    payload = _sample("00000000-0000-4000-8000-000000000007")
    payload["output_upload_urls"][0]["output_format"] = "pmtiles"
    with TestClient(app) as client:
        r = client.post(f"{PREFIX}/jobs", json=payload, headers=AUTH)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_restart_recovers_orphans_and_keeps_idempotency() -> None:
    """Two TestClient blocks against one state db = a service restart. The first
    'process' dies mid-job; the second must 409 the redelivered submit, report the
    orphan as failed, and drain its terminal callback from the outbox."""
    events: list[dict[str, Any]] = []
    payload = _sample("00000000-0000-4000-8000-000000000004")
    with TestClient(app) as client:
        _install_gateway_sink(events)
        assert client.post(f"{PREFIX}/jobs", json=payload, headers=AUTH).status_code == 202
        # exit immediately: the process dies while the job is still 'processing'
    events.clear()
    with TestClient(app) as client:
        _install_gateway_sink(events)
        assert client.post(f"{PREFIX}/jobs", json=payload, headers=AUTH).status_code == 409
        status = client.get(f"{PREFIX}/jobs/{payload['job_id']}/status", headers=AUTH).json()
        assert status["status"] == "failed"
        deadline = time.time() + 5
        while time.time() < deadline:
            if any(e["status"] == "failed" and e["job_id"] == payload["job_id"] for e in events):
                break
            time.sleep(0.02)
        else:
            raise AssertionError(f"orphan failure callback never delivered: {events}")


def test_terminal_callback_survives_gateway_outage() -> None:
    """The gateway 503s the first two terminal deliveries; the outbox drainer must
    keep redelivering (same sequence) until the ack lands — exactly once."""
    events: list[dict[str, Any]] = []
    outage = {"remaining": 2}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["status"] == "complete" and outage["remaining"] > 0:
            outage["remaining"] -= 1
            return httpx.Response(503)
        events.append(body)
        return httpx.Response(200, json={"received": True})

    with TestClient(app) as client:
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        payload = _sample("00000000-0000-4000-8000-000000000005")
        assert client.post(f"{PREFIX}/jobs", json=payload, headers=AUTH).status_code == 202
        deadline = time.time() + 5
        while time.time() < deadline and not any(e["status"] == "complete" for e in events):
            time.sleep(0.02)
    complete = [e for e in events if e["status"] == "complete"]
    assert len(complete) == 1
    assert outage["remaining"] == 0


def test_unusable_input_fails_as_validation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """User-fault input must fail with VALIDATION_ERROR: the gateway's no-refund code."""
    from converter.worker_api import ConversionInputError

    async def bad_input(request: Any, client: Any, report_progress: Any) -> Any:
        raise ConversionInputError("unable to read LAS/LAZ input")

    monkeypatch.setattr("converter.jobs.execute_conversion", bad_input)
    events: list[dict[str, Any]] = []
    with TestClient(app) as client:
        _install_gateway_sink(events)
        job_id = str(SAMPLE_REQUEST["job_id"])
        assert client.post(f"{PREFIX}/jobs", json=SAMPLE_REQUEST, headers=AUTH).status_code == 202
        assert _wait_for_status(client, job_id, "failed")["status"] == "failed"

    terminal = events[-1]
    assert terminal["status"] == "failed"
    assert terminal["error_code"] == "VALIDATION_ERROR"
    assert terminal["error_message"] == "unable to read LAS/LAZ input"
    assert terminal["credits_used"] == 0
