"""
Tree-sitter based parsing engine for multi-language AST analysis.
"""

import os
import re
from typing import Dict, List, Optional, Any

# Try to import tree-sitter, fall back to regex if unavailable
try:
    import tree_sitter_languages
    from tree_sitter import Parser, Language, Node
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False
    Language = Any  # type: ignore
    Node = Any  # type: ignore
    print("Warning: tree-sitter not installed. Falling back to regex parsing.")


class TreeSitterEngine:
    """
    Tree-sitter based AST parsing engine.
    Provides language-agnostic parsing with syntax tolerance.
    """

    # Language mapping: file extension -> tree-sitter language name
    LANGUAGE_MAP = {
        '.java': 'java',
        '.js': 'javascript',
        '.jsx': 'javascript',
        '.ts': 'typescript',
        '.tsx': 'tsx',
        '.py': 'python',
        '.go': 'go',
        '.rs': 'rust',
        '.rb': 'ruby',
        '.c': 'c',
        '.cpp': 'cpp',
        '.cs': 'c_sharp',
        '.php': 'php',
    }
    RX_JS_FUNCTION = re.compile(r'\bfunction\s+([A-Za-z_]\w*)\s*\(', re.MULTILINE)
    RX_JS_ARROW_FUNCTION = re.compile(
        r'\b(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?\([^)]*\)\s*(?::\s*[^=]+)?\s*=>',
        re.MULTILINE
    )
    RX_JS_CLASS = re.compile(r'\bclass\s+([A-Za-z_]\w*)\b', re.MULTILINE)
    RX_JS_EXPORT_FUNCTION = re.compile(r'\bexport\s+(?:default\s+)?function\s+([A-Za-z_]\w*)\s*\(', re.MULTILINE)
    RX_JS_EXPORT_ARROW = re.compile(
        r'\bexport\s+(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?\([^)]*\)\s*(?::\s*[^=]+)?\s*=>',
        re.MULTILINE
    )
    RX_JS_EXPORT_CLASS = re.compile(r'\bexport\s+(?:default\s+)?class\s+([A-Za-z_]\w*)\b', re.MULTILINE)

    def __init__(self):
        self.parser = Parser() if TREE_SITTER_AVAILABLE else None
        self._languages_cache: Dict[str, Language] = {}

    def _get_language(self, extension: str) -> Optional[Language]:
        """Get tree-sitter language for file extension."""
        if not TREE_SITTER_AVAILABLE:
            return None

        lang_name = self.LANGUAGE_MAP.get(extension)
        if not lang_name:
            return None

        if lang_name not in self._languages_cache:
            try:
                self._languages_cache[lang_name] = tree_sitter_languages.get_language(lang_name)
            except Exception as e:
                print(f"Warning: Could not load language {lang_name}: {e}")
                return None

        return self._languages_cache[lang_name]

    def parse(self, code: str, extension: str) -> Dict[str, Any]:
        """
        Parse source code and extract features.

        Args:
            code: Source code string
            extension: File extension (e.g., '.java')

        Returns:
            Dictionary containing extracted features and metadata
        """
        result = {
            "syntax_error": False,
            "error_nodes": [],
            "features": {},
            "ast_available": TREE_SITTER_AVAILABLE
        }

        if not TREE_SITTER_AVAILABLE or not code:
            return result

        language = self._get_language(extension)
        if not language:
            return result

        try:
            self.parser.set_language(language)
            tree = self.parser.parse(bytes(code, 'utf-8'))

            # Check for errors but continue
            if tree.root_node.has_error:
                result["syntax_error"] = True
                result["error_nodes"] = self._collect_error_nodes(tree.root_node)
                print(f"Warning: Syntax errors detected. Partial analysis will be performed.")

            # Extract features based on language
            lang_name = self.LANGUAGE_MAP.get(extension, '')

            if lang_name == 'java':
                result["features"] = self._extract_java_features(tree.root_node, code)
            elif lang_name in ['javascript', 'typescript', 'tsx']:
                result["features"] = self._extract_js_features(tree.root_node, code)
            elif lang_name == 'python':
                result["features"] = self._extract_python_features(tree.root_node, code)
            else:
                result["features"] = self._extract_generic_features(tree.root_node, code)

            # Backward-compatible JS/TS syntax behavior.
            if not result["syntax_error"] and lang_name in ['javascript', 'typescript', 'tsx']:
                if self._legacy_js_syntax_error(code):
                    result["syntax_error"] = True

        except Exception as e:
            print(f"Parsing error: {e}")
            result["syntax_error"] = True

        return result

    def _legacy_js_syntax_error(self, content: str) -> bool:
        """Heuristic JS/TS syntax checks used by legacy parser flow."""
        return (
            not self._check_balanced(content, '{', '}') or
            not self._check_balanced(content, '[', ']') or
            not self._check_balanced(content, '(', ')') or
            (content.count('`') % 2 != 0)
        )

    def _check_balanced(self, content: str, open_char: str, close_char: str) -> bool:
        """Check if characters are balanced, accounting for strings/comments."""
        cleaned = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', '""', content)
        cleaned = re.sub(r"'[^'\\]*(?:\\.[^'\\]*)*'", "''", cleaned)
        cleaned = re.sub(r'`[^`\\]*(?:\\.[^`\\]*)*`', '``', cleaned)
        cleaned = re.sub(r'//.*$', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'/\*.*?\*/', '', cleaned, flags=re.DOTALL)
        cleaned = re.sub(r'#.*$', '', cleaned, flags=re.MULTILINE)

        count = 0
        for char in cleaned:
            if char == open_char:
                count += 1
            elif char == close_char:
                count -= 1
            if count < 0:
                return False
        return count == 0

    def _collect_error_nodes(self, node: 'Node', errors: List = None) -> List[Dict]:
        """Collect all ERROR nodes in the tree."""
        if errors is None:
            errors = []

        if node.type == 'ERROR' or node.is_missing:
            errors.append({
                "type": "ERROR" if node.type == 'ERROR' else "MISSING",
                "start_line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1,
                "start_col": node.start_point[1],
                "end_col": node.end_point[1]
            })

        for child in node.children:
            self._collect_error_nodes(child, errors)

        return errors

    def _get_node_text(self, node: 'Node', code: str) -> str:
        """Extract text for a node."""
        return code[node.start_byte:node.end_byte]

    def _extract_java_features(self, root: 'Node', code: str) -> Dict[str, Any]:
        """Extract Java classes, methods, and Spring annotations."""
        features: Dict[str, Any] = {
            "classes": [],
            "methods": [],
            "constructors": [],
            "annotations": [],
            "api_endpoints": [],
            "schema_annotations": [],
            "imports": [],
            "comments": [],
            "complexity_nodes": 0,
            "calls": []
        }

        self._traverse_java(root, code, features)
        return features

    def _traverse_java(self, node: 'Node', code: str, features: Dict, context: Dict = None):
        """Recursively traverse Java AST.
        
        Args:
            context: Carries down class-level state (e.g. base path, class name, current function)
        """
        if context is None:
            context = {"class_name": "", "base_path": "", "current_function": ""}
            
        # Class declarations
        if node.type == 'class_declaration':
            name_node = node.child_by_field_name('name')
            class_name = ""
            base_path = ""
            if name_node:
                class_name = self._get_node_text(name_node, code)
                features["classes"].append(class_name)
                
            # Look for class-level RequestMapping
            modifiers = node.child_by_field_name('modifiers')
            if modifiers:
                for mod in modifiers.children:
                    if mod.type == 'annotation' and mod.child_by_field_name('name'):
                        ann_name = self._get_node_text(mod.child_by_field_name('name'), code)
                        if ann_name == 'RequestMapping':
                            base_path = self._extract_annotation_value(mod, code)
            
            # Update context for children
            new_context = {"class_name": class_name, "base_path": base_path}
            for child in node.children:
                self._traverse_java(child, code, features, new_context)
            return

        # Method declarations
            name_node = node.child_by_field_name('name')
            if name_node:
                features["classes"].append(self._get_node_text(name_node, code))

        # Method declarations
        elif node.type == 'method_declaration':
            name_node = node.child_by_field_name('name')
            if name_node:
                method_name = self._get_node_text(name_node, code)
                features["methods"].append(method_name)
                # Update context for body traversal
                context = context.copy()
                context["current_function"] = method_name

        # Constructor declarations
        elif node.type == 'constructor_declaration':
            name_node = node.child_by_field_name('name')
            if name_node:
                name = self._get_node_text(name_node, code)
                features["constructors"].append(name)
                context = context.copy()
                context["current_function"] = name

        # Annotations (Spring)
        elif node.type in ['marker_annotation', 'annotation']:
            name_node = node.child_by_field_name('name')
            if name_node:
                ann_name = self._get_node_text(name_node, code)
                features["annotations"].append(f"@{ann_name}")

                # Check for API annotations
                if ann_name in ['GetMapping', 'PostMapping', 'PutMapping',
                               'DeleteMapping', 'PatchMapping', 'RequestMapping']:
                    
                    method_route = self._extract_annotation_value(node, code)
                    base_path = context.get("base_path", "")
                    
                    # Combine base path and method path
                    route = method_route
                    if base_path:
                        normalized_base = base_path.rstrip('/')
                        normalized_method = method_route if method_route.startswith('/') else f"/{method_route}" if method_route else ""
                        route = f"{normalized_base}{normalized_method}"
                        
                    # Find method name for handler
                    method_name = ""
                    method_decl = node.parent.parent if node.parent and node.parent.parent and node.parent.parent.type == 'method_declaration' else None
                    if method_decl:
                        name_node = method_decl.child_by_field_name('name')
                        if name_node:
                            method_name = self._get_node_text(name_node, code)
                            
                    class_name = context.get("class_name", "")
                    handler = f"{class_name}.{method_name}" if class_name and method_name else method_name

                    features["api_endpoints"].append({
                        "verb": ann_name.replace('Mapping', '').upper() or 'REQUEST',
                        "route": route,
                        "line": node.start_point[0] + 1,
                        "handler": handler,
                        "router_symbol": class_name,
                    })

                # Check for schema annotations
                if ann_name in ['Entity', 'Table', 'Column', 'Id',
                               'OneToMany', 'ManyToOne', 'ManyToMany']:
                    features["schema_annotations"].append(ann_name)

        # Import declarations
        elif node.type == 'import_declaration':
            features["imports"].append(self._get_node_text(node, code).strip())

        # Call expressions
        elif node.type == 'method_invocation':
            if context.get("current_function"):
                name_node = node.child_by_field_name('name')
                obj_node = node.child_by_field_name('object')
                callee = ""
                if obj_node:
                    callee = f"{self._get_node_text(obj_node, code)}.{self._get_node_text(name_node, code)}"
                else:
                    callee = self._get_node_text(name_node, code)
                
                features["calls"].append({
                    "caller": context["current_function"],
                    "callee": callee,
                    "line": node.start_point[0] + 1
                })

        # Comments
        elif node.type in ['line_comment', 'block_comment']:
            features["comments"].append(self._get_node_text(node, code).strip())

        # Complexity nodes
        elif node.type in ['if_statement', 'for_statement', 'while_statement',
                          'do_statement', 'switch_expression', 'try_statement',
                          'catch_clause', 'ternary_expression']:
            features["complexity_nodes"] += 1

        # Recurse
        for child in node.children:
            self._traverse_java(child, code, features, context)

    def _extract_annotation_value(self, node: 'Node', code: str) -> str:
        """Extract value from annotation arguments."""
        for child in node.children:
            if child.type == 'annotation_argument_list':
                for arg in child.children:
                    if arg.type == 'string_literal':
                        return self._get_node_text(arg, code).strip('"\'')
                    elif arg.type == 'element_value_pair':
                        for val in arg.children:
                            if val.type == 'string_literal':
                                return self._get_node_text(val, code).strip('"\'')
        return ""

    def _extract_js_features(self, root: 'Node', code: str) -> Dict[str, Any]:
        """Extract JS/TS features in backward-compatible schema."""
        features: Dict[str, Any] = {
            "functions": [],
            "classes": [],
            "exported_functions": [],
            "exported_classes": [],
            "api_endpoints": [],
            "api_mounts": [],
            "mongoose_schemas": [],
            "api_calls": [],
            "react_components": [],
            "jsx_routes": [],
            "calls": [],
        }
        route_targets = self._extract_js_route_targets(code)
        self._traverse_js(root, code, features, route_targets)
        # Preserve legacy symbol extraction behavior/ordering for compatibility.
        legacy_symbols = self._extract_legacy_js_symbols(code)
        features["functions"] = legacy_symbols["functions"]
        features["classes"] = legacy_symbols["classes"]
        features["exported_functions"] = legacy_symbols["exported_functions"]
        features["exported_classes"] = legacy_symbols["exported_classes"]

        features["api_endpoints"] = self._dedupe_endpoint_list(features["api_endpoints"])
        return features

    def _extract_js_route_targets(self, code: str) -> set[str]:
        """Extract deterministic candidate objects that can own route methods."""
        targets = {"app", "router"}
        patterns = [
            r"\b(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*express\.Router\s*\(",
            r"\b(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*Router\s*\(",
            r"\b(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*express\s*\(",
        ]
        for rx in patterns:
            for match in re.finditer(rx, code):
                symbol = match.group(1).strip()
                if symbol:
                    targets.add(symbol)
        return set(sorted(targets))

    def _extract_legacy_js_symbols(self, code: str) -> Dict[str, List[str]]:
        """Compatibility symbol extraction matching previous TS parser behavior."""
        functions = self.RX_JS_FUNCTION.findall(code) + self.RX_JS_ARROW_FUNCTION.findall(code)
        classes = self.RX_JS_CLASS.findall(code)
        exported_functions = self.RX_JS_EXPORT_FUNCTION.findall(code) + self.RX_JS_EXPORT_ARROW.findall(code)
        exported_classes = self.RX_JS_EXPORT_CLASS.findall(code)
        return {
            "functions": self._dedupe_order(functions),
            "classes": self._dedupe_order(classes),
            "exported_functions": self._dedupe_order(exported_functions),
            "exported_classes": self._dedupe_order(exported_classes),
        }

    def _traverse_js(self, node: 'Node', code: str, features: Dict, route_targets: set[str], context: Dict = None):
        """Recursively traverse JS/TS AST.
        
        Args:
            context: Carries down class-level state (e.g., base path, class name for NestJS)
        """
        if context is None:
            context = {"class_name": "", "base_path": "", "current_function": ""}

        if node.type == 'function_declaration':
            name_node = node.child_by_field_name('name')
            if name_node:
                func_name = self._get_node_text(name_node, code)
                features["functions"].append(func_name)
                context = context.copy()
                context["current_function"] = func_name

        elif node.type == 'lexical_declaration':
            for child in node.children:
                if child.type != 'variable_declarator':
                    continue
                value_node = child.child_by_field_name('value')
                if value_node and value_node.type == 'arrow_function':
                    func_name = self._extract_js_variable_name(child, code)
                    if func_name:
                        features["functions"].append(func_name)
                        context = context.copy()
                        context["current_function"] = func_name

        elif node.type == 'class_declaration':
            name_node = node.child_by_field_name('name')
            class_name = ""
            base_path = ""
            if name_node:
                class_name = self._get_node_text(name_node, code)
                features["classes"].append(class_name)

            # Check for NestJS @Controller decorator
            prev_node = node.prev_sibling
            while prev_node and prev_node.type == 'decorator':
                # The node is a call_expression inside the decorator
                call_node = prev_node.child(1) if prev_node.child_count > 1 else None
                if call_node and call_node.type == 'call_expression':
                    func_node = call_node.child_by_field_name('function')
                    if func_node and self._get_node_text(func_node, code) == 'Controller':
                        args_node = call_node.child_by_field_name('arguments')
                        if args_node and args_node.child_count > 2: # '(', arg, ')'
                            first_arg = args_node.children[1]
                            if first_arg.type in ('string', 'string_fragment', 'template_string'):
                                base_path = self._get_node_text(first_arg, code).strip('"`\'')

                prev_node = prev_node.prev_sibling

            new_context = {"class_name": class_name, "base_path": base_path}
            for child in node.children:
                self._traverse_js(child, code, features, route_targets, new_context)
            return

        elif node.type == 'method_definition':
            name_node = node.child_by_field_name('name')
            method_name = ""
            if name_node:
                method_name = self._get_node_text(name_node, code)
                features["functions"].append(method_name) # simplified
                context = context.copy()
                context["current_function"] = method_name
                
            # Check for NestJS @Get, @Post, etc. decorators
            prev_node = node.prev_sibling
            while prev_node and prev_node.type == 'decorator':
                call_node = prev_node.child(1) if prev_node.child_count > 1 else None
                if call_node and call_node.type == 'call_expression':
                    func_node = call_node.child_by_field_name('function')
                    if func_node:
                        func_name = self._get_node_text(func_node, code)
                        if func_name in ('Get', 'Post', 'Put', 'Delete', 'Patch', 'Options', 'Head', 'All'):
                            method_route = ""
                            args_node = call_node.child_by_field_name('arguments')
                            if args_node and args_node.child_count > 2:
                                first_arg = args_node.children[1]
                                if first_arg.type in ('string', 'string_fragment', 'template_string'):
                                    method_route = self._get_node_text(first_arg, code).strip('"`\'')
                            
                            base_path = context.get('base_path', '')
                            route = method_route
                            if base_path:
                                normalized_base = base_path.rstrip('/')
                                normalized_method = method_route if method_route.startswith('/') else f"/{method_route}" if method_route else ""
                                route = f"{normalized_base}{normalized_method}"
                                
                            class_name = context.get('class_name', '')
                            handler = f"{class_name}.{method_name}" if class_name and method_name else method_name
                            
                            features["api_endpoints"].append({
                                "verb": func_name.upper(),
                                "route": route,
                                "line": prev_node.start_point[0] + 1,
                                "handler": handler,
                                "router_symbol": class_name,
                            })
                prev_node = prev_node.prev_sibling

        elif node.type == 'export_statement':
            self._extract_exported_js_declarations(node, code, features)

        elif node.type == 'call_expression':
            endpoint = self._extract_express_route(node, code, route_targets)
            if endpoint:
                features["api_endpoints"].append(endpoint)
            
            # Record general calls
            if context.get("current_function"):
                func_node = node.child_by_field_name('function')
                if func_node:
                    callee = self._get_node_text(func_node, code)
                    # Ignore very short or noise calls if desired, but here we include all
                    features["calls"].append({
                        "caller": context["current_function"],
                        "callee": callee,
                        "line": node.start_point[0] + 1
                    })
            
            # Detect API calls (fetch or axios)
            func_node = node.child_by_field_name('function')
            if func_node:
                func_name = self._get_node_text(func_node, code)
                is_fetch = func_name == 'fetch'
                is_axios = func_name.startswith('axios')
                if is_fetch or is_axios:
                    features["api_calls"].append({
                        "client": "fetch" if is_fetch else "axios",
                        "method": "UNKNOWN",  # Will need deeper inspect if desired, string matching for now
                        "line": node.start_point[0] + 1
                    })

        elif node.type == 'jsx_element' or node.type == 'jsx_self_closing_element':
            # Basic React Component Route detection
            open_tag = node.child(0) if node.type == 'jsx_element' else node
            if open_tag:
                tag_name_node = open_tag.child(1) # '<', 'tag_name'
                if tag_name_node:
                    tag_name = self._get_node_text(tag_name_node, code)
                    # React Router <Route>
                    if tag_name in ('Route', 'RouterProvider'):
                        path = ""
                        element = ""
                        for child in open_tag.children:
                            if child.type == 'jsx_attribute':
                                attr_name_node = child.child_by_field_name('name')
                                if attr_name_node:
                                    attr_name = self._get_node_text(attr_name_node, code)
                                    attr_val = child.child(2) if child.child_count > 2 else None
                                    if attr_val:
                                        val_text = self._get_node_text(attr_val, code).strip('"\'`{}')
                                        if attr_name == 'path':
                                            path = val_text
                                        elif attr_name in ('element', 'component'):
                                            # Strip out generic component wrappers like <MyComponent /> -> MyComponent
                                            comp_match = re.search(r'<([A-Z][a-zA-Z0-9_]*)', self._get_node_text(attr_val, code))
                                            if comp_match:
                                                element = comp_match.group(1)
                                            else:
                                                element = val_text
                        if path or element:
                            features["jsx_routes"].append({
                                "path": path,
                                "component": element,
                                "line": node.start_point[0] + 1
                            })
            
            # Fallback regex for extremely nested or custom AST parsed Route components
            if node.type == 'jsx_element':
                raw_jsx = self._get_node_text(node, code)
                if '<Route ' in raw_jsx:
                    # Regex to grab path="..." and element={...} or component={...}
                    # Also supports index={true} since path might not be exactly next to Route
                    for r_match in re.finditer(r'<Route\b[^>]*?path=[\'"]([^\'"]+)[\'"][^>]*(?:element|component)=\{?\s*<?([A-Z][a-zA-Z0-9_]*)', raw_jsx):
                        features["jsx_routes"].append({
                            "path": r_match.group(1),
                            "component": r_match.group(2),
                            "line": node.start_point[0] + 1
                        })

            # Check if this tree is returning JSX (making the parent a React component)
            features["react_components"].append("REACT_COMPONENT")
            
        for child in node.children:
            self._traverse_js(child, code, features, route_targets, context)

    def _extract_exported_js_declarations(self, export_node: 'Node', code: str, features: Dict):
        """Extract exported function/class names from export statements."""
        for child in export_node.children:
            if child.type == 'function_declaration':
                name_node = child.child_by_field_name('name')
                if name_node:
                    features["exported_functions"].append(self._get_node_text(name_node, code))
            elif child.type == 'class_declaration':
                name_node = child.child_by_field_name('name')
                if name_node:
                    features["exported_classes"].append(self._get_node_text(name_node, code))
            elif child.type == 'lexical_declaration':
                for decl in child.children:
                    if decl.type != 'variable_declarator':
                        continue
                    value_node = decl.child_by_field_name('value')
                    if value_node and value_node.type == 'arrow_function':
                        func_name = self._extract_js_variable_name(decl, code)
                        if func_name:
                            features["exported_functions"].append(func_name)

    def _extract_express_route(self, call_node: 'Node', code: str, route_targets: set[str]) -> Optional[Dict[str, Any]]:
        """Extract Express-style app/router endpoint calls and mount declarations."""
        func_node = call_node.child_by_field_name('function')
        args_node = call_node.child_by_field_name('arguments')
        if not func_node or not args_node or func_node.type != 'member_expression':
            return None

        object_node = func_node.child_by_field_name('object')
        property_node = func_node.child_by_field_name('property')
        if not object_node or not property_node:
            return None

        target = self._get_node_text(object_node, code)
        verb = self._get_node_text(property_node, code).lower()

        # Capture router mounts: app.use('/prefix', routerVar)
        if target in route_targets and verb == 'use':
            return self._extract_express_mount(call_node, args_node, code, target)

        if target not in route_targets or verb not in {'get', 'post', 'put', 'delete', 'patch'}:
            return None

        route = ""
        for arg in args_node.children:
            if arg.type in {'string', 'string_fragment'}:
                route = self._get_node_text(arg, code).strip('"\'')
                break

        # Collect all meaningful (non-string, non-punctuation) args
        skip_types = {'string', 'string_fragment', ',', '(', ')'}
        meaningful_args = []
        for arg in args_node.children:
            if arg.type not in skip_types and self._get_node_text(arg, code).strip():
                meaningful_args.append(self._get_node_text(arg, code).strip())

        # Last meaningful arg is the handler; everything before it is middleware
        handler = meaningful_args[-1] if meaningful_args else ""
        middleware = meaningful_args[:-1] if len(meaningful_args) > 1 else []

        return {
            "verb": verb.upper(),
            "route": route,
            "line": call_node.start_point[0] + 1,
            "handler": handler,
            "router_symbol": target,
            "middleware": middleware,
        }

    def _extract_express_mount(self, call_node: 'Node', args_node: 'Node', code: str, target: str) -> Optional[Dict[str, Any]]:
        """Extract app.use('/prefix', routerVar) as a mount record (returned as a USE verb endpoint)."""
        mount_path = ""
        router_var = ""
        arg_children = [a for a in args_node.children if a.type not in {',', '(', ')'}]
        for i, arg in enumerate(arg_children):
            if i == 0 and arg.type in {'string', 'string_fragment'}:
                mount_path = self._get_node_text(arg, code).strip('"\'')
            elif i == 1:
                router_var = self._get_node_text(arg, code).strip()
        if not mount_path and not router_var:
            return None
        # Return as a USE endpoint so existing mount resolution pipeline handles it.
        return {
            "verb": "USE",
            "route": mount_path,
            "handler": router_var,
            "router_symbol": target,
            "line": call_node.start_point[0] + 1,
            # Extra field for explicit mount surfacing
            "mount_path": mount_path,
            "mounted_router": router_var,
        }

    def _extract_mongoose_schema(self, new_node: 'Node', code: str, features: Dict):
        """Extract Mongoose Schema relationships via AST."""
        # Find constructor name
        constructor_node = new_node.child_by_field_name('constructor')
        is_schema = False
        if constructor_node:
            text = self._get_node_text(constructor_node, code)
            if text in ('mongoose.Schema', 'Schema'):
                is_schema = True
        
        if not is_schema:
            return

        args_node = new_node.child_by_field_name('arguments')
        if not args_node:
            return

        schema_fields = {}
        for arg in args_node.children:
            if arg.type == 'object':
                # Traverse the top-level schema definition fields
                for pair in arg.children:
                    if pair.type == 'pair':
                        key_node = pair.child_by_field_name('key')
                        val_node = pair.child_by_field_name('value')
                        if not key_node or not val_node:
                            continue
                            
                        field_name = self._get_node_text(key_node, code).strip('\'"')
                        
                        # The value could be an object like { type: ..., ref: 'Model' }
                        if val_node.type == 'object':
                            for prop in val_node.children:
                                if prop.type == 'pair':
                                    p_key = prop.child_by_field_name('key')
                                    p_val = prop.child_by_field_name('value')
                                    if p_key and p_val and self._get_node_text(p_key, code) == 'ref':
                                        ref_model = self._get_node_text(p_val, code).strip('\'"')
                                        schema_fields[field_name] = ref_model
                                        break
                                        
                        # Schema could also be an array of references: [{ type: ..., ref: 'Model' }]
                        elif val_node.type == 'array':
                            for item in val_node.children:
                                if item.type == 'object':
                                    for prop in item.children:
                                        if prop.type == 'pair':
                                            p_key = prop.child_by_field_name('key')
                                            p_val = prop.child_by_field_name('value')
                                            if p_key and p_val and self._get_node_text(p_key, code) == 'ref':
                                                ref_model = self._get_node_text(p_val, code).strip('\'"')
                                                schema_fields[field_name] = ref_model
                                                break
                break # Only process the first argument (the main schema block)
                
        # To determine the model name, look at what this new Schema is assigned to, e.g. mongoose.model('User', schema)
        # We'll just pass out the schema fields and let repository_evidence match it up to the file's model 
        if schema_fields:
            features["mongoose_schemas"].append(schema_fields)

    def _extract_js_variable_name(self, declarator_node: 'Node', code: str) -> str:
        """Extract variable name robustly from JS variable_declarator."""
        name_node = declarator_node.child_by_field_name('name')
        if name_node:
            name_text = self._get_node_text(name_node, code).strip()
            if re.match(r'^[A-Za-z_]\w*$', name_text):
                return name_text

        decl_text = self._get_node_text(declarator_node, code)
        match = re.match(r'\s*([A-Za-z_]\w*)\s*=', decl_text)
        return match.group(1) if match else ""

    @staticmethod
    def _dedupe_order(items: List[str]) -> List[str]:
        seen = set()
        result: List[str] = []
        for item in items:
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result

    @staticmethod
    def _dedupe_endpoint_list(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        result: List[Dict[str, Any]] = []
        for item in items:
            key = (item.get("verb"), item.get("route"), item.get("line"))
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result

    def _extract_python_features(self, root: 'Node', code: str) -> Dict[str, Any]:
        """Extract Python functions, classes, and decorators."""
        features: Dict[str, Any] = {
            "functions": [],
            "classes": [],
            "decorators": [],
            "imports": [],
            "api_routes": [],
            "api_endpoints": [],
            "docstrings": [],
            "comments": [],
            "complexity_nodes": 0,
            "calls": []
        }

        self._traverse_python(root, code, features)
        return features

    def _traverse_python(self, node: 'Node', code: str, features: Dict, context: Dict = None):
        """Recursively traverse Python AST."""
        if context is None:
            context = {"current_function": ""}

        # Handle router prefix mounts: app.include_router(..., prefix=...) or app.register_blueprint(..., url_prefix=...)
        if node.type == 'call':
            func_node = node.child_by_field_name('function')
            if func_node:
                func_text = self._get_node_text(func_node, code)
                if func_text.endswith('.include_router') or func_text.endswith('.register_blueprint'):
                    args_node = node.child_by_field_name('arguments')
                    if args_node:
                        # Extract the router/blueprint name
                        router_name = ""
                        if len(args_node.children) > 1 and args_node.children[1].type == 'identifier':
                            router_name = self._get_node_text(args_node.children[1], code)
                        
                        # Find prefix argument
                        prefix = ""
                        for child in args_node.children:
                            if child.type == 'keyword_argument':
                                name_node = child.child_by_field_name('name')
                                value_node = child.child_by_field_name('value')
                                if name_node and value_node and self._get_node_text(name_node, code) in ('prefix', 'url_prefix'):
                                    if value_node.type == 'string':
                                        prefix = self._get_node_text(value_node, code).strip('"\'')
                        
                        if router_name and prefix:
                            # We record this as a "USE" mount point to simulate Express route_resolution_engine mounting
                            features["api_endpoints"].append({
                                "verb": "USE",
                                "route": prefix,
                                "handler": router_name,  # The module/router being mounted
                                "router_symbol": func_text.split('.')[0] if '.' in func_text else "app",
                                "line": node.start_point[0] + 1
                            })
                            
        # Handle router initialization with prefix: router = APIRouter(prefix=...)
        elif node.type == 'assignment':
            left = node.child_by_field_name('left')
            right = node.child_by_field_name('right')
            if left and right and left.type == 'identifier' and right.type == 'call':
                func_node = right.child_by_field_name('function')
                if func_node and self._get_node_text(func_node, code) in ('APIRouter', 'Blueprint'):
                    router_name = self._get_node_text(left, code)
                    args_node = right.child_by_field_name('arguments')
                    if args_node:
                        for child in args_node.children:
                            if child.type == 'keyword_argument':
                                name_node = child.child_by_field_name('name')
                                value_node = child.child_by_field_name('value')
                                if name_node and value_node and self._get_node_text(name_node, code) in ('prefix', 'url_prefix'):
                                    if value_node.type == 'string':
                                        prefix = self._get_node_text(value_node, code).strip('"\'')
                                        # Record the self-prefix
                                        if 'router_prefixes' not in features:
                                            features['router_prefixes'] = {}
                                        features['router_prefixes'][router_name] = prefix

        # Function definitions
        elif node.type == 'function_definition':
            name_node = node.child_by_field_name('name')
            func_name = ""
            if name_node:
                func_name = self._get_node_text(name_node, code)
                features["functions"].append(func_name)
                context = context.copy()
                context["current_function"] = func_name

            # Check if this function has decorators that define routes
            has_route = False
            prev_node = node.prev_sibling
            while prev_node and prev_node.type == 'decorator':
                dec_text = self._get_node_text(prev_node, code)
                features["decorators"].append(dec_text)

                # Check for API routes
                if any(
                    pattern in dec_text
                    for pattern in [
                        '@app.route',
                        '@app.get',
                        '@app.post',
                        '@app.put',
                        '@app.delete',
                        '@app.patch',
                        '@app.options',
                        '@app.head',
                        '@router.',
                        '@auth_bp.',
                        '@api.',
                    ]
                ):
                    has_route = True
                    route = self._extract_decorator_route(dec_text)
                    method = self._extract_decorator_method(dec_text) # New helper to extract methods=["POST"] or @router.post
                    
                    # Extract router_symbol (e.g. app from @app.route)
                    router_symbol = "app"
                    call_node = prev_node.child(1) # The call/attribute inside the decorator
                    if call_node:
                        if call_node.type == 'call':
                            func_attr = call_node.child_by_field_name('function')
                            if func_attr and func_attr.type == 'attribute':
                                obj_node = func_attr.child_by_field_name('object')
                                if obj_node:
                                    router_symbol = self._get_node_text(obj_node, code)
                        elif call_node.type == 'attribute':
                            obj_node = call_node.child_by_field_name('object')
                            if obj_node:
                                router_symbol = self._get_node_text(obj_node, code)

                    features["api_endpoints"].append({
                        "verb": method,
                        "route": route,
                        "line": prev_node.start_point[0] + 1,
                        "handler": func_name,
                        "router_symbol": router_symbol,
                        "decorator": dec_text  # Keep for backward compatibility
                    })
                    
                prev_node = prev_node.prev_sibling

            # Extract docstring
            body = node.child_by_field_name('body')
            if body and body.children:
                first_stmt = body.children[0]
                if first_stmt.type == 'expression_statement':
                    for child in first_stmt.children:
                        if child.type == 'string':
                            features["docstrings"].append({
                                "type": "function",
                                "text": self._get_node_text(child, code)[:200]
                            })

        # Class definitions
        elif node.type == 'class_definition':
            name_node = node.child_by_field_name('name')
            if name_node:
                features["classes"].append(self._get_node_text(name_node, code))

        # Import statements
        elif node.type in ['import_statement', 'import_from_statement']:
            features["imports"].append(self._get_node_text(node, code).strip())

        # Comments
        elif node.type == 'comment':
            features["comments"].append(self._get_node_text(node, code).strip())

        # Complexity nodes
        elif node.type in ['if_statement', 'elif_clause', 'for_statement',
                          'while_statement', 'try_statement', 'except_clause',
                          'with_statement', 'conditional_expression',
                          'list_comprehension', 'dictionary_comprehension']:
            features["complexity_nodes"] += 1

        # Call expressions
        elif node.type == 'call':
            if context.get("current_function"):
                func_node = node.child_by_field_name('function')
                if func_node:
                    callee = self._get_node_text(func_node, code)
                    features["calls"].append({
                        "caller": context["current_function"],
                        "callee": callee,
                        "line": node.start_point[0] + 1
                    })

        # Recurse
        for child in node.children:
            self._traverse_python(child, code, features, context)

    def _extract_decorator_method(self, decorator: str) -> str:
        """Extract HTTP method from decorator (e.g. methods=['POST'] or @router.post)."""
        import re
        decorator_lower = decorator.lower()
        if 'methods=' in decorator_lower or 'methods =' in decorator_lower:
            match = re.search(r'methods\s*=\s*\[?["\']([A-Z]+)["\']\]?', decorator, re.IGNORECASE)
            if match:
                return match.group(1).upper()
        
        # Check for fastapi-style explicit method decorators
        for method in ['get', 'post', 'put', 'delete', 'patch']:
            if f".{method}(" in decorator_lower:
                return method.upper()
                
        return "GET" # default

    def _extract_decorator_route(self, decorator: str) -> str:
        """Extract route path from decorator."""
        import re
        match = re.search(r'["\']([^"\']+)["\']', decorator)
        return match.group(1) if match else ""

    def _extract_generic_features(self, root: 'Node', code: str) -> Dict[str, Any]:
        """Extract generic features for unsupported languages."""
        return {
            "node_count": self._count_nodes(root),
            "depth": self._tree_depth(root)
        }

    def _count_nodes(self, node: 'Node') -> int:
        """Count total nodes in tree."""
        count = 1
        for child in node.children:
            count += self._count_nodes(child)
        return count

    def _tree_depth(self, node: 'Node', depth: int = 0) -> int:
        """Calculate maximum tree depth."""
        max_depth = depth
        for child in node.children:
            child_depth = self._tree_depth(child, depth + 1)
            max_depth = max(max_depth, child_depth)
        return max_depth


# Singleton instance for easy access
engine = TreeSitterEngine()


def parse_code(code: str, extension: str) -> Dict[str, Any]:
    """
    Convenience function for parsing code.

    Args:
        code: Source code string
        extension: File extension (e.g., '.java')

    Returns:
        Parsed features dictionary
    """
    return engine.parse(code, extension)
