"""Deterministic data model extraction for Mongoose, Prisma, and JPA."""

from __future__ import annotations

import re
from typing import Any, Callable

from src.file_filter import FileFilter


MONGOOSE_MODEL_DECL_RX = re.compile(r"(?:mongoose\.)?model\s*\(", re.MULTILINE)
SCHEMA_DECL_RX = re.compile(
    r"(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:new\s+)?(?:mongoose\.)?Schema\s*\(",
    re.MULTILINE,
)
JPA_ENTITY_RX = re.compile(r"@Entity\b")
JPA_CLASS_RX = re.compile(r"class\s+(\w+)")
PRISMA_MODEL_RX = re.compile(r"^\s*model\s+(\w+)\s*\{", re.MULTILINE)


def _find_matching(text: str, open_idx: int, open_ch: str, close_ch: str) -> int:
    if open_idx < 0 or open_idx >= len(text) or text[open_idx] != open_ch:
        return -1
    depth = 1
    i = open_idx + 1
    quote = ""
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in {"'", '"', "`"}:
            quote = ch
            i += 1
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1

def _canonicalize_model_name(name: str) -> str:
    if not name:
        return name
    return name[0].upper() + name[1:]


def _split_top_level(raw: str, sep: str = ",") -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    par = arr = obj = 0
    quote = ""
    i = 0
    while i < len(raw):
        ch = raw[i]
        if quote:
            buf.append(ch)
            if ch == "\\":
                if i + 1 < len(raw):
                    buf.append(raw[i + 1])
                    i += 2
                    continue
            elif ch == quote:
                quote = ""
            i += 1
            continue
        if ch in {"'", '"', "`"}:
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "(":
            par += 1
        elif ch == ")":
            par = max(0, par - 1)
        elif ch == "[":
            arr += 1
        elif ch == "]":
            arr = max(0, arr - 1)
        elif ch == "{":
            obj += 1
        elif ch == "}":
            obj = max(0, obj - 1)
        if ch == sep and par == arr == obj == 0:
            token = "".join(buf).strip()
            if token:
                out.append(token)
            buf = []
        else:
            buf.append(ch)
        i += 1
    token = "".join(buf).strip()
    if token:
        out.append(token)
    return out


def _split_key_value(pair: str) -> tuple[str, str] | None:
    quote = ""
    par = arr = obj = 0
    for i, ch in enumerate(pair):
        if quote:
            if ch == "\\":
                continue
            if ch == quote:
                quote = ""
            continue
        if ch in {"'", '"', "`"}:
            quote = ch
            continue
        if ch == "(":
            par += 1
        elif ch == ")":
            par = max(0, par - 1)
        elif ch == "[":
            arr += 1
        elif ch == "]":
            arr = max(0, arr - 1)
        elif ch == "{":
            obj += 1
        elif ch == "}":
            obj = max(0, obj - 1)
        elif ch == ":" and par == arr == obj == 0:
            return pair[:i].strip(), pair[i + 1 :].strip()
    return None


def _strip_quotes(text: str) -> str:
    token = text.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"', "`"}:
        return token[1:-1]
    return token


def _is_string_literal(text: str) -> bool:
    token = text.strip()
    return len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"', "`"}


def _normalize_type_token(token: str) -> str:
    raw = token.strip()
    if not raw:
        return "Mixed"
    aliases = {
        "string": "String",
        "number": "Number",
        "boolean": "Boolean",
        "date": "Date",
        "buffer": "Buffer",
        "mixed": "Mixed",
        "map": "Map",
        "object": "Object",
        "schema.types.objectid": "ObjectId",
        "mongoose.schema.types.objectid": "ObjectId",
        "types.objectid": "ObjectId",
        "mongoose.types.objectid": "ObjectId",
        "objectid": "ObjectId",
    }
    lowered = raw.lower()
    if lowered in aliases:
        return aliases[lowered]
    if lowered.endswith("schema.types.objectid") or "objectid" in lowered:
        return "ObjectId"
    if raw.startswith("[") and raw.endswith("]"):
        return "Array"
    if raw.startswith("{") and raw.endswith("}"):
        return "Object"
    if re.fullmatch(r"[A-Za-z_]\w*", raw):
        return raw
    return "Mixed"


def _parse_required(expr: str) -> bool:
    value = expr.strip().lower()
    if value == "true":
        return True
    if value.startswith("["):
        inner = value[1:-1].strip()
        first = _split_top_level(inner)[0].strip().lower() if inner else ""
        return first == "true"
    return False


def _parse_default(expr: str) -> Any:
    token = expr.strip()
    if not token:
        return None
    if _is_string_literal(token):
        return _strip_quotes(token)
    if token.lower() == "true":
        return True
    if token.lower() == "false":
        return False
    if token.lower() == "null":
        return None
    if re.fullmatch(r"-?\d+", token):
        try:
            return int(token)
        except Exception:
            return token
    if re.fullmatch(r"-?\d+\.\d+", token):
        try:
            return float(token)
        except Exception:
            return token
    return token


def _parse_enum(expr: str) -> list[str]:
    text = expr.strip()
    if text.startswith("[") and text.endswith("]"):
        body = text[1:-1]
        out: list[str] = []
        for item in _split_top_level(body):
            token = item.strip()
            if _is_string_literal(token):
                out.append(_strip_quotes(token))
            elif token:
                out.append(token)
        return sorted(set(out))
    return []


def _parse_type_expr(expr: str) -> tuple[str, str | None]:
    """
    Returns (field_type, ref_model).
    """
    value = expr.strip()
    if not value:
        return "Mixed", None
    if value.startswith("[") and value.endswith("]"):
        inner_items = _split_top_level(value[1:-1])
        if not inner_items:
            return "Array<Mixed>", None
        inner_type, inner_ref = _parse_type_expr(inner_items[0])
        return f"Array<{inner_type}>", inner_ref
    if value.startswith("{") and value.endswith("}"):
        parsed = _parse_object_field(value[1:-1])
        return parsed["type"], parsed.get("ref")
    return _normalize_type_token(value), None


def _parse_object_field(body: str) -> dict[str, Any]:
    props: dict[str, str] = {}
    for token in _split_top_level(body):
        kv = _split_key_value(token)
        if not kv:
            continue
        key = _strip_quotes(kv[0])
        value = kv[1].strip()
        if key:
            props[key] = value

    has_schema_props = any(k in props for k in ("type", "required", "default", "ref", "enum"))
    if not has_schema_props:
        nested = _parse_schema_fields(body)
        return {
            "type": "Object",
            "required": False,
            "default": None,
            "enum_values": [],
            "nested_fields": nested,
        }

    t_expr = props.get("type", "")
    field_type, ref_model = _parse_type_expr(t_expr) if t_expr else ("Mixed", None)
    field = {
        "type": field_type,
        "required": _parse_required(props.get("required", "false")),
        "default": _parse_default(props.get("default", "")) if "default" in props else None,
        "enum_values": _parse_enum(props.get("enum", "")) if "enum" in props else [],
    }
    if "ref" in props:
        ref_value = props.get("ref", "")
        field["ref"] = _canonicalize_model_name(_strip_quotes(ref_value))
    elif ref_model:
        field["ref"] = _canonicalize_model_name(ref_model)
    return field


def _parse_schema_fields(object_body: str) -> dict[str, dict[str, Any]]:
    fields: dict[str, dict[str, Any]] = {}
    for token in _split_top_level(object_body):
        kv = _split_key_value(token)
        if not kv:
            continue
        raw_key, raw_val = kv
        field_name = _strip_quotes(raw_key)
        if not field_name:
            continue
        value = raw_val.strip()

        if value.startswith("{") and value.endswith("}"):
            meta = _parse_object_field(value[1:-1])
        elif value.startswith("[") and value.endswith("]"):
            inner_items = _split_top_level(value[1:-1])
            if inner_items:
                inner = inner_items[0].strip()
                if inner.startswith("{") and inner.endswith("}"):
                    inner_meta = _parse_object_field(inner[1:-1])
                    meta = {
                        "type": f"Array<{inner_meta.get('type', 'Mixed')}>",
                        "required": False,
                        "default": None,
                        "enum_values": inner_meta.get("enum_values", []),
                    }
                    if inner_meta.get("ref"):
                        meta["ref"] = inner_meta["ref"]
                else:
                    inner_type, inner_ref = _parse_type_expr(inner)
                    meta = {
                        "type": f"Array<{inner_type}>",
                        "required": False,
                        "default": None,
                        "enum_values": [],
                    }
                    if inner_ref:
                        meta["ref"] = inner_ref
            else:
                meta = {
                    "type": "Array<Mixed>",
                    "required": False,
                    "default": None,
                    "enum_values": [],
                }
        else:
            field_type, ref_model = _parse_type_expr(value)
            meta = {
                "type": field_type,
                "required": False,
                "default": None,
                "enum_values": [],
            }
            if ref_model:
                meta["ref"] = ref_model
        fields[field_name] = meta
    return fields


def _extract_first_object_arg(content: str, open_paren_idx: int) -> str:
    close_paren_idx = _find_matching(content, open_paren_idx, "(", ")")
    if close_paren_idx < 0:
        return ""
    args_body = content[open_paren_idx + 1 : close_paren_idx]
    args = _split_top_level(args_body)
    if not args:
        return ""
    first = args[0].strip()
    if first.startswith("{") and first.endswith("}"):
        return first[1:-1]
    return ""


def _extract_inline_schema_fields(expr: str) -> dict[str, dict[str, Any]]:
    m = re.search(r"(?:new\s+)?(?:mongoose\.)?Schema\s*\(", expr)
    if not m:
        return {}
    open_idx = expr.find("(", m.end() - 1)
    if open_idx < 0:
        return {}
    body = _extract_first_object_arg(expr, open_idx)
    return _parse_schema_fields(body) if body else {}


def _infer_model_name_from_schema_var(schema_var: str) -> str:
    base = re.sub(r"schema$", "", schema_var, flags=re.IGNORECASE).strip("_")
    if not base:
        return schema_var
    return base[:1].upper() + base[1:]


def _has_schema_reference(field_meta: dict, schema_var: str) -> bool:
    """Recursively check if a schema variable is referenced in field metadata."""
    if schema_var in str(field_meta.get("type", "")):
        return True
    nested = field_meta.get("nested_fields", {})
    for sub_meta in nested.values():
        if _has_schema_reference(sub_meta, schema_var):
            return True
    return False


def extract_mongoose_models(content: str, file_path: str | None = None) -> list[dict[str, Any]]:
    """
    Extract mongoose models with full schema fields.
    """
    try:
        import os
        from src.parsers.tree_sitter_engine import parse_code
        ext = ".js"
        if file_path:
            ext = os.path.splitext(file_path)[1].lower() or ".js"
        parsed = parse_code(content, ext)
        if parsed.get("ast_available") and parsed.get("features"):
            features = parsed["features"]
            ast_schemas = features.get("mongoose_schemas", {})
            ast_models = features.get("mongoose_models", [])
            
            if ast_schemas or ast_models:
                models: list[dict[str, Any]] = []
                seen_model_names: set[str] = set()
                
                # Process explicit model registrations
                for m in ast_models:
                    model_name = m["name"]
                    schema_var = m["schema_var"]
                    fields = ast_schemas.get(schema_var) or {}
                    relationships = sorted(
                        [
                            {"field": f_name, "ref": _canonicalize_model_name(f_meta["ref"])}
                            for f_name, f_meta in fields.items()
                            if isinstance(f_meta, dict) and f_meta.get("ref")
                        ],
                        key=lambda x: (x["field"], x["ref"]),
                    )
                    models.append({"name": model_name, "fields": fields, "relationships": relationships})
                    seen_model_names.add(model_name)
                
                # Identify embedded subdocument schemas in the same file
                referenced_schemas = set()
                for other_var, fields in ast_schemas.items():
                    for f_name, f_meta in fields.items():
                        for s_var in ast_schemas.keys():
                            if s_var != other_var and _has_schema_reference(f_meta, s_var):
                                referenced_schemas.add(s_var)
                                
                # Process fallback schemas
                for schema_var, fields in sorted(ast_schemas.items(), key=lambda kv: kv[0]):
                    if schema_var in referenced_schemas:
                        continue
                    if schema_var.startswith("__inline_schema_"):
                        continue
                    fallback_name = _canonicalize_model_name(_infer_model_name_from_schema_var(schema_var))
                    if fallback_name in seen_model_names:
                        continue
                    relationships = sorted(
                        [
                            {"field": f_name, "ref": _canonicalize_model_name(f_meta["ref"])}
                            for f_name, f_meta in fields.items()
                            if isinstance(f_meta, dict) and f_meta.get("ref")
                        ],
                        key=lambda x: (x["field"], x["ref"]),
                    )
                    models.append({"name": fallback_name, "fields": fields, "relationships": relationships})
                    seen_model_names.add(fallback_name)
                    
                models.sort(key=lambda row: row["name"])
                return models
    except Exception as e:
        print(f"Warning: Mongoose AST extraction failed: {e}. Falling back to regex.")

    schema_fields_by_var: dict[str, dict[str, dict[str, Any]]] = {}

    for m in SCHEMA_DECL_RX.finditer(content):
        schema_var = m.group(1)
        open_idx = content.find("(", m.end() - 1)
        if open_idx < 0:
            continue
        body = _extract_first_object_arg(content, open_idx)
        if not body:
            continue
        schema_fields_by_var[schema_var] = _parse_schema_fields(body)

    models: list[dict[str, Any]] = []
    seen_model_names: set[str] = set()
    for m in MONGOOSE_MODEL_DECL_RX.finditer(content):
        open_idx = content.find("(", m.end() - 1)
        close_idx = _find_matching(content, open_idx, "(", ")")
        if open_idx < 0 or close_idx < 0:
            continue
        args = _split_top_level(content[open_idx + 1 : close_idx])
        if len(args) < 2:
            continue
        model_name_expr = args[0].strip()
        schema_expr = args[1].strip()
        if not _is_string_literal(model_name_expr):
            continue
        model_name = _canonicalize_model_name(_strip_quotes(model_name_expr))
        if not model_name:
            continue
        fields = schema_fields_by_var.get(schema_expr) or _extract_inline_schema_fields(schema_expr)
        relationships = sorted(
            [
                {"field": f_name, "ref": _canonicalize_model_name(f_meta["ref"])}
                for f_name, f_meta in fields.items()
                if isinstance(f_meta, dict) and f_meta.get("ref")
            ],
            key=lambda x: (x["field"], x["ref"]),
        )
        models.append({"name": model_name, "fields": fields, "relationships": relationships})
        seen_model_names.add(model_name)

    # Identify embedded subdocument schemas in the same file
    referenced_schemas = set()
    for other_var, fields in schema_fields_by_var.items():
        for f_name, f_meta in fields.items():
            for s_var in schema_fields_by_var.keys():
                if s_var != other_var and _has_schema_reference(f_meta, s_var):
                    referenced_schemas.add(s_var)

    # Fallback: schema declaration without explicit model() call.
    for schema_var, fields in sorted(schema_fields_by_var.items(), key=lambda kv: kv[0]):
        if schema_var in referenced_schemas:
            continue
        fallback_name = _canonicalize_model_name(_infer_model_name_from_schema_var(schema_var))
        if fallback_name in seen_model_names:
            continue
        relationships = sorted(
            [
                {"field": f_name, "ref": _canonicalize_model_name(f_meta["ref"])}
                for f_name, f_meta in fields.items()
                if isinstance(f_meta, dict) and f_meta.get("ref")
            ],
            key=lambda x: (x["field"], x["ref"]),
        )
        models.append({"name": fallback_name, "fields": fields, "relationships": relationships})
        seen_model_names.add(fallback_name)

    models.sort(key=lambda row: row["name"])
    return models


def _extract_prisma_relationships(content: str) -> list[dict]:
    rels: list[dict] = []
    current_model = None
    for line in content.splitlines():
        model_match = re.match(r"^\s*model\s+(\w+)\s*\{", line)
        if model_match:
            current_model = model_match.group(1)
            continue
        if current_model and line.strip().startswith("}"):
            current_model = None
            continue
        if not current_model:
            continue

        field = line.strip()
        if not field or field.startswith("//"):
            continue

        if "@relation" in field:
            parts = field.split()
            if len(parts) >= 2:
                target = re.sub(r"[\[\]?]", "", parts[1])
                if target and target[0].isupper() and target != current_model:
                    kind = "many-to-many" if "[]" in parts[1] else "one-to-one"
                    rels.append({"from": current_model, "to": target, "type": kind})
    return rels


def build_data_model(file_paths: list[str], read_file: Callable[[str], str | None]) -> dict:
    entities: dict[str, dict[str, Any]] = {}
    relationships: set[tuple[str, str, str, str]] = set()

    for path in sorted(file_paths):
        if FileFilter.should_exclude_from_entity_analysis(path):
            continue

        lower = path.lower()
        content = read_file(path) or ""
        if not content:
            continue

        if lower.endswith((".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs")):
            for model in extract_mongoose_models(content, path):
                name = model["name"]
                fields = model.get("fields", {})
                entities[name] = {
                    "name": name,
                    "type": "mongoose_model",
                    "database": "mongodb",
                    "orm": "mongoose",
                    "fields": fields,
                    "source_file": path,
                }
                for rel in model.get("relationships", []):
                    relationships.add((f"{name}.{rel['field']}", rel["ref"], "references", rel["field"]))

        if lower.endswith("schema.prisma"):
            for match in PRISMA_MODEL_RX.finditer(content):
                entities.setdefault(
                    match.group(1),
                    {"name": match.group(1), "type": "prisma_model", "database": "sql", "orm": "prisma", "fields": {}, "source_file": path},
                )
            for rel in _extract_prisma_relationships(content):
                relationships.add((rel["from"], rel["to"], rel["type"], ""))

        if lower.endswith(".java") and JPA_ENTITY_RX.search(content):
            class_match = JPA_CLASS_RX.search(content)
            if class_match:
                entities.setdefault(
                    class_match.group(1),
                    {"name": class_match.group(1), "type": "jpa_entity", "database": "sql", "orm": "jpa", "fields": {}, "source_file": path},
                )

            for rel_rx, rel_type in [
                (r"@OneToMany\s*(?:\([^)]*targetEntity\s*=\s*(\w+)\.class[^)]*\))?", "one-to-many"),
                (r"@ManyToMany\s*(?:\([^)]*targetEntity\s*=\s*(\w+)\.class[^)]*\))?", "many-to-many"),
                (r"@OneToOne\s*(?:\([^)]*targetEntity\s*=\s*(\w+)\.class[^)]*\))?", "one-to-one"),
            ]:
                for rel_match in re.finditer(rel_rx, content):
                    target = rel_match.group(1)
                    if target and class_match:
                        relationships.add((class_match.group(1), target, rel_type, ""))

    entity_rows = [entities[name] for name in sorted(entities.keys())]
    relation_rows = [
        {"from": src, "to": dst, "type": kind, "field": field}
        for src, dst, kind, field in sorted(relationships, key=lambda x: (x[0], x[1], x[2], x[3]))
        if src and dst
    ]

    return {
        "entities": entity_rows,
        "relationships": relation_rows,
    }

