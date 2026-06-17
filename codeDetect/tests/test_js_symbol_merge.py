from src.parsers.tree_sitter_engine import TreeSitterEngine


class _DummyRoot:
    pass


def test_js_symbol_lists_merge_ast_then_legacy(monkeypatch):
    engine = TreeSitterEngine()

    def fake_traverse(self, root, code, features, route_targets, context=None):
        features["functions"].extend(["ast_fn", "shared_fn"])
        features["classes"].extend(["AstClass", "SharedClass"])
        features["exported_functions"].extend(["ast_exported_fn", "shared_exported_fn"])
        features["exported_classes"].extend(["AstExportedClass", "SharedExportedClass"])

    def fake_routes(self, code):
        return set()

    def fake_legacy(self, code):
        return {
            "functions": ["shared_fn", "legacy_fn"],
            "classes": ["SharedClass", "LegacyClass"],
            "exported_functions": ["shared_exported_fn", "legacy_exported_fn"],
            "exported_classes": ["SharedExportedClass", "LegacyExportedClass"],
        }

    monkeypatch.setattr(TreeSitterEngine, "_traverse_js", fake_traverse)
    monkeypatch.setattr(TreeSitterEngine, "_extract_js_route_targets", fake_routes)
    monkeypatch.setattr(TreeSitterEngine, "_extract_legacy_js_symbols", fake_legacy)

    features = engine._extract_js_features(_DummyRoot(), "const shared_fn = 1;")

    assert features["functions"] == ["ast_fn", "shared_fn", "legacy_fn"]
    assert features["classes"] == ["AstClass", "SharedClass", "LegacyClass"]
    assert features["exported_functions"] == ["ast_exported_fn", "shared_exported_fn", "legacy_exported_fn"]
    assert features["exported_classes"] == ["AstExportedClass", "SharedExportedClass", "LegacyExportedClass"]
