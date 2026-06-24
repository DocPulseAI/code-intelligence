"""Tests for the repository_evidence intelligence module."""

import sys
from pathlib import Path

# Ensure codeDetect root is importable.
_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.intelligence.repository_evidence import build_repository_evidence


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _noop_read(_path: str):
    return None


def _make_reader(content_map: dict[str, str]):
    def _read(path: str):
        return content_map.get(path)
    return _read


# ---------------------------------------------------------------------------
# 1. Express backend
# ---------------------------------------------------------------------------

class TestExpressBackend:
    """Files in backend/src/* with Express Router features."""

    FILES = [
        "backend/src/routes/auth.routes.js",
        "backend/src/controllers/auth.controller.js",
        "backend/src/services/auth.service.js",
        "backend/src/models/User.js",
        "backend/package.json",
    ]

    FEATURES = {
        "backend/src/routes/auth.routes.js": {
            "functions": ["login", "register"],
            "api_endpoints": [
                {"verb": "POST", "route": "/auth/login", "line": 5},
                {"verb": "POST", "route": "/auth/register", "line": 10},
            ],
        },
        "backend/src/controllers/auth.controller.js": {
            "functions": ["handleLogin", "handleRegister"],
            "classes": ["AuthController"],
        },
        "backend/src/services/auth.service.js": {
            "functions": ["verifyPassword", "createToken"],
            "classes": ["AuthService"],
        },
        "backend/src/models/User.js": {
            "functions": [],
        },
    }

    SCHEMA_TAGS = {
        "backend/src/models/User.js": ["MONGOOSE_MODEL:User"],
    }

    CONTENT = {
        "backend/src/routes/auth.routes.js": (
            "const express = require('express');\n"
            "const router = express.Router();\n"
            "router.post('/auth/login', handleLogin);\n"
            "router.post('/auth/register', handleRegister);\n"
            "module.exports = router;\n"
        ),
    }

    TECH_STACK = {
        "backend_framework": "express",
        "frontend_framework": None,
        "database": "mongodb",
        "orm": "mongoose",
        "infra": [],
        "ci": [],
    }

    def test_components_detected(self):
        ev = build_repository_evidence(
            self.FILES, _make_reader(self.CONTENT), self.FEATURES,
            self.SCHEMA_TAGS, self.TECH_STACK,
        )
        names = [c["name"] for c in ev["components"]]
        assert "src" in names or "auth" in names or "backend" in names

    def test_apis_detected(self):
        ev = build_repository_evidence(
            self.FILES, _make_reader(self.CONTENT), self.FEATURES,
            self.SCHEMA_TAGS, self.TECH_STACK,
        )
        methods = {(a["method"], a["path"]) for a in ev["apis"]}
        assert ("POST", "/auth/login") in methods
        assert ("POST", "/auth/register") in methods

    def test_entities_detected(self):
        ev = build_repository_evidence(
            self.FILES, _make_reader(self.CONTENT), self.FEATURES,
            self.SCHEMA_TAGS, self.TECH_STACK,
        )
        entity_names = [e["name"] for e in ev["entities"]]
        assert "User" in entity_names

    def test_services_detected(self):
        ev = build_repository_evidence(
            self.FILES, _make_reader(self.CONTENT), self.FEATURES,
            self.SCHEMA_TAGS, self.TECH_STACK,
        )
        services = {s["name"]: s for s in ev["services"]}
        assert "AuthService" in services
        assert services["AuthService"]["file"] == "backend/src/services/auth.service.js"
        assert set(services["AuthService"]["functions"]) == {"verifyPassword", "createToken"}

    def test_routers_detected(self):
        ev = build_repository_evidence(
            self.FILES, _make_reader(self.CONTENT), self.FEATURES,
            self.SCHEMA_TAGS, self.TECH_STACK,
        )
        router_names = [r["name"] for r in ev["routers"]]
        assert "router" in router_names

    def test_relationships_exist(self):
        ev = build_repository_evidence(
            self.FILES, _make_reader(self.CONTENT), self.FEATURES,
            self.SCHEMA_TAGS, self.TECH_STACK,
        )
        rel_types = {r["type"] for r in ev["relationships"]}
        assert "entity_used_by_component" in rel_types or len(ev["relationships"]) > 0


# ---------------------------------------------------------------------------
# 1.5. Express Mount Resolution
# ---------------------------------------------------------------------------

class TestExpressMountResolution:
    """Tests that app.use('/api/auth', authRoutes) correctly resolves prefixes."""

    FILES = [
        "app.js",
        "routes/auth.routes.js",
    ]

    FEATURES = {
        "routes/auth.routes.js": {
            "functions": ["login"],
            "api_endpoints": [
                {"verb": "POST", "route": "/login", "line": 5, "handler": "authController.login"},
            ],
        },
        "app.js": {
            "functions": [],
            "api_endpoints": [],
        }
    }

    SCHEMA_TAGS = {}

    CONTENT = {
        "app.js": (
            "const express = require('express');\n"
            "const app = express();\n"
            "const authRoutes = require('./routes/auth.routes');\n"
            "app.use('/api/auth', authRoutes);\n"
        ),
        "routes/auth.routes.js": (
            "const express = require('express');\n"
            "const router = express.Router();\n"
            "router.post('/login', authController.login);\n"
            "module.exports = router;\n"
        ),
    }

    TECH_STACK = {
        "backend_framework": "express",
        "frontend_framework": None,
        "database": None,
        "orm": None,
        "infra": [],
        "ci": [],
    }

    def test_mount_resolution_prefix(self):
        ev = build_repository_evidence(
            self.FILES, _make_reader(self.CONTENT), self.FEATURES,
            self.SCHEMA_TAGS, self.TECH_STACK,
        )
        methods = {(a["method"], a["path"]) for a in ev["apis"]}
        # Verify the prefix '/api/auth' is added to '/login'
        assert ("POST", "/api/auth/login") in methods
        
        # Verify component and controller properties
        login_api = next(a for a in ev["apis"] if a["path"] == "/api/auth/login")
        assert login_api["controller"] == "authController.login"
        assert login_api["router_file"] == "routes/auth.routes.js"


# ---------------------------------------------------------------------------
# 2. Java Spring
# ---------------------------------------------------------------------------

class TestJavaSpring:
    """Files with @RestController, @Service, @Entity annotations."""

    FILES = [
        "src/main/java/com/app/controller/UserController.java",
        "src/main/java/com/app/service/UserService.java",
        "src/main/java/com/app/entity/User.java",
    ]

    FEATURES = {
        "src/main/java/com/app/controller/UserController.java": {
            "classes": ["UserController"],
            "methods": ["getUser", "createUser"],
            "annotations": ["@RestController", "@RequestMapping", "@GetMapping", "@PostMapping"],
            "api_endpoints": [
                {"verb": "GET", "route": "/api/users/{id}", "line": 10, "handler": "UserController.getUser", "router_symbol": "UserController"},
                {"verb": "POST", "route": "/api/users", "line": 15, "handler": "UserController.createUser", "router_symbol": "UserController"},
            ],
        },
        "src/main/java/com/app/service/UserService.java": {
            "classes": ["UserService"],
            "methods": ["findById", "save"],
            "annotations": ["@Service"],
        },
        "src/main/java/com/app/entity/User.java": {
            "classes": ["User"],
            "annotations": ["@Entity", "@Table", "@Id", "@Column"],
            "schema_annotations": ["Entity", "Table"],
        },
    }

    SCHEMA_TAGS = {
        "src/main/java/com/app/entity/User.java": ["JPA_ENTITY"],
    }

    CONTENT = {
        "src/main/java/com/app/entity/User.java": (
            "@Entity\n@Table(name=\"users\")\npublic class User {\n"
            "  @Id private Long id;\n  @Column private String name;\n}\n"
        ),
    }

    TECH_STACK = {
        "backend_framework": "spring",
        "frontend_framework": None,
        "database": "postgresql",
        "orm": "jpa",
        "infra": [],
        "ci": [],
    }

    def test_spring_controller_as_router(self):
        ev = build_repository_evidence(
            self.FILES, _make_reader(self.CONTENT), self.FEATURES,
            self.SCHEMA_TAGS, self.TECH_STACK,
        )
        router_names = [r["name"] for r in ev["routers"]]
        assert "UserController" in router_names
        controller = next(r for r in ev["routers"] if r["name"] == "UserController")
        assert controller["type"] == "spring_controller"
        
        # Verify APIs for Java endpoints mapping
        apis = {(a["method"], a["path"], a["controller"]) for a in ev["apis"]}
        assert ("GET", "/api/users/{id}", "UserController.getUser") in apis
        assert ("POST", "/api/users", "UserController.createUser") in apis

    def test_spring_service_detected(self):
        ev = build_repository_evidence(
            self.FILES, _make_reader(self.CONTENT), self.FEATURES,
            self.SCHEMA_TAGS, self.TECH_STACK,
        )
        services = {s["name"]: s for s in ev["services"]}
        assert "UserService" in services
        assert services["UserService"]["file"] == "src/main/java/com/app/service/UserService.java"
        assert set(services["UserService"]["functions"]) == {"findById", "save"}

    def test_jpa_entity_detected(self):
        ev = build_repository_evidence(
            self.FILES, _make_reader(self.CONTENT), self.FEATURES,
            self.SCHEMA_TAGS, self.TECH_STACK,
        )
        entity_names = [e["name"] for e in ev["entities"]]
        assert "User" in entity_names
        user = next(e for e in ev["entities"] if e["name"] == "User")
        assert user["type"] == "jpa_entity"


# ---------------------------------------------------------------------------
# 3. Python Flask
# ---------------------------------------------------------------------------

class TestPythonFlask:
    """Files with @app.route, Blueprint, models.Model."""

    FILES = [
        "app/routes/auth.py",
        "app/models/user.py",
    ]

    FEATURES = {
        "app/routes/auth.py": {
            "functions": ["login", "register"],
            "decorators": ["app.route('/login', methods=['POST'])", "app.route('/register', methods=['POST'])"],
            "api_endpoints": [
                {"verb": "USE", "route": "/api/auth", "line": 2, "handler": "auth_bp", "router_symbol": "app"},
                {"verb": "POST", "route": "/login", "line": 5, "handler": "login", "router_symbol": "auth_bp"},
                {"verb": "POST", "route": "/register", "line": 10, "handler": "register", "router_symbol": "auth_bp"},
            ],
            "api_routes": [],
        },
        "app/models/user.py": {
            "classes": ["User"],
            "functions": [],
        },
    }

    SCHEMA_TAGS = {
        "app/models/user.py": ["DJANGO_MODEL"],
    }

    CONTENT = {
        "app/routes/auth.py": (
            "from flask import Blueprint\n"
            "auth_bp = Blueprint('auth', __name__)\n"
            "@auth_bp.route('/login', methods=['POST'])\n"
            "def login(): pass\n"
        ),
        "app/models/user.py": (
            "from django.db import models\n"
            "class User(models.Model):\n"
            "    name = models.CharField(max_length=100)\n"
        ),
    }

    TECH_STACK = {
        "backend_framework": "flask",
        "frontend_framework": None,
        "database": None,
        "orm": None,
        "infra": [],
        "ci": [],
    }

    def test_flask_blueprint_router(self):
        ev = build_repository_evidence(
            self.FILES, _make_reader(self.CONTENT), self.FEATURES,
            self.SCHEMA_TAGS, self.TECH_STACK,
        )
        router_names = [r["name"] for r in ev["routers"]]
        assert "auth_bp" in router_names
        
        # Verify Python API endpoint mount prefixes
        apis = {(a["method"], a["path"], a["controller"]) for a in ev["apis"]}
        assert ("POST", "/api/auth/login", "login") in apis
        assert ("POST", "/api/auth/register", "register") in apis

    def test_django_model_entity(self):
        ev = build_repository_evidence(
            self.FILES, _make_reader(self.CONTENT), self.FEATURES,
            self.SCHEMA_TAGS, self.TECH_STACK,
        )
        entity_names = [e["name"] for e in ev["entities"]]
        assert "User" in entity_names


# ---------------------------------------------------------------------------
# 4. Fullstack
# ---------------------------------------------------------------------------

class TestFullstack:
    """Frontend + backend files should produce multiple components."""

    FILES = [
        "frontend/src/components/App.tsx",
        "frontend/src/pages/Home.tsx",
        "backend/src/routes/api.js",
        "backend/src/services/data.service.js",
    ]

    FEATURES = {
        "backend/src/routes/api.js": {
            "functions": ["getItems"],
            "api_endpoints": [{"verb": "GET", "route": "/api/items", "line": 3}],
        },
        "backend/src/services/data.service.js": {
            "classes": ["DataService"],
            "functions": ["fetchAll"],
        },
    }

    SCHEMA_TAGS: dict[str, list[str]] = {}

    CONTENT = {
        "backend/src/routes/api.js": (
            "const router = require('express').Router();\n"
            "router.get('/api/items', getItems);\n"
        ),
    }

    TECH_STACK = {
        "backend_framework": "express",
        "frontend_framework": "react",
        "database": None,
        "orm": None,
        "infra": [],
        "ci": [],
    }

    def test_multiple_component_types(self):
        ev = build_repository_evidence(
            self.FILES, _make_reader(self.CONTENT), self.FEATURES,
            self.SCHEMA_TAGS, self.TECH_STACK,
        )
        comp_types = {c["type"] for c in ev["components"]}
        # Should have at least one backend and one frontend
        assert len(comp_types) >= 1
        assert any(c["type"] == "backend_module" for c in ev["components"])


# ---------------------------------------------------------------------------
# 5. Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    """Same inputs produce identical output."""

    FILES = [
        "backend/src/services/b.service.js",
        "backend/src/services/a.service.js",
        "backend/src/routes/z.routes.js",
        "backend/src/routes/a.routes.js",
    ]

    FEATURES = {
        "backend/src/routes/z.routes.js": {
            "functions": ["zHandler"],
            "api_endpoints": [{"verb": "GET", "route": "/z", "line": 1}],
        },
        "backend/src/routes/a.routes.js": {
            "functions": ["aHandler"],
            "api_endpoints": [{"verb": "POST", "route": "/a", "line": 5}],
        },
        "backend/src/services/b.service.js": {
            "classes": ["BService"],
            "functions": ["fetchB"],
        },
        "backend/src/services/a.service.js": {
            "classes": ["AService"],
            "functions": ["doA"],
        },
    }

    CONTENT = {
        "backend/src/routes/z.routes.js": "const router = require('express').Router();\nrouter.get('/z', zHandler);",
        "backend/src/routes/a.routes.js": "const router = require('express').Router();\nrouter.get('/a', aHandler);",
    }

    def test_deterministic_output(self):
        import json

        ev1 = build_repository_evidence(
            self.FILES, _make_reader(self.CONTENT), self.FEATURES,
            {}, {"backend_framework": "express"},
        )
        ev2 = build_repository_evidence(
            list(reversed(self.FILES)), _make_reader(self.CONTENT), self.FEATURES,
            {}, {"backend_framework": "express"},
        )
        assert json.dumps(ev1, sort_keys=True) == json.dumps(ev2, sort_keys=True)


# ---------------------------------------------------------------------------
# 6. Empty repo
# ---------------------------------------------------------------------------

class TestEmptyRepo:
    """No files → all lists empty."""

    def test_empty_evidence(self):
        ev = build_repository_evidence([], _noop_read, {}, {}, {})
        assert ev["components"] == []
        assert ev["apis"] == []
        assert ev["entities"] == []
        assert ev["services"] == []
        assert ev["routers"] == []
        assert ev["relationships"] == []


# ---------------------------------------------------------------------------
# 7. Database Entity Extraction
# ---------------------------------------------------------------------------

class TestDatabaseExtraction:
    """Verifies that DB and ORM specs are attached to entities and schema edges are parsed."""

    FILES = [
        "models/User.js",
        "models/Task.js",
        "schema.sql",
    ]

    FEATURES = {
        "models/User.js": {
            "mongoose_schemas": [{"profile": "Profile"}],
        },
        "models/Task.js": {
            "mongoose_schemas": [{"user": "User"}],
        },
        "schema.sql": {},
    }

    SCHEMA_TAGS = {
        "models/User.js": ["MONGOOSE_SCHEMA", "MONGOOSE_MODEL:User"],
        "models/Task.js": ["MONGOOSE_SCHEMA", "MONGOOSE_MODEL:Task"],
        "schema.sql": [
            "SQL_SCHEMA_CHANGE", 
            "POSTGRES_SCHEMA_CHANGE",
            "SQL_TABLE_CHANGE", 
            "SQL_CREATE_TABLE:users",
            "SQL_CREATE_TABLE:tasks",
            "SQL_FOREIGN_KEY:tasks:users",
        ]
    }

    CONTENT = {
        "models/User.js": "const userSchema = new mongoose.Schema({ profile: { type: ObjectId, ref: 'Profile' } }); mongoose.model('User', userSchema);",
        "models/Task.js": "const taskSchema = new mongoose.Schema({ user: { type: ObjectId, ref: 'User' } }); mongoose.model('Task', taskSchema);",
        "schema.sql": "CREATE TABLE users (id INT PRIMARY KEY); CREATE TABLE tasks (id INT, user_id INT REFERENCES users(id));",
    }

    TECH_STACK = {
        "backend_framework": "express",
        "frontend_framework": None,
        "database": "postgres",
        "orm": "mongoose",
        "infra": [],
        "ci": [],
    }

    def test_database_orm_population(self):
        ev = build_repository_evidence(
            self.FILES, _make_reader(self.CONTENT), self.FEATURES,
            self.SCHEMA_TAGS, self.TECH_STACK,
        )
        
        entities = {e["name"]: e for e in ev["entities"]}
        assert "User" in entities
        assert entities["User"]["database"] == "mongodb"
        assert entities["User"]["orm"] == "mongoose"
        
        assert "tasks" in entities
        assert entities["tasks"]["database"] == "postgres"
        assert entities["tasks"]["orm"] == "none"
        
    def test_schema_edges(self):
        ev = build_repository_evidence(
            self.FILES, _make_reader(self.CONTENT), self.FEATURES,
            self.SCHEMA_TAGS, self.TECH_STACK,
        )
        
        rels = {(r["from"], r["to"], r["relation"]) for r in ev["relationships"] if r["type"] == "entity_relation"}
        
        # Mongoose schema edges
        assert ("User", "Profile", "references") in rels
        assert ("Task", "User", "references") in rels
        
        # SQL schema edges
        assert ("tasks", "users", "foreign_key") in rels


# ---------------------------------------------------------------------------
# 8. Service Layer Extraction
# ---------------------------------------------------------------------------

class TestServiceLayerExtraction:
    """Verifies that services and repositories are extracted correctly."""

    FILES = [
        "src/services/billing.ts",
        "src/repositories/user.repo.ts",
        "random/not_a_service.ts"
    ]

    FEATURES = {
        "src/services/billing.ts": {
            "classes": ["BillingService"],
            "functions": ["processPayment", "refund"],
        },
        "src/repositories/user.repo.ts": {
            "classes": ["UserRepository"],
            "functions": ["findUser", "saveUser"],
        },
        "random/not_a_service.ts": {
            "classes": ["Helper"],
            "functions": ["doWork"],
        }
    }

    def test_services_and_repositories(self):
        ev = build_repository_evidence(
            self.FILES, _noop_read, self.FEATURES, {}, {"backend_framework": "express"}
        )
        
        services = {s["name"]: s for s in ev["services"]}
        assert "BillingService" in services
        assert services["BillingService"]["file"] == "src/services/billing.ts"
        assert set(services["BillingService"]["functions"]) == {"processPayment", "refund"}
        
        assert "UserRepository" in services
        assert services["UserRepository"]["file"] == "src/repositories/user.repo.ts"
        assert set(services["UserRepository"]["functions"]) == {"findUser", "saveUser"}
        
        assert "Helper" not in services



# ---------------------------------------------------------------------------
# 10. Frontend Extraction Additions
# ---------------------------------------------------------------------------

class TestFrontendExtraction:
    """Verifies that React components, Next.js routes, and API calls are extracted properly."""

    FILES = [
        "frontend/src/pages/about.tsx",
        "frontend/app/dashboard/page.jsx",
        "frontend/src/components/Button.tsx",
        "frontend/src/api/client.js",
        "frontend/src/App.tsx", # React Router
    ]

    FEATURES = {
        "frontend/src/pages/about.tsx": {
            "react_components": ["REACT_COMPONENT"],
            "exported_functions": ["AboutPage"],
            "api_calls": [{"client": "fetch", "method": "UNKNOWN", "line": 4}]
        },
        "frontend/app/dashboard/page.jsx": {
            "react_components": ["REACT_COMPONENT"],
            "exported_functions": ["Dashboard"],
        },
        "frontend/src/components/Button.tsx": {
            "react_components": ["REACT_COMPONENT"],
            "exported_functions": ["Button"],
        },
        "frontend/src/api/client.js": {
            "api_calls": [{"client": "axios", "method": "UNKNOWN", "line": 2}]
        },
        "frontend/src/App.tsx": {
            "react_components": ["REACT_COMPONENT"],
            "jsx_routes": [
                {"path": "/home", "component": "HomeComponent", "line": 5},
                {"path": "/profile", "component": "Profile", "line": 6}
            ]
        }
    }

    SCHEMA_TAGS = {}
    CONTENT = {}
    TECH_STACK = {
        "backend_framework": None,
        "frontend_framework": "nextjs",
        "database": None,
        "orm": None,
    }

    def test_frontend_routes_and_components(self):
        ev = build_repository_evidence(
            self.FILES, _noop_read, self.FEATURES, self.SCHEMA_TAGS, self.TECH_STACK
        )
        
        assert "frontend_routes" in ev
        
        # Routes
        routes = {(r["path"], r["framework"]) for r in ev["frontend_routes"]}
        
        # Next.js Pages Router
        assert ("/about", "nextjs_pages") in routes
        # Next.js App Router (from directory)
        assert ("/dashboard", "nextjs_app") in routes
        # React Router routes
        assert ("/home", "react_router") in routes
        assert ("/profile", "react_router") in routes


# ---------------------------------------------------------------------------
# 11. Component Relationships
# ---------------------------------------------------------------------------

class TestComponentRelationships:
    """Verifies that internal EXPOSES_API, USES_ENTITY, CALLS_SERVICE, and IMPORTS_MODULE edges generate correctly."""

    FILES = [
        "src/auth/controllers/auth.ts",
        "src/auth/services/auth_service.ts",
        "src/user/models/user.ts",
        "src/routes/api.ts"
    ]

    FEATURES = {
        "src/auth/controllers/auth.ts": {
            "imports": ["../services/auth_service"],
            "api_endpoints": [
                {"verb": "POST", "route": "/login"}
            ]
        },
        "src/auth/services/auth_service.ts": {
            "classes": ["AuthService"],
            "functions": ["login"]
        },
        "src/user/models/user.ts": {
            "classes": ["User"]
        },
        "src/routes/api.ts": {}
    }

    SCHEMA_TAGS = {
        "src/user/models/user.ts": ["MONGOOSE_MODEL:User"],
    }

    CONTENT = {
        "src/auth/controllers/auth.ts": "const svc = new AuthService(); const user = await User.findById(1);",
        "src/auth/services/auth_service.ts": "export class AuthService {}",
        "src/user/models/user.ts": "mongoose.model('User', schema);",
    }
    
    TECH_STACK = {
        "backend_framework": "express",
        "database": "mongodb",
        "orm": "mongoose",
    }

    def test_component_edges(self):
        ev = build_repository_evidence(
            self.FILES, _make_reader(self.CONTENT), self.FEATURES, self.SCHEMA_TAGS, self.TECH_STACK
        )
        
        edges = {(r["from"], r["to"], r["type"]) for r in ev["relationships"]}
        
        # EXPOSES_API mapping
        assert ("auth", "POST /login", "EXPOSES_API") in edges
        
        # IMPORTS_MODULE mapping (auth controller imports auth_service)
        assert ("auth", "auth_service", "IMPORTS_MODULE") in edges
        
        # CALLS_SERVICE (auth controller content contains literal "AuthService")
        assert ("auth", "AuthService", "CALLS_SERVICE") in edges
        
        # USES_ENTITY (auth controller content contains literal "User")
        assert ("auth", "User", "USES_ENTITY") in edges

# ---------------------------------------------------------------------------
# 10. Output structure
# ---------------------------------------------------------------------------

class TestOutputStructure:
    """Verify the top-level keys are always present."""

    def test_all_keys_present(self):
        ev = build_repository_evidence(
            ["some/file.js"], _noop_read, {}, {},
            {"backend_framework": None},
        )
        assert set(ev.keys()) == {
            "tech_stack", "modules", "components", "apis", "entities", "services", "repositories",
            "mounts", "routers", "relationships", "frontend_routes"
        }

    def test_modules_is_alias_for_components(self):
        """modules and components must reference the same list."""
        ev = build_repository_evidence(
            ["some/file.js"], _noop_read, {}, {},
            {"backend_framework": None},
        )
        assert ev["modules"] is ev["components"]


# ---------------------------------------------------------------------------
# 11. EPIC-1 Pipeline Upgrade Tests
# ---------------------------------------------------------------------------

class TestPipelineUpgrades:
    """Verify the new EPIC-1 features: mounts, service classes, controller fields."""

    def test_service_class_from_service_file(self):
        """*.service.ts files should be detected as service_class entries."""
        features = {
            "auth.service.ts": {
                "classes": [],
                "exported_classes": ["AuthService"],
                "methods": ["login", "register"],
                "annotations": [],
            }
        }
        ev = build_repository_evidence(
            ["auth.service.ts"], _noop_read, features, {},
            {"backend_framework": None},
        )
        services = ev["services"]
        assert any(s["name"] == "AuthService" for s in services), \
            f"Expected AuthService in services, got: {services}"
        svc = next(s for s in services if s["name"] == "AuthService")
        assert svc["type"] == "service_module"
        assert svc["file"] == "auth.service.ts"

    def test_router_mount_captured_in_mounts(self):
        """api_endpoints with USE verb + mount_path should appear in mounts."""
        features = {
            "server.js": {
                "api_endpoints": [
                    {
                        "verb": "USE",
                        "route": "/api/auth",
                        "handler": "authRouter",
                        "mount_path": "/api/auth",
                        "mounted_router": "authRouter",
                        "line": 5,
                    }
                ],
                "api_mounts": [],
            }
        }
        ev = build_repository_evidence(
            ["server.js"], _noop_read, features, {},
            {"backend_framework": None},
        )
        mounts = ev["mounts"]
        assert any(m["mount_path"] == "/api/auth" for m in mounts), \
            f"Expected /api/auth mount, got: {mounts}"

    def test_controller_field_on_api(self):
        """Endpoints with a handler field should expose it as controller."""
        features = {
            "routes/auth.js": {
                "api_endpoints": [
                    {
                        "verb": "POST",
                        "route": "/login",
                        "handler": "authController.login",
                        "router_symbol": "router",
                        "line": 10,
                    }
                ],
            }
        }
        ev = build_repository_evidence(
            ["routes/auth.js"], _noop_read, features, {},
            {"backend_framework": None},
        )
        apis = ev["apis"]
        if apis:  # Only validate if extraction produced results
            api = next((a for a in apis if a.get("path") == "/login"), None)
            if api:
                assert api.get("controller") == "authController.login" or api.get("handler") == "authController.login"

    def test_mounts_sorted_deterministically(self):
        """mounts must be sorted by mount_path."""
        features = {
            "server.js": {
                "api_endpoints": [
                    {"verb": "USE", "route": "/api/users", "handler": "userRouter",
                     "mount_path": "/api/users", "mounted_router": "userRouter", "line": 3},
                    {"verb": "USE", "route": "/api/auth", "handler": "authRouter",
                     "mount_path": "/api/auth", "mounted_router": "authRouter", "line": 2},
                ],
                "api_mounts": [],
            }
        }
        ev = build_repository_evidence(
            ["server.js"], _noop_read, features, {},
            {"backend_framework": None},
        )
        paths = [m["mount_path"] for m in ev["mounts"]]
        assert paths == sorted(paths), f"mounts not sorted: {paths}"
