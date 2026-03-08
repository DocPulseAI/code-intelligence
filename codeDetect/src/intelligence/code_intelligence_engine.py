"""Code Intelligence Reasoning Engine.

Aggregates insights from architecture, dependencies, call graphs, and impact analysis
into a unified, multi-step intelligence JSON.
"""

from typing import Any, Dict


def analyze_code_intelligence(
    architecture_reconstruction: Dict[str, Any],
    dependency_analysis: Dict[str, Any],
    dependency_graph: Dict[str, Any],
    call_graph_analysis: Dict[str, Any],
    impact_analysis: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Performs deep system analysis across four operational steps.
    """
    
    # Step 1: Architecture Validation
    # Detect layer violations (e.g. Data layer depending on Presentation)
    layer_violations = []
    
    # Map components to their layers
    component_layers = {}
    for comp in architecture_reconstruction.get("components", []):
        component_layers[comp.get("id", "Unknown")] = comp.get("layer", "Unknown")
        
    layer_hierarchy = {
        "Presentation": 1,
        "Application": 2,
        "Data": 3,
        "Infrastructure": 4,
        "Unknown": 5
    }
    
    for edge in architecture_reconstruction.get("edges", []):
        source = edge.get("from")
        target = edge.get("to")
        
        # We need to know the layers to detect violations
        # e.g., if Application layer calls Presentation layer, that's a violation.
        s_layer = component_layers.get(source)
        t_layer = component_layers.get(target)
        
        # Lower index means "higher" in architecture (e.g., Presentation is 1)
        # Higher index means "lower" (e.g., Data is 3)
        # General rule: Higher layer (1) implies lower layer (3). Lower layer should NOT imply higher.
        if s_layer and t_layer and s_layer != "Unknown" and t_layer != "Unknown":
            if layer_hierarchy.get(s_layer, 5) > layer_hierarchy.get(t_layer, 5):
                layer_violations.append({
                    "violation_type": "Upward Dependency",
                    "description": f"{s_layer} layer component '{source}' unlawfully depends on {t_layer} layer component '{target}'"
                })

    architecture_insights = {
        "layer_violations": layer_violations,
        "tightly_coupled_modules": dependency_analysis.get("high_coupling_modules", []),
        "module_centrality": dependency_analysis.get("core_modules", [])
    }

    # Step 2: Dependency Analysis
    dependency_insights = {
        "circular_dependencies": dependency_analysis.get("cycles", []),
        "high_fan_in_modules": [],
        "unstable_modules": []
    }
    
    node_metrics = dependency_analysis.get("graph_metrics", {}).get("node_metrics", {})
    for mod, metrics in node_metrics.items():
        if metrics.get("instability", 0.0) >= 0.8:
            dependency_insights["unstable_modules"].append(mod)
        if metrics.get("fan_in", 0) >= 3:
            dependency_insights["high_fan_in_modules"].append(mod)

    # Step 3: Call Graph Analysis
    call_graph_insights = {
        "entry_points": call_graph_analysis.get("entry_points", []),
        "execution_paths": call_graph_analysis.get("execution_paths", []),
        "unused_functions": call_graph_analysis.get("dead_functions", [])
    }

    # Step 4: Change Impact Analysis
    # We pass the pre-computed impact analysis verbatim.
    
    return {
        "architecture_insights": architecture_insights,
        "dependency_insights": dependency_insights,
        "call_graph_insights": call_graph_insights,
        "impact_analysis": impact_analysis
    }
