"""
Code Change Detector - CLI Entry Point
Complete implementation with all intelligence layers
"""

import hashlib
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

# Configure logging
LOG = logging.getLogger("epic1.cli")
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def main():
    """CLI entry point for code analysis."""
    # Parse arguments
    args = sys.argv[1:]
    if len(args) < 1:
        LOG.error("Usage: python main.py <repo_url_or_path> [github_token] [branch] [--new-user]")
        sys.exit(1)

    repo_input = args[0]
    github_token = None
    branch = "main"
    new_user = False

    # Parse optional arguments
    for i, arg in enumerate(args[1:]):
        if arg == "--new-user":
            new_user = True
        elif "--" not in arg:
            if i == 0 and arg.startswith(("ghp_", "github_pat_")):
                github_token = arg
            elif i == 1 or (i == 0 and not arg.startswith(("ghp_", "github_pat_"))):
                branch = arg

    # Determine if it's a URL or local path
    is_url = repo_input.startswith(("http://", "https://", "git@"))

    try:
        # Import dependencies
        from src.git_manager import GitManager
        from src.file_filter import FileFilter
        from src.parsers.ts_parser import TSParser
        from src.parsers.js_parser import JSParser
        from src.parsers.python_parser import PythonParser
        from src.parsers.java_parser import JavaParser
        from src.parsers.schema_detector import SchemaDetector
        from src.scorers import SeverityCalculator
        from src.intelligence.tech_stack_model import build_tech_stack
        from src.intelligence.api_surface import build_api_surface
        from src.intelligence.repository_classifier import classify_repository_type
        from src.intelligence.documentation_contract import build_documentation_contract
        from src.intelligence.architecture_model import build_architecture_model
        from src.intelligence.data_model_graph import build_data_model
        from src.intelligence.schema_diff_engine import extract_canonical_models, diff_schema_models
        from src.intelligence.repository_evidence import build_repository_evidence
        from src.intelligence.architecture_reconstructor import reconstruct_architecture
        from src.intelligence.dependency_graph_engine import build_dependency_graph
        from src.intelligence.call_graph_engine import build_call_graph
        from src.intelligence.impact_analysis_engine import build_impact_analysis
        from src.intelligence.dependency_analysis_engine import analyze_dependencies
        from src.intelligence.call_graph_analysis_engine import analyze_call_graph
        from src.intelligence.repository_intelligence_engine import analyze_repository_intelligence
        from src.intelligence.search_index_builder import build_search_index
        from src.breaking_change_engine import compare_reports
        from src.risk_scoring import score_report_risk
        from src.baseline_store import BaselineStore
        from src.serialization.stable_json import dump_stable_file

        # Initialize Git manager
        git_mgr = GitManager(repo_input, github_token, branch)

        try:
            # Get repository metadata
            metadata = git_mgr.get_metadata()
            repo_name = git_mgr.repo_slug.split("/")[-1] if git_mgr.repo_slug else metadata.get("repository", "unknown")
            commit_sha = metadata.get("full_sha", metadata.get("commit_sha", "HEAD"))
            project_id = git_mgr.repo_slug or repo_name

            # Get changed or all files
            changed_files = []
            if not new_user:
                try:
                    changed_files = git_mgr.get_changed_files(commit_sha)
                    changed_files = [f for f in changed_files if f.get("change_type") != "ERROR"]
                except Exception:
                    pass

            # Always get full file inventory
            all_files = git_mgr.list_all_files()
            all_file_paths = [f["path"] for f in all_files if f.get("path") and f.get("change_type") != "ERROR"]

            # Build read_file callback
            def read_file(path: str):
                return git_mgr.get_file_content(path)

            # Build intelligence layers
            LOG.info("Building tech stack analysis...")
            tech_stack = build_tech_stack(all_file_paths, read_file)

            # Determine files to analyze
            files_to_analyze = changed_files if (changed_files and not new_user) else all_files
            file_paths_to_analyze = [
                f.get("path", "") for f in files_to_analyze
                if f.get("path") and f.get("change_type") != "ERROR"
            ]

            # Analyze files
            LOG.info(f"Analyzing {len(file_paths_to_analyze)} files...")
            changes = []
            all_endpoints = []
            all_packages = set()
            severity_counts = {"MAJOR": 0, "MINOR": 0, "PATCH": 0}
            database_models = []
            file_features: dict[str, dict] = {}
            file_schema_tags: dict[str, list] = {}

            PARSER_MAP = {
                ".ts": TSParser, ".tsx": TSParser,
                ".js": JSParser, ".jsx": JSParser,
                ".py": PythonParser,
                ".java": JavaParser,
            }

            for file_entry in files_to_analyze[:200]:  # Limit for performance
                path = file_entry.get("path", "")
                if not path or FileFilter.should_exclude_from_analysis(path):
                    continue

                ext = os.path.splitext(path)[1].lower()
                content = read_file(path) or ""
                if not content:
                    continue

                # Parse features
                features = {}
                parser = PARSER_MAP.get(ext)
                if parser:
                    try:
                        features = parser.analyze(content)
                    except Exception:
                        features = {}

                # Detect schema
                schema_tags = []
                try:
                    schema_tags = SchemaDetector.analyze(path, content) or []
                except Exception:
                    pass

                # Compute severity
                severity = SeverityCalculator.assess(ext, features, schema_tags)
                severity_counts[severity] = severity_counts.get(severity, 0) + 1

                # Classify component
                path_lower = path.lower()
                if path_lower.startswith(("frontend/", "client/", "src/components/")):
                    component = "frontend"
                elif path_lower.startswith(("backend/", "server/", "src/routes/")):
                    component = "backend"
                elif any(k in path_lower for k in ("migration", "schema", "model", ".sql")):
                    component = "database"
                elif any(k in path_lower for k in ("docker", "terraform", ".github/workflows", "deploy", "infra")):
                    component = "infra"
                else:
                    component = "backend"

                # Collect endpoints
                endpoints = features.get("api_endpoints", []) or features.get("api_routes", []) or []
                for ep in endpoints:
                    all_endpoints.append({
                        "method": ep.get("verb") or ep.get("method") or "GET",
                        "path": ep.get("route") or ep.get("path") or "",
                        "source_file": path,
                        "line": ep.get("line", 0),
                    })

                # Collect dependencies
                for dep in features.get("dependencies", []):
                    if dep.startswith((".", "..")):
                        file_dir = os.path.dirname(path)
                        norm_dep = os.path.normpath(os.path.join(file_dir, dep)).replace("\\", "/")
                        all_packages.add(norm_dep)
                    else:
                        all_packages.add(dep)

                # Collect database models
                if schema_tags:
                    database_models.append({"file": path, "tags": schema_tags})

                # Collect per-file features for repository evidence
                file_features[path] = features
                file_schema_tags[path] = schema_tags

                changes.append({
                    "file": path,
                    "change_type": file_entry.get("change_type", "ADDED"),
                    "component": component,
                    "severity": severity,
                    "features": features,
                    "schema_tags": schema_tags,
                })

            # Sort baseline root elements for determinism
            all_endpoints = sorted(all_endpoints, key=lambda ep: (ep.get("method", ""), ep.get("path", "")))
            database_models = sorted(database_models, key=lambda m: m.get("file", ""))
            changes = sorted(changes, key=lambda c: c.get("file", ""))

            # Build advanced intelligence layers
            LOG.info("Building API surface...")
            api_surface = build_api_surface(all_endpoints) if all_endpoints else []

            LOG.info("Building architecture model...")
            architecture = build_architecture_model(all_file_paths, read_file)

            LOG.info("Building data model...")
            data_model = build_data_model(all_file_paths, read_file)

            LOG.info("Extracting canonical schema models...")
            schema_models = extract_canonical_models(all_file_paths, read_file)

            # Build repository evidence graph
            LOG.info("Building repository evidence...")
            repository_evidence = build_repository_evidence(
                all_file_paths, read_file, file_features, file_schema_tags, tech_stack
            )

            LOG.info("Reconstructing architecture...")
            arch_recon = reconstruct_architecture(repository_evidence)
            architecture_reconstruction = arch_recon.get("architecture_reconstruction", {})

            LOG.info("Building dependency graph...")
            dependency_graph = build_dependency_graph(repository_evidence, changes)
            
            LOG.info("Building call graph...")
            call_graph = build_call_graph(repository_evidence)
            
            LOG.info("Analyzing call graph hierarchy...")
            call_analysis_res = analyze_call_graph(call_graph, repository_evidence)
            call_graph_analysis = call_analysis_res.get("call_graph_analysis", {})
            
            LOG.info("Analyzing dependencies...")
            dep_analysis_res = analyze_dependencies(dependency_graph)
            dependency_analysis = dep_analysis_res.get("dependency_analysis", {})
            impact_analysis = build_impact_analysis(
                repository_evidence, dependency_graph, call_graph, changes
            )

            LOG.info("Building search index...")
            search_index_res = build_search_index(
                repository_evidence,
                architecture_reconstruction,
                dependency_graph,
                call_graph,
                read_file,
            )
            search_index = search_index_res.get(
                "search_index",
                {"symbols": [], "references": [], "apis": [], "modules": []},
            )
            
            LOG.info("Generating deep repository intelligence reasoning...")
            repo_intel = analyze_repository_intelligence(
                ev_apis_dict if 'ev_apis_dict' in locals() else {"endpoints": repository_evidence.get('apis', [])},
                dependency_graph,
                call_graph,
                architecture_reconstruction,
                changes,
                database_models,
                dependency_analysis
            )

            # ---------------------------------------------------------------

            # Override api_surface and api_contract with resolved endpoints
            # from repository_evidence.apis (which carry full resolved paths
            # and controller mappings from the mount resolution engine).
            # ---------------------------------------------------------------
            ev_apis = repository_evidence.get("apis", [])
            if ev_apis:
                LOG.info(f"Overriding api_surface with {len(ev_apis)} resolved endpoints from repository_evidence")
                # Build enriched endpoint list for api_surface
                resolved_endpoints_for_surface = []
                health_endpoints = []
                for a in ev_apis:
                    if a.get("method") and a.get("path"):
                        path_str = str(a.get("path", ""))
                        handler_str = str(a.get("handler", "")).lower()
                        ctrl_str = str(a.get("controller", "")).lower()
                        
                        # FIX 1: Remove Health / Root Routes
                        is_health = (path_str == "/") or any(k in handler_str for k in ("status", "health", "api is running")) or any(k in ctrl_str for k in ("status", "health", "api is running"))
                        if is_health:
                            health_endpoints.append(a)
                            continue

                        ep = {
                            "method": a.get("method", "GET"),
                            "path": a.get("path", ""),
                            "source": {"controller": a.get("controller", "")},
                            "controller": a.get("controller", ""),
                            "component": a.get("module", ""),
                            "source_file": a.get("source_file", ""),
                            "router_file": a.get("router_file", ""),
                            "line": a.get("line", 0),
                        }
                        if "auth_required" in a:
                            ep["auth_required"] = a["auth_required"]
                        resolved_endpoints_for_surface.append(ep)
                api_surface = build_api_surface(resolved_endpoints_for_surface)
                # Also replace all_endpoints so api_contract gets resolved paths
                all_endpoints = sorted(
                    resolved_endpoints_for_surface,
                    key=lambda ep: (ep.get("method", ""), ep.get("path", ""))
                )
            # ---------------------------------------------------------------

            # Classify repository
            LOG.info("Classifying repository type...")
            repository_type = classify_repository_type(all_file_paths, read_file, len(all_endpoints))

            # Determine highest severity
            if severity_counts.get("MAJOR", 0) > 0:
                highest_severity = "MAJOR"
            elif severity_counts.get("MINOR", 0) > 0:
                highest_severity = "MINOR"
            else:
                highest_severity = "PATCH"

            # Build current report (normalized structure for baseline comparison)
            generated_at = datetime.utcnow().isoformat() + "Z"
            current_normalized_report = {
                "api_surface": api_surface,
                "api_contract": {"endpoints": all_endpoints[:200]},
                "data_model": data_model,
                "schema_analysis": {
                    "models": schema_models,
                    "models_detected": len(schema_models),
                },
                "architecture_reconstruction": architecture_reconstruction,
                "dependency_graph": dependency_graph,
                "dependency_analysis": dependency_analysis,
                "call_graph": call_graph,
                "call_graph_analysis": call_graph_analysis,
                "impact_analysis": impact_analysis,
                "search_index": search_index,
            }


            # Load baseline and detect breaking changes
            baseline_store = BaselineStore()
            baseline_report = None
            baseline_commit = None
            breaking_changes = []

            if not new_user:
                LOG.info("Loading baseline...")
                baseline_report = baseline_store.load_baseline(project_id, branch, commit_sha)
                if baseline_report:
                    baseline_commit = baseline_report.get("commit_sha")
                    LOG.info(f"Comparing against baseline commit: {baseline_commit}")
                    # Detect breaking changes
                    breaking_changes = compare_reports(
                        baseline_report.get("report", {}),
                        current_normalized_report
                    )
                    # Add schema diffs
                    schema_diffs = diff_schema_models(
                        baseline_report.get("report", {}),
                        current_normalized_report
                    )
                    breaking_changes.extend(schema_diffs)
                    # Sort by severity
                    SEVERITY_ORDER = {"PATCH": 1, "MINOR": 2, "MAJOR": 3}
                    breaking_changes = sorted(
                        breaking_changes,
                        key=lambda d: (
                            -SEVERITY_ORDER.get(str(d.get("severity", "PATCH")), 1),
                            str(d.get("file", "")),
                            str(d.get("entity", "")),
                        ),
                    )

            # Build documentation contract
            breaking_detected = len([bc for bc in breaking_changes if bc.get("severity") == "MAJOR"]) > 0
            documentation_contract = build_documentation_contract(repository_type, breaking_detected)

            # Calculate risk score
            risk_analysis = score_report_risk(current_normalized_report, breaking_changes)

            internal_modules_list = []
            external_dependencies_list = []
            for pkg in sorted(all_packages):
                if pkg.startswith(("./", "../", "src/", "app/")):
                    internal_modules_list.append(pkg)
                else:
                    external_dependencies_list.append(pkg)

            # Build complete impact report matching schema
            impact_report = {
                "schema_version": "epic1-impact/v3",
                "version": "1.0.0",
                "status": "success",
                "meta": {
                    "tool_version": "1.0.0",
                    "generated_at": generated_at,
                },
                "project_id": project_id,
                "branch": branch,
                "commit_sha": commit_sha,
                "baseline_commit": baseline_commit,
                "analysis_summary": {
                    "total_files": len(changes),
                    "highest_severity": highest_severity,
                    "breaking_changes_detected": breaking_detected,
                },
                "breaking_changes": breaking_changes[:100],
                "statistics": risk_analysis.get("statistics", {
                    "total_changes": len(breaking_changes),
                    "major": len([bc for bc in breaking_changes if bc.get("severity") == "MAJOR"]),
                    "minor": len([bc for bc in breaking_changes if bc.get("severity") == "MINOR"]),
                    "patch": len([bc for bc in breaking_changes if bc.get("severity") == "PATCH"]),
                }),
                "severity": risk_analysis.get("severity", highest_severity),
                "deterministic": True,
                "report": {
                    "context": {
                        "repository": repo_name,
                        "branch": branch,
                        "commit_sha": commit_sha,
                        "full_sha": metadata.get("full_sha", commit_sha),
                        "author": metadata.get("author", "unknown"),
                        "author_email": metadata.get("author_email", ""),
                        "commit_timestamp": metadata.get("intent", {}).get("timestamp", ""),
                        "generated_at": generated_at,
                    },
                    "analysis_summary": {
                        "highest_severity": highest_severity,
                        "breaking_changes_detected": breaking_detected,
                        "total_files": len(changes),
                        "total_files_analyzed": len(changes),
                        "severity_distribution": severity_counts,
                    },
                    "changes": changes[:100],
                    "files": [
                        {
                            "path": c["file"],
                            "change_type": c["change_type"],
                            "severity": c["severity"],
                            "component": c["component"],
                            "features": c.get("features", {}),
                        }
                        for c in changes[:100]
                    ],
                    "repository_type": repository_type,
                    "tech_stack": tech_stack,
                    "documentation_contract": documentation_contract,
                    "architecture": architecture,
                    "api_surface": api_surface[:200],
                    "api_contract": {"endpoints": all_endpoints[:200]},
                    "api_summary": {
                        "added": len([bc for bc in breaking_changes if bc.get("type") == "ENDPOINT_ADDED"]),
                        "modified": len([bc for bc in breaking_changes if bc.get("type") == "ENDPOINT_MODIFIED"]),
                        "removed": len([bc for bc in breaking_changes if bc.get("type") == "ENDPOINT_REMOVED"]),
                    },
                    "data_model": data_model,
                    "schema_analysis": {
                        "models": schema_models,
                        "models_detected": len(schema_models),
                    },
                    "affected_packages": sorted(all_packages),
                    "database_impact": {
                        "models": database_models,
                        "model_count": len(database_models),
                        "schema_changed": len([bc for bc in breaking_changes if "SCHEMA" in bc.get("type", "") or "ENTITY" in bc.get("type", "")]) > 0,
                        "tables_affected": [m.get("file", "") for m in database_models[:10]],
                    },
                    "risk_analysis": {
                        "operational_risk": "HIGH" if risk_analysis.get("score", 0) > 40 else "MEDIUM" if risk_analysis.get("score", 0) > 15 else "LOW",
                        "blast_radius": "APPLICATION" if len(breaking_changes) > 5 else "SERVICE" if len(breaking_changes) > 0 else "LOCAL",
                        "test_scope": ["unit", "integration"] if breaking_detected else ["unit"],
                        "migration_required": any("SCHEMA" in bc.get("type", "") or bc.get("severity") == "MAJOR" for bc in breaking_changes),
                    },
                    "breaking_change_details": breaking_changes[:50],
                    "change_complexity_score": min(100, len(changes) * 0.1 + len(breaking_changes) * 2),
                    "change_summary": metadata.get("intent", {}).get("message", "No commit message"),
                    "infra_analysis": {
                        "docker": "docker" in (tech_stack.get("infra") or []),
                        "ci_workflow": (tech_stack.get("ci") or [None])[0] if tech_stack.get("ci") else None,
                    },
                    "summary": f"Analyzed {len(changes)} files: {len(all_endpoints)} endpoints, {len(breaking_changes)} breaking changes, stack={tech_stack.get('backend_framework') or 'unknown'}",
                    "doc_contract": documentation_contract,
                    "dependency_classification": {
                        "external_dependencies": external_dependencies_list[:50],
                        "internal_modules": internal_modules_list[:50],
                        "static_assets": [],
                        "dev_dependencies": [],
                    },
                    "repository_evidence": repository_evidence,
                    "architecture_reconstruction": architecture_reconstruction,
                    "architecture_insights": repo_intel.get("architecture_insights", {}),
                    "corrected_call_graph": repo_intel.get("corrected_call_graph", {}),
                    "dead_function_analysis": repo_intel.get("dead_function_analysis", {}),
                    "impact_propagation": repo_intel.get("impact_propagation", {}),

                    "dependency_graph": dependency_graph,
                    "dependency_analysis": dependency_analysis,
                    "call_graph": call_graph,
                    "call_graph_analysis": call_graph_analysis,
                    "impact_analysis": impact_analysis,
                    "search_index": search_index,
                },
            }

            # Save baseline
            baseline_store.save_baseline(project_id, branch, commit_sha, {
                "commit_sha": commit_sha,
                "report": current_normalized_report,
            })
            LOG.info(f"Baseline saved for {project_id}/{branch}/{commit_sha}")

            # Validate Report Data Integrity
            def validate_impact_report(report: dict) -> None:
                """Strict structural validation of the impact report. Aborts on failure."""
                r = report.get("report", {})
                ev = r.get("repository_evidence", {})
                
                # 1. No undefined components (modules)
                components = {c["name"] for c in ev.get("modules", []) if "name" in c}
                
                # 2. Duplicate Endpoints Validation + APIs belong to components
                seen_endpoints = set()
                for api in ev.get("apis", []):
                    method = api.get("method", "").upper()
                    path = api.get("path", "")
                    key = f"{method} {path}"
                    
                    if key in seen_endpoints:
                        LOG.error(f"Validation Error: Duplicate endpoint found - {key}")
                        sys.exit(2)
                    seen_endpoints.add(key)
                    
                    if not api.get("controller"):
                        LOG.error(f"Validation Error: API endpoint {key} missing controller reference")
                        sys.exit(2)
                        
                    comp = api.get("module")
                    if not comp or comp not in components:
                        LOG.error(f"Validation Error: API endpoint {key} references unknown or empty module '{comp}'")
                        sys.exit(2)
                        
                for api in r.get("api_surface", []):
                    method = api.get("method", "").upper()
                    path = api.get("path", "")
                    key = f"{method} {path}"
                    if key not in seen_endpoints:
                         pass # It's possible for things to be in surface but not apis directly depending on extraction, but if checked we ensure consistency against knowns.

                # PHASE 2 MANDATORY VALIDATIONS
                # Validation 1: Auth sync mapping
                # Both arrays must match auth status precisely by endpoint.
                api_surface_auth = {f"{a.get('method', '').upper()} {a.get('path', '')}": bool(a.get("auth_required")) for a in r.get("api_surface", [])}
                for ep in r.get("api_contract", {}).get("endpoints", []):
                    key = f"{ep.get('method', '').upper()} {ep.get('path', '')}"
                    if key in api_surface_auth:
                        if bool(ep.get("auth_required")) != api_surface_auth[key]:
                            LOG.error(f"Validation Error: Auth desync on {key}")
                            sys.exit(2)
                
                # Validation 2: Component field presence & API structure
                for ep in r.get("api_contract", {}).get("endpoints", []):
                    method = ep.get('method')
                    path = ep.get('path')
                    key = f"{str(method).upper()} {path}"
                    
                    if not method:
                        LOG.error("Validation Error: API endpoint missing method")
                        sys.exit(2)
                    if not path or "//" in path:
                        LOG.error("Validation Error: API endpoint missing path or unsanitized")
                        sys.exit(2)
                    if not ep.get("controller") and not ep.get("source", {}).get("controller"):
                        LOG.error(f"Validation Error: API endpoint missing controller for {key}")
                        sys.exit(2)
                    if not ep.get("component"):
                        LOG.error(f"Validation Error: Component field is empty for {key}")
                        sys.exit(2)
                
                # Validation 3: Dependency classification accuracy
                deps_class = r.get("dependency_classification", {})
                for pkg in deps_class.get("internal_modules", []):
                    if not pkg.startswith(("./", "../", "src/", "app/")):
                        LOG.error(f"Validation Error: internal_module {pkg} is not a valid relative/internal path.")
                        sys.exit(2)
                for pkg in deps_class.get("external_dependencies", []):
                    if pkg.startswith(("./", "../", "src/", "app/")):
                        LOG.error(f"Validation Error: external_dependency {pkg} looks internal.")
                        sys.exit(2)

                # 3. All entities have source files
                for entity in ev.get("entities", []):
                    name = entity.get("name")
                    src = entity.get("source_file")
                    if not src:
                        LOG.error(f"Validation Error: Entity '{name}' is missing a source_file")
                        sys.exit(2)

                # 4. Valid relationships
                valid_nodes = components.union(seen_endpoints)
                for ent in ev.get("entities", []):
                    if "name" in ent:
                        valid_nodes.add(ent["name"])
                for router in ev.get("routers", []):
                    if "name" in router:
                        valid_nodes.add(router["name"])
                for svc in ev.get("services", []):
                    if "name" in svc:
                        valid_nodes.add(svc["name"])
                        
                for rel in ev.get("relationships", []):
                    # For EXPOSES_API, 'to' is 'METHOD /path'
                    rel_type = rel.get("type", "")
                    frm = rel.get("from")
                    to = rel.get("to")
                    
                    if rel_type in ("EXPOSES_API", "IMPORTS_MODULE", "CALLS_SERVICE", "USES_ENTITY"):
                        if frm not in valid_nodes:
                            LOG.error(f"Validation Error: Relationship '{rel_type}' has invalid 'from' node: {frm}")
                            sys.exit(2)
                        if to not in valid_nodes:
                            LOG.error(f"Validation Error: Relationship '{rel_type}' has invalid 'to' node: {to}")
                            sys.exit(2)

            LOG.info("Validating impact report integrity...")
            validate_impact_report(impact_report)

            # Write to impact_report.json
            output_path = os.path.join(os.path.dirname(__file__), "impact_report.json")
            dump_stable_file(output_path, impact_report, pretty=True)
            LOG.info(f"Report written to: {output_path}")

            # Print to stdout (for API to parse)
            print(json.dumps(impact_report, ensure_ascii=True, separators=(",", ":")))

        finally:
            git_mgr.cleanup()

    except Exception as e:
        LOG.error(f"Analysis failed: {str(e)}", exc_info=True)
        # Write error report
        error_report = {
            "schema_version": "epic1-impact/v3",
            "version": "1.0.0",
            "status": "error",
            "error": str(e),
            "meta": {"tool_version": "1.0.0", "generated_at": datetime.utcnow().isoformat() + "Z"},
            "project_id": repo_input.split("/")[-1] if "/" in repo_input else "unknown",
            "branch": branch,
            "commit_sha": "error",
            "baseline_commit": None,
            "analysis_summary": {"total_files": 0, "highest_severity": "PATCH", "breaking_changes_detected": False},
            "breaking_changes": [],
            "statistics": {"total_changes": 0, "major": 0, "minor": 0, "patch": 0},
            "severity": "PATCH",
            "deterministic": True,
            "report": {
                "context": {},
                "analysis_summary": {},
                "changes": [],
                "repository_type": "library",
                "tech_stack": {"backend_framework": None, "frontend_framework": None, "database": None, "orm": None, "infra": [], "ci": []},
                "documentation_contract": {"requires_readme": True, "requires_api_reference": False, "requires_architecture_doc": False, "requires_adr": False},
                "architecture": {"pattern": "modular-monolith", "layers": [], "external_dependencies": []},
                "api_surface": [],
                "data_model": {"entities": [], "relationships": []},
                "doc_contract": {},
                "search_index": {"symbols": [], "references": [], "apis": [], "modules": []},
            },
        }
        output_path = os.path.join(os.path.dirname(__file__), "impact_report.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(error_report, f, indent=2, ensure_ascii=True)
        print(json.dumps(error_report, ensure_ascii=True, separators=(",", ":")))
        sys.exit(1)


if __name__ == "__main__":
    main()
