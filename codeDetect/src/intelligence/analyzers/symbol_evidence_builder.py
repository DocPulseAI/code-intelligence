import os
import re
from typing import Any, Dict, List, Tuple
from src.file_filter import FileFilter
from src.intelligence.data_model_graph import extract_mongoose_models
from src.intelligence.evidence.context import AnalysisContext

def _canonicalize_model_name(name: str) -> str:
    if not name:
        return name
    return name[0].upper() + name[1:]

def build_entities(
    context: AnalysisContext,
    schema_tags_map: Dict[str, List[str]],
    tech_stack: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    entities: List[Dict[str, Any]] = []
    schema_edges: List[Dict[str, Any]] = []
    seen: set[str] = set()

    MONGOOSE_MODEL_RX = re.compile(r"mongoose\.model\s*\(\s*['\"](\w+)['\"]")
    PRISMA_MODEL_RX = re.compile(r"^\s*model\s+(\w+)\s*\{", re.MULTILINE)
    JPA_CLASS_RX = re.compile(r"class\s+(\w+)")
    DJANGO_CLASS_RX = re.compile(r"class\s+(\w+)\s*\(\s*(?:models\.)?Model\s*\)")
    SQLALCHEMY_MODEL_RX = re.compile(r"class\s+(\w+)\s*\(\s*Base\s*\)")
    SQLALCHEMY_TABLE_NAME_RX = re.compile(r"__tablename__\s*=\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]")
    ALEMBIC_CREATE_TABLE_RX = re.compile(r"op\.create_table\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]")

    for path in sorted(context.file_paths):
        if FileFilter.should_exclude_from_entity_analysis(path):
            continue
        feats = context.features_map.get(path, {})
        tags = schema_tags_map.get(path, [])
        lower = path.lower()
        content = context.read_file(path) or ""

        # JPA entities
        if "JPA_ENTITY" in tags or feats.get("schema_annotations"):
            m = JPA_CLASS_RX.search(content)
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                entities.append({
                    "name": m.group(1),
                    "type": "jpa_entity",
                    "database": "sql",
                    "orm": "jpa",
                    "source_file": path,
                })

        # Mongoose models
        mongoose_models = extract_mongoose_models(content, path) if lower.endswith((".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs")) else []
        if not mongoose_models:
            mongoose_names = set()
            for tag in tags:
                if tag.startswith("MONGOOSE_MODEL:"):
                    mongoose_names.add(_canonicalize_model_name(tag.split(":", 1)[1]))
            if any(t == "MONGOOSE_SCHEMA" for t in tags):
                for match in MONGOOSE_MODEL_RX.finditer(content):
                    mongoose_names.add(_canonicalize_model_name(match.group(1)))
            for name in sorted(mongoose_names):
                mongoose_models.append({"name": name, "fields": {}, "relationships": []})

        for model in mongoose_models:
            model_name = _canonicalize_model_name(str(model.get("name", "")).strip())
            if not model_name:
                continue
            fields = model.get("fields", {}) if isinstance(model.get("fields"), dict) else {}
            if model_name not in seen:
                seen.add(model_name)
                entities.append({
                    "name": model_name,
                    "type": "mongoose_model",
                    "database": "mongodb",
                    "orm": "mongoose",
                    "fields": fields,
                    "source_file": path,
                })
            else:
                for row in entities:
                    if row.get("name") == model_name and not row.get("fields") and fields:
                        row["fields"] = fields
                        break

            for rel in model.get("relationships", []):
                field_name = str(rel.get("field", "")).strip()
                ref_model = _canonicalize_model_name(str(rel.get("ref", "")).strip())
                if not field_name or not ref_model:
                    continue
                schema_edges.append({
                    "type": "entity_relation",
                    "from": model_name,
                    "to": ref_model,
                    "relation": "references",
                    "field": field_name,
                })

        # Django models
        if "DJANGO_MODEL" in tags:
            for match in DJANGO_CLASS_RX.finditer(content):
                name = match.group(1)
                if name not in seen:
                    seen.add(name)
                    entities.append({
                        "name": name,
                        "type": "django_model",
                        "database": "sql",
                        "orm": "django",
                        "source_file": path,
                    })

        # SQLAlchemy declarative models
        if lower.endswith(".py"):
            if "sqlalchemy" in content or "declarative_base" in content:
                for match in SQLALCHEMY_MODEL_RX.finditer(content):
                    model_name = match.group(1)
                    if model_name and model_name not in seen:
                        seen.add(model_name)
                        entities.append({
                            "name": model_name,
                            "type": "sqlalchemy_model",
                            "database": "sql",
                            "orm": "sqlalchemy",
                            "source_file": path,
                        })

                for tmatch in SQLALCHEMY_TABLE_NAME_RX.finditer(content):
                    table_name = tmatch.group(1)
                    if table_name and table_name not in seen:
                        seen.add(table_name)
                        entities.append({
                            "name": table_name,
                            "type": "sql_table",
                            "database": "sql",
                            "orm": "sqlalchemy",
                            "source_file": path,
                        })

                for cmatch in ALEMBIC_CREATE_TABLE_RX.finditer(content):
                    table_name = cmatch.group(1)
                    if table_name and table_name not in seen:
                        seen.add(table_name)
                        entities.append({
                            "name": table_name,
                            "type": "sql_table",
                            "database": "sql",
                            "orm": "alembic",
                            "source_file": path,
                        })

        # Prisma models
        if lower.endswith("schema.prisma"):
            PRISMA_MODEL_FULL_RX = re.compile(r"^\s*model\s+(\w+)\s*\{([^}]+)\}", re.MULTILINE)
            prisma_models_data = {}

            for match in PRISMA_MODEL_FULL_RX.finditer(content):
                name = match.group(1)
                block = match.group(2)
                if name not in seen:
                    seen.add(name)
                
                fields = []
                field_types = {}
                for line in block.strip().split("\n"):
                    line = line.strip()
                    if not line or line.startswith("//") or line.startswith("@@"):
                        continue
                    parts = line.split()
                    if len(parts) >= 2:
                        fname = parts[0]
                        ftype = parts[1]
                        fields.append(fname)
                        field_types[fname] = ftype
                        
                fields = sorted(list(set(fields)))
                prisma_models_data[name] = {"fields": fields, "types": field_types}

                entities.append({
                    "name": name,
                    "type": "prisma_model",
                    "database": "sql",
                    "orm": "prisma",
                    "fields": fields,
                    "source_file": path,
                })
                
            for model_name, data in prisma_models_data.items():
                for fname, ftype in data["types"].items():
                    is_array = ftype.endswith("[]")
                    base_type = ftype.replace("[]", "").replace("?", "")
                    
                    if base_type in prisma_models_data:
                        other_data = prisma_models_data[base_type]
                        back_refs = [t for n, t in other_data["types"].items() if t.replace("[]", "").replace("?", "") == model_name]
                        is_other_array = any(t.endswith("[]") for t in back_refs)
                        
                        if is_array and is_other_array:
                            rel_type = "many-to-many"
                        elif is_array and not is_other_array:
                            rel_type = "one-to-many"
                        elif not is_array and is_other_array:
                            rel_type = "many-to-one"
                        else:
                            rel_type = "one-to-one"
                            
                        if rel_type == "many-to-one":
                            from_m = base_type
                            to_m = model_name
                            emit_type = "one-to-many"
                        elif rel_type == "many-to-many":
                            from_m, to_m = sorted([model_name, base_type])
                            emit_type = "many-to-many"
                        elif rel_type == "one-to-one":
                            from_m, to_m = sorted([model_name, base_type])
                            emit_type = "one-to-one"
                        else:
                            from_m = base_type
                            to_m = model_name
                            emit_type = "one-to-many"
                            
                        schema_edges.append({
                            "type": emit_type,
                            "from": from_m,
                            "to": to_m,
                        })

        # Sequelize models
        SEQUELIZE_RX = re.compile(r"sequelize\.define\s*\(\s*['\"](\w+)['\"]", re.IGNORECASE)
        for match in SEQUELIZE_RX.finditer(content):
            name = match.group(1)
            if name:
                name = name[0].upper() + name[1:]
            if name not in seen:
                seen.add(name)
                entities.append({
                    "name": name,
                    "type": "sequelize_model",
                    "database": "sql",
                    "orm": "sequelize",
                    "source_file": path,
                })

        # TypeORM models
        TYPEORM_RX = re.compile(r"@Entity\b[\s\S]*?(?:export\s+)?class\s+(\w+)\b")
        if any("Entity" in str(ann) for ann in feats.get("annotations", [])) or "@Entity" in content:
            for match in TYPEORM_RX.finditer(content):
                name = match.group(1)
                if name not in seen:
                    seen.add(name)
                    entities.append({
                        "name": name,
                        "type": "typeorm_entity",
                        "database": "sql",
                        "orm": "typeorm",
                        "source_file": path,
                    })
            for match in PRISMA_MODEL_RX.finditer(content):
                name = match.group(1)
                if name not in seen:
                    seen.add(name)
                    entities.append({
                        "name": name,
                        "type": "prisma_model",
                        "database": "sql",
                        "orm": "prisma",
                        "source_file": path,
                    })

        # SQL table definitions
        for tag in tags:
            if tag.startswith("SQL_CREATE_TABLE:"):
                table_name = tag.split(":", 1)[1]
                if table_name not in seen:
                    seen.add(table_name)
                    entities.append({
                        "name": table_name,
                        "type": "sql_table",
                        "database": "postgres" if "POSTGRES_SCHEMA_CHANGE" in tags else "sql",
                        "orm": "none",
                        "source_file": path,
                    })
            elif tag.startswith("SQL_FOREIGN_KEY:"):
                parts = tag.split(":")
                if len(parts) >= 3:
                    src_table = parts[1]
                    target_table = parts[2]
                    schema_edges.append({
                        "type": "entity_relation",
                        "from": src_table,
                        "to": target_table,
                        "relation": "foreign_key",
                    })

    entities = sorted(entities, key=lambda e: e.get("name", ""))
    schema_edges = sorted(
        [dict(s) for s in set(frozenset(d.items()) for d in schema_edges)],
        key=lambda e: (e["from"], e["to"])
    )
    return entities, schema_edges
