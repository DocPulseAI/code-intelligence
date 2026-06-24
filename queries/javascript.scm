; ============================================
; JAVASCRIPT/TYPESCRIPT QUERIES - Essential Features Only
; US-8: JS/TS Parsing
; US-13: API Impact
; ============================================

; ============================================
; FUNCTION DEFINITIONS (US-8)
; ============================================
(function_declaration
  name: (identifier) @function.name
  parameters: (formal_parameters) @function.params
  body: (statement_block) @function.body) @function.definition

; Arrow functions assigned to variables
(lexical_declaration
  (variable_declarator
    name: (identifier) @function.name
    value: (arrow_function
      parameters: (_) @function.params
      body: (_) @function.body))) @function.arrow

; Regular function expressions
(variable_declaration
  (variable_declarator
    name: (identifier) @function.name
    value: (function_expression
      parameters: (formal_parameters) @function.params))) @function.expression

; ============================================
; CLASS DEFINITIONS (US-8)
; ============================================
(class_declaration
  name: (identifier) @class.name
  body: (class_body) @class.body) @class.definition

(method_definition
  name: (property_identifier) @method.name
  parameters: (formal_parameters) @method.params) @method.definition

; ============================================
; EXPRESS/API ROUTES (US-13: API Impact)
; ============================================
((call_expression
  function: (member_expression
    object: (identifier) @api.object
    property: (property_identifier) @api.method)
  arguments: (arguments
    (string) @api.route))
 (#match? @api.method "^(get|post|put|delete|patch|use)$"))
