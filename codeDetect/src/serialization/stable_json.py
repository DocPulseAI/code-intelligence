"""Stable JSON serialization helpers."""

from __future__ import annotations

import json
from typing import Any


def dumps_stable(data: Any, *, pretty: bool = False) -> str:
    kwargs = {
        "ensure_ascii": True,
        "sort_keys": True,
    }
    if pretty:
        kwargs["indent"] = 2
    else:
        kwargs["separators"] = (",", ":")
    return json.dumps(data, **kwargs)


def dump_stable_file(path: str, data: Any, *, pretty: bool = True) -> None:
    payload = dumps_stable(data, pretty=pretty)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.write("\n")
