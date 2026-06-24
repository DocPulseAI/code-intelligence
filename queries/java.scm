; ============================================
; JAVA QUERIES - Essential Features Only
; US-6: Java Parsing
; US-13: API Impact
; US-14: Schema Changes
; ============================================

; ============================================
; CLASS DEFINITIONS (US-6)
; ============================================
(class_declaration
  name: (identifier) @class.name
  body: (class_body) @class.body) @class.definition

; ============================================
; METHOD DEFINITIONS (US-6)
; ============================================
(method_declaration
  name: (identifier) @method.name
  parameters: (formal_parameters) @method.params
  body: (block)? @method.body) @method.definition

; Constructor definitions
(constructor_declaration
  name: (identifier) @constructor.name
  parameters: (formal_parameters) @constructor.params) @constructor.definition

; ============================================
; ANNOTATIONS (US-6)
; ============================================
(marker_annotation
  name: (identifier) @annotation.name) @annotation

(annotation
  name: (identifier) @annotation.name
  arguments: (annotation_argument_list)? @annotation.args) @annotation.full

; ============================================
; API MAPPING ANNOTATIONS (US-13: API Impact)
; ============================================
((annotation
  name: (identifier) @api.annotation
  arguments: (annotation_argument_list
    (element_value_pair
      value: (string_literal) @api.route)?
    (string_literal)? @api.route.direct)?)
 (#match? @api.annotation "^(RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)$"))

; ============================================
; SCHEMA ANNOTATIONS (US-14: Schema Changes)
; ============================================
((marker_annotation
  name: (identifier) @schema.annotation)
 (#match? @schema.annotation "^(Entity|Table|Column|Id|OneToMany|ManyToOne|ManyToMany|JoinColumn)$"))

; ============================================
; FIELD DECLARATIONS (US-6)
(field_declaration
  type: (_) @field.type
  declarator: (variable_declarator
    name: (identifier) @field.name)) @field.definition
