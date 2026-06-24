import pytest
from src.intelligence.evidence.context import AnalysisContext
from src.intelligence.collectors.repository_collector import build_components
from src.intelligence.resolvers.api_evidence_builder import build_apis
from src.intelligence.resolvers.route_evidence_builder import build_routers, build_frontend, build_mounts
from src.intelligence.analyzers.symbol_evidence_builder import build_entities
from src.intelligence.analyzers.dependency_evidence_builder import build_services, build_repositories
from src.intelligence.enrichers.evidence_enricher import build_relationships, build_quality_warnings
from src.intelligence.serializers.evidence_serializer import serialize_evidence

def _noop_read(path: str) -> str | None:
    return None

def test_analysis_context_caching():
    read_count = 0
    def custom_read(path: str) -> str | None:
        nonlocal read_count
        read_count += 1
        return f"content of {path}"

    context = AnalysisContext(
        file_paths=["src/index.js", "src/auth.js"],
        read_file=custom_read,
        features_map={}
    )

    # First read
    assert context.read_file("src/index.js") == "content of src/index.js"
    assert read_count == 1

    # Second read (cached)
    assert context.read_file("src/index.js") == "content of src/index.js"
    assert read_count == 1

    # Diff file read
    assert context.read_file("src/auth.js") == "content of src/auth.js"
    assert read_count == 2


def test_repository_collector_builds_components():
    context = AnalysisContext(
        file_paths=["backend/src/routes/auth.routes.js", "frontend/src/components/Button.tsx"],
        read_file=_noop_read,
        features_map={}
    )
    tech_stack = {"backend_framework": "express", "frontend_framework": "react"}
    components = build_components(context, tech_stack)
    
    assert len(components) == 2
    comp_names = {c["name"] for c in components}
    assert "auth" in comp_names
    assert "button" in comp_names


def test_api_evidence_builder():
    content_map = {
        "routes/users.js": "const router = express.Router(); router.get('/', handler);"
    }
    context = AnalysisContext(
        file_paths=["routes/users.js"],
        read_file=lambda p: content_map.get(p),
        features_map={
            "routes/users.js": {
                "api_endpoints": [{"verb": "GET", "route": "/", "line": 1}]
            }
        }
    )
    components = [{"name": "users", "files": ["routes/users.js"]}]
    apis = build_apis(context, components)
    
    assert len(apis) == 1
    assert apis[0]["method"] == "GET"
    assert apis[0]["path"] == "/"
    assert apis[0]["module"] == "users"


def test_route_evidence_builder_routers_and_mounts():
    context = AnalysisContext(
        file_paths=["routes/auth.js"],
        read_file=lambda p: "const router = express.Router();",
        features_map={}
    )
    routers = build_routers(context)
    assert len(routers) == 1
    assert routers[0]["name"] == "router"
    assert routers[0]["type"] == "express_router"


def test_symbol_evidence_builder():
    context = AnalysisContext(
        file_paths=["models/User.js"],
        read_file=lambda p: "mongoose.model('User', schema);",
        features_map={}
    )
    entities, edges = build_entities(context, {"models/User.js": ["MONGOOSE_MODEL:User"]}, {})
    assert len(entities) == 1
    assert entities[0]["name"] == "User"
    assert entities[0]["orm"] == "mongoose"


def test_dependency_evidence_builder():
    context = AnalysisContext(
        file_paths=["services/payment.service.ts"],
        read_file=_noop_read,
        features_map={
            "services/payment.service.ts": {
                "classes": ["PaymentService"],
                "methods": ["pay"]
            }
        }
    )
    services = build_services(context, [{"name": "payment", "files": ["services/payment.service.ts"]}])
    assert len(services) == 1
    assert services[0]["name"] == "PaymentService"
    assert services[0]["type"] == "service_module"


def test_enricher_and_serializer():
    context = AnalysisContext(
        file_paths=["server.js"],
        read_file=lambda p: "const express = require('express');",
        features_map={}
    )
    
    components = [{"name": "server", "files": ["server.js"], "type": "backend_module", "framework": "express"}]
    apis = [{"method": "GET", "path": "/health", "module": "server", "controller": "health", "source_file": "server.js"}]
    
    relationships = build_relationships(
        context=context,
        components=components,
        services=[],
        repositories=[],
        routers=[],
        entities=[],
        schema_edges=[],
        apis=apis
    )
    
    assert len(relationships) == 1
    assert relationships[0]["type"] == "EXPOSES_API"
    
    # Serialize and validate
    output = serialize_evidence(
        components=components,
        apis=apis,
        entities=[],
        services=[],
        repositories=[],
        mounts=[],
        relationships=relationships,
        frontend_routes=[],
        routers=[],
        tech_stack={"backend_framework": "express"},
        features_map={},
        quality_warnings=[],
        include_extended=False
    )
    
    assert output["tech_stack"]["backend"] == ["express"]
    assert output["components"] == output["modules"]
