"""Deterministic data model extraction for Mongoose, Prisma, and JPA."""

from __future__ import annotations

import re
from typing import Callable


MONGOOSE_MODEL_RX = re.compile(r"mongoose\.model\s*\(\s*['\"](\w+)['\"]")
JPA_ENTITY_RX = re.compile(r"@Entity\b")
JPA_CLASS_RX = re.compile(r"class\s+(\w+)")
PRISMA_MODEL_RX = re.compile(r"^\s*model\s+(\w+)\s*\{", re.MULTILINE)


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
    entities: set[str] = set()
    relationships: set[tuple[str, str, str]] = set()

    for path in sorted(file_paths):
        lower = path.lower()
        content = read_file(path) or ""
        if not content:
            continue

        if lower.endswith((".js", ".ts", ".jsx", ".tsx")):
            for match in MONGOOSE_MODEL_RX.finditer(content):
                entities.add(match.group(1))

        if lower.endswith("schema.prisma"):
            for match in PRISMA_MODEL_RX.finditer(content):
                entities.add(match.group(1))
            for rel in _extract_prisma_relationships(content):
                relationships.add((rel["from"], rel["to"], rel["type"]))

        if lower.endswith(".java") and JPA_ENTITY_RX.search(content):
            class_match = JPA_CLASS_RX.search(content)
            if class_match:
                entities.add(class_match.group(1))

            for rel_rx, rel_type in [
                (r"@OneToMany\s*(?:\([^)]*targetEntity\s*=\s*(\w+)\.class[^)]*\))?", "one-to-many"),
                (r"@ManyToMany\s*(?:\([^)]*targetEntity\s*=\s*(\w+)\.class[^)]*\))?", "many-to-many"),
                (r"@OneToOne\s*(?:\([^)]*targetEntity\s*=\s*(\w+)\.class[^)]*\))?", "one-to-one"),
            ]:
                for rel_match in re.finditer(rel_rx, content):
                    target = rel_match.group(1)
                    if target:
                        relationships.add((class_match.group(1) if class_match else "", target, rel_type))

    entity_rows = [{"name": name} for name in sorted(entities)]
    relation_rows = [
        {"from": f, "to": t, "type": typ}
        for f, t, typ in sorted(relationships, key=lambda x: (x[0], x[1], x[2]))
        if f and t
    ]

    return {
        "entities": entity_rows,
        "relationships": relation_rows,
    }
