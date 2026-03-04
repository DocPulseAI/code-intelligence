from main import _build_api_contract_endpoints


def test_route_level_auth_detection_ignores_global_middlewares():
    content = """
const express = require('express');
const app = express();
const router = express.Router();
app.use(json());
app.use(corsMiddleware);
app.use('/api', router);
router.get('/address/:id', protect, controller.getAddress);
"""
    candidates = [
        {
            "method": "GET",
            "path": "/api/address/:id",
            "source_file": "routes.js",
            "line_start": 8,
            "line_end": 8,
            "content": content,
            "warnings": [],
        }
    ]
    endpoints, _ = _build_api_contract_endpoints(candidates)
    ep = endpoints[0]
    assert ep["auth"]["required"] is True
    assert ep["auth"]["type"] == "JWT"
    assert ep["auth"]["middleware"] == ["protect"]


def test_semantic_operation_id_and_summary_for_addresses():
    content = """
router.get('/api/address', protect, listAddresses);
router.post('/api/address', protect, createAddress);
router.delete('/api/address/:id', protect, deleteAddress);
router.patch('/api/address/:id/default', protect, setDefaultAddress);
"""
    candidates = [
        {"method": "GET", "path": "/api/address", "source_file": "x.js", "line_start": 2, "line_end": 2, "content": content, "warnings": []},
        {"method": "POST", "path": "/api/address", "source_file": "x.js", "line_start": 3, "line_end": 3, "content": content, "warnings": []},
        {"method": "DELETE", "path": "/api/address/:id", "source_file": "x.js", "line_start": 4, "line_end": 4, "content": content, "warnings": []},
        {"method": "PATCH", "path": "/api/address/:id/default", "source_file": "x.js", "line_start": 5, "line_end": 5, "content": content, "warnings": []},
    ]
    endpoints, _ = _build_api_contract_endpoints(candidates)
    by_key = {f"{e['method']} {e['path']}": e for e in endpoints}
    assert by_key["GET /api/address"]["operation_id"] == "getAddresses"
    assert by_key["POST /api/address"]["operation_id"] == "createAddress"
    assert by_key["DELETE /api/address/{id}"]["operation_id"] == "deleteAddressById"
    assert by_key["PATCH /api/address/{id}/default"]["operation_id"] == "updateAddressDefault"
    assert by_key["DELETE /api/address/{id}"]["summary"] == "Delete Address By Id"
    assert by_key["GET /api/address"]["summary"] == "List Addresses"
    assert by_key["POST /api/address"]["summary"] == "Create Address"
    assert by_key["PATCH /api/address/{id}/default"]["summary"] == "Update Address Default"


def test_auth_rbac_and_jwt_rbac():
    content = """
router.get('/api/users', rbac, usersController.listUsers);
router.get('/api/users/:id', protect, rbac, usersController.getUser);
"""
    candidates = [
        {"method": "GET", "path": "/api/users", "source_file": "y.js", "line_start": 2, "line_end": 2, "content": content, "warnings": []},
        {"method": "GET", "path": "/api/users/:id", "source_file": "y.js", "line_start": 3, "line_end": 3, "content": content, "warnings": []},
    ]
    endpoints, _ = _build_api_contract_endpoints(candidates)
    by_path = {e["path"]: e for e in endpoints}
    assert by_path["/api/users"]["auth"]["type"] == "RBAC"
    assert by_path["/api/users"]["auth"]["required"] is True
    assert by_path["/api/users/{id}"]["auth"]["type"] == "JWT+RBAC"
    assert by_path["/api/users/{id}"]["auth"]["required"] is True


def test_non_auth_middleware_not_classified_as_jwt():
    content = """
router.get('/api/csrf-token', csrfProtection, getCsrfToken);
"""
    candidates = [
        {"method": "GET", "path": "/api/csrf-token", "source_file": "z.js", "line_start": 2, "line_end": 2, "content": content, "warnings": []},
    ]
    endpoints, _ = _build_api_contract_endpoints(candidates)
    ep = endpoints[0]
    assert ep["auth"]["type"] == "Public"
    assert ep["auth"]["required"] is False
    assert ep["auth"]["middleware"] == []
