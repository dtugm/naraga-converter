"""/ready readiness checks: each configured tool must be resolvable on disk/PATH."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from converter.main import app


def test_ready_ok_when_all_tools_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _cmd: "/usr/bin/tool")
    monkeypatch.setattr(Path, "is_file", lambda _self: True)

    with TestClient(app) as client:
        resp = client.get("/ready")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"] == {
        "java_bin": True,
        "ogr2ogr_bin": True,
        "tippecanoe_bin": True,
        "pmtiles_bin": True,
        "pdal_bin": True,
        "mago_tiler_jar": True,
    }


def test_ready_503_when_a_binary_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_which(cmd: str) -> str | None:
        return None if cmd == "tippecanoe" else "/usr/bin/tool"

    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setattr(Path, "is_file", lambda _self: True)

    with TestClient(app) as client:
        resp = client.get("/ready")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["tippecanoe_bin"] is False
    assert body["checks"]["ogr2ogr_bin"] is True


def test_ready_503_when_mago_jar_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _cmd: "/usr/bin/tool")
    monkeypatch.setattr(Path, "is_file", lambda _self: False)

    with TestClient(app) as client:
        resp = client.get("/ready")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["mago_tiler_jar"] is False
