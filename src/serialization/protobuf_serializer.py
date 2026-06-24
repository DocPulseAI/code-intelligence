"""Serialize AST extraction results to protobuf bytes."""

from __future__ import annotations

import json
from typing import Any

try:
    from google.protobuf.struct_pb2 import Struct
    PROTOBUF_AVAILABLE = True
except Exception:
    PROTOBUF_AVAILABLE = False


def _summarize_features(features: dict[str, Any]) -> dict[str, str]:
    summary: dict[str, str] = {}
    for key, value in (features or {}).items():
        if isinstance(value, list):
            summary[key] = str(len(value))
        elif isinstance(value, dict):
            summary[key] = str(len(value))
        elif isinstance(value, bool):
            summary[key] = "true" if value else "false"
        else:
            summary[key] = str(value)
    return summary


def serialize_ast_record(
    file_path: str,
    language: str | None,
    syntax_error: bool,
    features: dict[str, Any],
    symbols: list[dict[str, str]],
) -> bytes:
    """
    Serialize AST results as a compact protobuf payload.

    This uses Struct so we do not require generated code at runtime.
    The canonical schema is documented in src/proto/schema.proto.
    """
    payload = {
        "file_path": file_path,
        "language": language or "unknown",
        "syntax_error": bool(syntax_error),
        "feature_summary": _summarize_features(features),
        "symbols": symbols,
    }

    if PROTOBUF_AVAILABLE:
        msg = Struct()
        msg.update(payload)
        return msg.SerializeToString()

    # Fallback for environments without protobuf runtime.
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")
