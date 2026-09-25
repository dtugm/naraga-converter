from __future__ import annotations

import stat
import zipfile
from pathlib import Path

import pytest

from converter.archives import UnsafeArchive, extract_zip


def test_extract_zip_rejects_traversal_without_extracting_anything(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("safe.geojson", "{}")
        zf.writestr("../escaped", "bad")

    destination = tmp_path / "out"
    with pytest.raises(UnsafeArchive, match="unsafe path"):
        extract_zip(archive, destination)

    assert not destination.exists()
    assert not (tmp_path / "escaped").exists()


def test_extract_zip_rejects_symlinks(tmp_path: Path) -> None:
    archive = tmp_path / "link.zip"
    link = zipfile.ZipInfo("link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(link, "target")

    with pytest.raises(UnsafeArchive, match="symlink"):
        extract_zip(archive, tmp_path / "out")


def test_extract_zip_normalizes_windows_paths(tmp_path: Path) -> None:
    archive = tmp_path / "windows.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("layer\\roads.shp", b"shape")

    destination = tmp_path / "out"
    extract_zip(archive, destination)

    assert (destination / "layer" / "roads.shp").read_bytes() == b"shape"
