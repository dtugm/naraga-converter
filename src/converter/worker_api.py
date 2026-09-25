"""Names shared by the parent, the worker process and the pipelines.

Kept out of pipeline_worker.py on purpose: that module runs as ``__main__`` in the
worker process, so an exception class defined there would be a *different* class from
the one pipelines import, and ``except ConversionInputError`` would never match.
"""

from __future__ import annotations

import json

INPUT_ERROR_EXIT = 3


class ConversionInputError(ValueError):
    """The user's input cannot be converted (bad/empty file, missing CRS or classes)."""


def report_progress(pct: int) -> None:
    """Progress line the parent parses from stdout; must never contain paths/URLs."""
    print(json.dumps({"progress": int(pct)}), flush=True)
