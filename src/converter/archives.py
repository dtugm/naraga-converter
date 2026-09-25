"""Strict, streaming ZIP extraction for converter inputs."""

from __future__ import annotations

import posixpath
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath


class UnsafeArchive(ValueError):
    """Raised when an archive could escape or alter the extraction tree."""


def _member_path(info: zipfile.ZipInfo) -> PurePosixPath:
    raw = info.filename.replace("\\", "/")
    normalized = posixpath.normpath(raw)
    path = PurePosixPath(normalized)
    if normalized in {"", ".", ".."} or path.is_absolute() or ".." in path.parts:
        raise UnsafeArchive(f"unsafe path in ZIP: {info.filename!r}")
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        raise UnsafeArchive(f"symlink entry in ZIP: {info.filename!r}")
    return path


def extract_zip(archive: Path, destination: Path) -> list[Path]:
    """Validate the complete archive, then stream regular files to destination."""
    with zipfile.ZipFile(archive) as zf:
        members = [(info, _member_path(info)) for info in zf.infolist()]
        destination.mkdir(parents=True, exist_ok=False)
        extracted: list[Path] = []
        try:
            for info, relative in members:
                target = destination.joinpath(*relative.parts)
                if info.is_dir() or info.filename.endswith(("/", "\\")):
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                extracted.append(target)
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise
    return extracted
