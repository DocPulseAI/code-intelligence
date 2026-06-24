from src.intelligence.call_graph_engine import _resolve_callee

def test_resolve_this_method_basic():
    resolved = _resolve_callee(
        callee_raw="this._getUserTaskStats",
        local_functions={"_getUserTaskStats", "getDashboardOverview"},
        local_classes={"DashboardService"},
        imports=[],
        file_path="backend/src/modules/dashboard/services/dashboard.service.js",
        all_files={"backend/src/modules/dashboard/services/dashboard.service.js"},
        features_map={},
        file_to_mod={"backend/src/modules/dashboard/services/dashboard.service.js": "dashboard"},
        file_to_layer={"backend/src/modules/dashboard/services/dashboard.service.js": "service"},
        current_module="dashboard",
        current_layer="service"
    )
    assert resolved == "dashboard.service._getUserTaskStats"


def test_resolve_self_method_basic():
    resolved = _resolve_callee(
        callee_raw="self.helper_method",
        local_functions={"helper_method", "main_method"},
        local_classes={"HelperClass"},
        imports=[],
        file_path="src/services/helper.py",
        all_files={"src/services/helper.py"},
        features_map={},
        file_to_mod={"src/services/helper.py": "helper_module"},
        file_to_layer={"src/services/helper.py": "service"},
        current_module="helper_module",
        current_layer="service"
    )
    assert resolved == "helper_module.service.helper_method"


def test_resolve_this_method_async_and_underscored():
    resolved = _resolve_callee(
        callee_raw="this._asyncHelper",
        local_functions={"_asyncHelper", "someMethod"},
        local_classes={"MyClass"},
        imports=[],
        file_path="src/controllers/test.controller.js",
        all_files={"src/controllers/test.controller.js"},
        features_map={},
        file_to_mod={"src/controllers/test.controller.js": "test_module"},
        file_to_layer={"src/controllers/test.controller.js": "controller"},
        current_module="test_module",
        current_layer="controller"
    )
    assert resolved == "test_module.controller._asyncHelper"
