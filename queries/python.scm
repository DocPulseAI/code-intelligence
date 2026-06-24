

; ============================================
; PYTHON QUERIES - Essential Features Only
; US-10: Python Parsing
; US-13: API Impact
; US-14: Schema Changes (Django ORM)
; ============================================

; ============================================
; FUNCTION DEFINITIONS (US-10)
; ============================================
(function_definition
  name: (identifier) @function.name
  parameters: (parameters) @function.params
  body: (block) @function.body) @function.definition

; Async functions
(function_definition
  name: (identifier) @function.async.name
  parameters: (parameters) @function.params
  body: (block) @function.body) @function.async

; ============================================
; CLASS DEFINITIONS (US-10)
; ============================================
(class_definition
  name: (identifier) @class.name
  body: (block) @class.body) @class.definition

(class_definition
  name: (identifier) @class.name
  superclasses: (argument_list) @class.bases
  body: (block) @class.body) @class.inheritance

; ============================================
; DECORATORS (US-10)
; ============================================
(decorator
  (identifier) @decorator.name) @decorator.simple

(decorator
  (call
    function: (identifier) @decorator.name
    arguments: (argument_list) @decorator.args)) @decorator.call

(decorator
  (call
    function: (attribute
      object: (identifier) @decorator.module
      attribute: (identifier) @decorator.method)
    arguments: (argument_list) @decorator.args)) @decorator.attribute

; ============================================
; Flask/FastAPI route decorators (US-13: API Impact)
; ============================================
((decorator
  (call
    function: (attribute
      attribute: (identifier) @api.method)
    arguments: (argument_list
      (string) @api.route)))
 (#match? @api.method "^(route|get|post|put|delete|patch)$"))

; ============================================
; DJANGO/ORM MODELS (US-14: Schema Changes)
((class_definition
  name: (identifier) @model.name
  superclasses: (argument_list
    (attribute
      attribute: (identifier) @model.base)))
 (#match? @model.base "^Model$"))

; Field definitions
((assignment
  left: (identifier) @field.name
  right: (call
    function: (attribute
      attribute: (identifier) @field.type)))
 (#match? @field.type "^(CharField|IntegerField|TextField|ForeignKey|DateTimeField|BooleanField)$"))
