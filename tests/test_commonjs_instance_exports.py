from src.parsers.tree_sitter_engine import TreeSitterEngine


class FakeNode:
    def __init__(self, node_type, code, start, end, children=None, fields=None):
        self.type = node_type
        self._code = code
        self.start_byte = start
        self.end_byte = end
        self.children = children or []
        self._fields = fields or {}

    def child(self, index):
        return self.children[index]

    def child_by_field_name(self, name):
        return self._fields.get(name)


def _node(code, text, node_type="identifier"):
    start = code.index(text)
    end = start + len(text)
    return FakeNode(node_type, code, start, end)


def _build_export_statement(code, right_node):
    left_node = _node(code, "module.exports", "member_expression")
    assignment = FakeNode(
        "assignment_expression",
        code,
        0,
        len(code),
        children=[left_node, right_node],
        fields={"left": left_node, "right": right_node},
    )
    return FakeNode("expression_statement", code, 0, len(code), children=[assignment])


def test_commonjs_instance_export_becomes_default_export():
    engine = TreeSitterEngine()
    code = "module.exports = new UserService();"
    constructor_node = _node(code, "UserService")
    new_expression = FakeNode(
        "new_expression",
        code,
        17,
        len(code),
        children=[constructor_node],
        fields={"constructor": constructor_node},
    )

    features = {"exports": []}
    engine._extract_js_imports_exports(_build_export_statement(code, new_expression), code, features)

    assert features["exports"] == [{"exported": "default", "local": "UserService"}]


def test_commonjs_identifier_export_still_passes_through():
    engine = TreeSitterEngine()
    code = "module.exports = UserService;"
    right_node = _node(code, "UserService")

    features = {"exports": []}
    engine._extract_js_imports_exports(_build_export_statement(code, right_node), code, features)

    assert features["exports"] == [{"exported": "default", "local": "UserService"}]