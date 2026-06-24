"""Deterministic schema/model extraction and diff engine."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable

from src.file_filter import FileFilter
from src.intelligence.data_model_graph import extract_mongoose_models


_SEVERITY_ORDER = {"PATCH": 1, "MINOR": 2, "MAJOR": 3}
_TYPE_MAP = {
    "str": "string",
    "string": "string",
    "varchar": "string",
    "text": "string",
    "char": "string",
    "int": "int",
    "integer": "int",
    "bigint": "int",
    "smallint": "int",
    "number": "number",
    "float": "number",
    "double": "number",
    "decimal": "number",
    "bool": "boolean",
    "boolean": "boolean",
    "date": "date",
    "datetime": "datetime",
    "timestamp": "datetime",
    "json": "json",
    "object": "object",
    "array": "array",
}


def _canonical(data: Any) -> str:
    return json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _stable_id(payload: dict) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:16]


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _norm_type(raw: Any) -> str:
    text = str(raw or "unknown").strip().lower()
    if "|" in text:
        parts = [p.strip() for p in text.split("|") if p.strip() and p.strip() not in {"none", "null"}]
        if len(parts) == 1:
            text = parts[0]
    if text.startswith("optional[") and text.endswith("]"):
        text = text[len("optional[") : -1].strip()
    if text.startswith("array<") and text.endswith(">"):
        return text
    if "objectid" in text:
        return "objectid"
    if text.endswith("[]") or text.startswith("list["):
        return "array"
    return _TYPE_MAP.get(text, text or "unknown")


def _normalize_field(field: Any) -> dict:
    if isinstance(field, str):
        return {
            "name": field,
            "type": "unknown",
            "required": False,
            "default": None,
            "indexed": False,
            "enum_values": [],
            "primary_key": False,
            "ref": "",
        }
    if not isinstance(field, dict):
        return {
            "name": "",
            "type": "unknown",
            "required": False,
            "default": None,
            "indexed": False,
            "enum_values": [],
            "primary_key": False,
            "ref": "",
        }
    enum_values = field.get("enum_values")
    if not isinstance(enum_values, list):
        enum_values = []
    ref = str(field.get("ref", "") or "").strip()
    return {
        "name": str(field.get("name", "")),
        "type": _norm_type(field.get("type", "unknown")),
        "required": _to_bool(field.get("required", False)),
        "default": field.get("default"),
        "indexed": _to_bool(field.get("indexed", False)),
        "enum_values": sorted(str(v) for v in enum_values if str(v).strip()),
        "primary_key": _to_bool(field.get("primary_key", False)),
        "ref": ref,
    }


def _normalize_entity(entity: Any) -> dict:
    if isinstance(entity, str):
        return {"name": entity, "fields": {}, "model_hash": ""}
    if not isinstance(entity, dict):
        return {"name": "", "fields": {}, "model_hash": ""}
    fields_raw = entity.get("fields")
    fields_map: dict[str, dict] = {}
    if isinstance(fields_raw, list):
        for row in fields_raw:
            f = _normalize_field(row)
            if f["name"]:
                fields_map[f["name"]] = f
    elif isinstance(fields_raw, dict):
        for key, value in fields_raw.items():
            entry = {"name": str(key)}
            if isinstance(value, dict):
                entry.update(value)
            f = _normalize_field(entry)
            if f["name"]:
                fields_map[f["name"]] = f
    name = str(entity.get("name", ""))
    model_hash = hashlib.sha256(
        f"v1|{name}|{_canonical({'fields': fields_map})}".encode("utf-8")
    ).hexdigest()
    return {
        "name": name,
        "fields": fields_map,
        "model_hash": model_hash,
    }


def _extract_entities_from_report(report: dict | None) -> dict[str, dict]:
    payload = report or {}
    out: dict[str, dict] = {}

    # Preferred detailed schema catalog.
    schema_analysis = payload.get("schema_analysis", {}) if isinstance(payload.get("schema_analysis"), dict) else {}
    models = schema_analysis.get("models", []) if isinstance(schema_analysis.get("models", []), list) else []
    for row in models:
        entity = _normalize_entity(row)
        if entity["name"]:
            out[entity["name"]] = entity
    if out:
        return out

    # Legacy fallback from data_model entities.
    data_model = payload.get("data_model", {}) if isinstance(payload.get("data_model"), dict) else {}
    for row in data_model.get("entities", []) if isinstance(data_model.get("entities", []), list) else []:
        entity = _normalize_entity(row)
        if entity["name"]:
            out[entity["name"]] = entity
    return out


def _descriptor(entity: str, field: str, change: str, description: str, severity: str) -> dict:
    row = {
        "type": "SCHEMA_BREAKING_CHANGE",
        "entity": entity,
        "file": "",
        "field": field,
        "change": change,
        "description": description,
        "severity": severity,
        "classification_basis": "SCHEMA_DIFF",
    }
    row["id"] = _stable_id(row)
    return row


def _extract_ts_models(content: str) -> list[dict]:
    models: list[dict] = []
    # interface / class DTO field forms.
    for m in re.finditer(r"\b(?:export\s+)?(?:interface|class)\s+([A-Za-z_]\w*)\s*\{([\s\S]*?)\n\}", content):
        name, body = m.group(1), m.group(2)
        fields = {}
        for line in body.splitlines():
            row = line.strip().rstrip(";")
            fm = re.match(r"(?:public\s+|private\s+|readonly\s+)?([A-Za-z_]\w*)\??\s*:\s*([A-Za-z0-9_<>\[\]\|\.\?]+)", row)
            if not fm:
                continue
            field_name = fm.group(1)
            raw_type = fm.group(2)
            required = "?" not in row
            fields[field_name] = _normalize_field({"name": field_name, "type": raw_type, "required": required})
        if fields:
            models.append({"name": name, "fields": fields})

    # Zod schemas: const UserSchema = z.object({ ... })
    for m in re.finditer(r"\bconst\s+([A-Za-z_]\w*)\s*=\s*z\.object\s*\(\s*\{([\s\S]*?)\}\s*\)", content):
        name, body = m.group(1), m.group(2)
        fields = {}
        for line in body.splitlines():
            row = line.strip().rstrip(",")
            fm = re.match(r"([A-Za-z_]\w*)\s*:\s*z\.([A-Za-z_]\w*)(.*)", row)
            if not fm:
                continue
            field_name = fm.group(1)
            raw_type = fm.group(2)
            tail = fm.group(3)
            required = ".optional()" not in tail
            fields[field_name] = _normalize_field({"name": field_name, "type": raw_type, "required": required})
        if fields:
            models.append({"name": name, "fields": fields})

    # Joi object schemas.
    for m in re.finditer(r"\bconst\s+([A-Za-z_]\w*)\s*=\s*Joi\.object\s*\(\s*\{([\s\S]*?)\}\s*\)", content):
        name, body = m.group(1), m.group(2)
        fields = {}
        for line in body.splitlines():
            row = line.strip().rstrip(",")
            fm = re.match(r"([A-Za-z_]\w*)\s*:\s*Joi\.([A-Za-z_]\w*)(.*)", row)
            if not fm:
                continue
            field_name = fm.group(1)
            raw_type = fm.group(2)
            tail = fm.group(3)
            required = ".required()" in tail
            fields[field_name] = _normalize_field({"name": field_name, "type": raw_type, "required": required})
        if fields:
            models.append({"name": name, "fields": fields})

    return models


def _extract_java_models(content: str) -> list[dict]:
    models: list[dict] = []
    entity_match = re.search(r"@(Entity|Embeddable)\b[\s\S]*?class\s+([A-Za-z_]\w+)\s*\{([\s\S]*?)\n\}", content)
    if not entity_match:
        return models
    name, body = entity_match.group(2), entity_match.group(3)
    fields: dict[str, dict] = {}
    pending_required = False
    pending_enum = False
    pending_primary = False
    for line in body.splitlines():
        row = line.strip()
        if "@Column" in row and "nullable=false" in row.replace(" ", "").lower():
            pending_required = True
            continue
        if "@Enumerated" in row:
            pending_enum = True
            continue
        if "@Id" in row:
            pending_primary = True
            continue
        fm = re.search(r"(?:private|protected|public)\s+([A-Za-z_][A-Za-z0-9_<>]*)\s+([A-Za-z_]\w*)\s*;", row)
        if not fm:
            continue
        raw_type, field_name = fm.group(1), fm.group(2)
        field = _normalize_field(
            {
                "name": field_name,
                "type": "enum" if pending_enum else raw_type,
                "required": pending_required,
                "primary_key": pending_primary,
            }
        )
        fields[field_name] = field
        pending_required = False
        pending_enum = False
        pending_primary = False
    if fields:
        models.append({"name": name, "fields": fields})
    return models


def _extract_python_models(content: str) -> list[dict]:
    models: list[dict] = []
    class_rx = re.compile(r"^\s*class\s+([A-Za-z_]\w*)\s*\(([^)]*)\)\s*:\s*$", re.MULTILINE)
    lines = content.splitlines()
    for m in class_rx.finditer(content):
        name, base = m.group(1), m.group(2)
        if "BaseModel" not in base and "dataclass" not in content[: m.start()].splitlines()[-1:][0]:
            # also allow dataclass-decorated class by looking one line above
            idx = content[: m.start()].count("\n")
            prev = lines[idx - 1].strip() if idx - 1 >= 0 else ""
            if "@dataclass" not in prev:
                continue
        idx = content[: m.end()].count("\n")
        fields = {}
        for row in lines[idx : idx + 60]:
            if not row.startswith(" ") and row.strip():
                break
            line = row.strip()
            fm = re.match(r"([A-Za-z_]\w*)\s*:\s*([^=]+?)(?:\s*=\s*(.*))?$", line)
            if not fm:
                continue
            fname, raw_type, default_val = fm.group(1), fm.group(2).strip(), fm.group(3)
            required = default_val is None and "Optional[" not in raw_type and "| None" not in raw_type
            fields[fname] = _normalize_field(
                {
                    "name": fname,
                    "type": raw_type,
                    "required": required,
                    "default": None if default_val is None else default_val.strip(),
                }
            )
        if fields:
            models.append({"name": name, "fields": fields})
    return models


def extract_canonical_models(file_paths: list[str], read_file: Callable[[str], str | None]) -> list[dict]:
    models: dict[str, dict] = {}
    for path in sorted(file_paths):
        if FileFilter.should_exclude_from_entity_analysis(path):
            continue
        content = read_file(path) or ""
        if not content:
            continue
        lower = path.lower()
        extracted: list[dict] = []
        if lower.endswith((".ts", ".tsx")):
            extracted.extend(_extract_ts_models(content))
        if lower.endswith((".java", ".kt")):
            extracted.extend(_extract_java_models(content))
        if lower.endswith(".py"):
            extracted.extend(_extract_python_models(content))
        if lower.endswith("schema.prisma"):
            for mm in re.finditer(r"^\s*model\s+([A-Za-z_]\w+)\s*\{([\s\S]*?)^\s*\}", content, re.MULTILINE):
                name, body = mm.group(1), mm.group(2)
                fields = {}
                for line in body.splitlines():
                    row = line.strip()
                    if not row or row.startswith("//"):
                        continue
                    parts = row.split()
                    if len(parts) < 2:
                        continue
                    fname, ftype = parts[0], parts[1]
                    fields[fname] = _normalize_field(
                        {
                            "name": fname,
                            "type": ftype.replace("?", ""),
                            "required": not ftype.endswith("?"),
                            "primary_key": "@id" in row,
                            "indexed": "@unique" in row or "@index" in row,
                        }
                    )
                if fields:
                    extracted.append({"name": name, "fields": fields})
        if lower.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
            for model in extract_mongoose_models(content):
                extracted.append(
                    {
                        "name": str(model.get("name", "")),
                        "fields": model.get("fields", {}) if isinstance(model.get("fields"), dict) else {},
                    }
                )
        for item in extracted:
            entity = _normalize_entity(item)
            if entity["name"]:
                models[entity["name"]] = entity
    rows = [
        {
            "model_name": name,
            "fields": entity["fields"],
            "model_hash": entity["model_hash"],
        }
        for name, entity in sorted(models.items(), key=lambda kv: kv[0])
    ]
    return rows


def build_schema_analysis(file_paths: list[str], read_file: Callable[[str], str | None]) -> dict:
    models = extract_canonical_models(file_paths, read_file)
    return {
        "models_detected": len(models),
        "breaking_changes": 0,
        "model_diffs": [],
        "models": models,
    }


def diff_schema_models(baseline_report: dict | None, current_report: dict) -> list[dict]:
    base_entities = _extract_entities_from_report(baseline_report)
    curr_entities = _extract_entities_from_report(current_report)
    findings: list[dict] = []

    for name in sorted(set(base_entities.keys()) - set(curr_entities.keys())):
        findings.append(_descriptor(name, "", "ENTITY_REMOVED", f"Entity removed: {name}", "MAJOR"))

    for name in sorted(set(base_entities.keys()) & set(curr_entities.keys())):
        old_fields = base_entities[name]["fields"]
        new_fields = curr_entities[name]["fields"]

        for field in sorted(set(old_fields.keys()) - set(new_fields.keys())):
            findings.append(_descriptor(name, field, "FIELD_REMOVED", f"Field removed: {name}.{field}", "MAJOR"))

        for field in sorted(set(new_fields.keys()) - set(old_fields.keys())):
            candidate = new_fields[field]
            if candidate.get("required") and candidate.get("default") in {None, ""}:
                findings.append(
                    _descriptor(
                        name,
                        field,
                        "REQUIRED_FIELD_ADDED_NO_DEFAULT",
                        f"Required field added without default: {name}.{field}",
                        "MAJOR",
                    )
                )
            else:
                findings.append(
                    _descriptor(name, field, "OPTIONAL_FIELD_ADDED", f"Optional field added: {name}.{field}", "MINOR")
                )

        for field in sorted(set(old_fields.keys()) & set(new_fields.keys())):
            oldf = old_fields[field]
            newf = new_fields[field]
            if str(oldf.get("type")) != str(newf.get("type")):
                findings.append(
                    _descriptor(
                        name,
                        field,
                        "TYPE_CHANGED",
                        f"Type changed: {name}.{field} ({oldf.get('type')} -> {newf.get('type')})",
                        "MAJOR",
                    )
                )
            old_enum = set(oldf.get("enum_values", []))
            new_enum = set(newf.get("enum_values", []))
            removed = sorted(old_enum - new_enum)
            if removed:
                findings.append(
                    _descriptor(
                        name,
                        field,
                        "ENUM_VALUE_REMOVED",
                        f"Enum value removed: {name}.{field} ({', '.join(removed)})",
                        "MAJOR",
                    )
                )
            if bool(oldf.get("primary_key")) != bool(newf.get("primary_key")):
                findings.append(
                    _descriptor(
                        name,
                        field,
                        "PRIMARY_KEY_CHANGED",
                        f"Primary key changed: {name}.{field}",
                        "MAJOR",
                    )
                )
            if oldf.get("default") != newf.get("default"):
                findings.append(
                    _descriptor(
                        name,
                        field,
                        "DEFAULT_VALUE_CHANGED",
                        f"Default changed: {name}.{field}",
                        "MINOR",
                    )
                )
            if bool(oldf.get("indexed")) != bool(newf.get("indexed")):
                findings.append(
                    _descriptor(
                        name,
                        field,
                        "INDEX_CHANGED",
                        f"Index changed: {name}.{field}",
                        "MINOR",
                    )
                )

    dedup: dict[str, dict] = {}
    for item in findings:
        dedup[item["id"]] = item
    return sorted(
        dedup.values(),
        key=lambda d: (
            -_SEVERITY_ORDER.get(str(d.get("severity", "PATCH")), 1),
            str(d.get("entity", "")),
            str(d.get("field", "")),
            str(d.get("change", "")),
            str(d.get("id", "")),
        ),
    )
