import os
import sys
import json
import logging
from src.baseline_store import BaselineStore
from src.breaking_change_engine import compare_reports
from src.intelligence.schema_diff_engine import diff_schema_models
from src.risk_scoring import score_report_risk
from src.intelligence.documentation_contract import build_documentation_contract
from src.serialization.stable_json import dump_stable_file

LOG = logging.getLogger("epic1.cli")

def _stable_generated_at(metadata: dict, commit_sha: str) -> str:
    ts = str((metadata or {}).get("intent", {}).get("timestamp") or "").strip()
    if ts:
        return ts
    if commit_sha and commit_sha != "error":
        return f"commit:{commit_sha}"
    return "1970-01-01T00:00:00Z"

class ReportGenerationService:
    def generate_report(self, raw_analysis: dict, intelligence: dict, new_user: bool) -> dict:
        """
        Loads baseline, detects breaking changes, scores risk, builds documentation contract, 
        and constructs final impact report.
        """
        project_id = raw_analysis["project_id"]
        branch = raw_analysis["branch"] if "branch" in raw_analysis else "main"
        commit_sha = raw_analysis["commit_sha"]
        metadata = raw_analysis["metadata"]
        repo_name = raw_analysis["repo_name"]
        changes = raw_analysis["changes"]
        all_endpoints = intelligence["all_endpoints"]
        database_models = raw_analysis["database_models"]
        severity_counts = raw_analysis["severity_counts"]

        current_normalized_report = intelligence["current_normalized_report"]
        tech_stack = intelligence["tech_stack"]
        api_surface = intelligence["api_surface"]
        architecture = intelligence["architecture"]
        data_model = intelligence["data_model"]
        schema_models = intelligence["schema_models"]
        schema_relationships = intelligence["schema_relationships"]
        repository_evidence = intelligence["repository_evidence"]
        architecture_reconstruction = intelligence["architecture_reconstruction"]
        dependency_graph = intelligence["dependency_graph"]
        call_graph = intelligence["call_graph"]
        call_graph_analysis = intelligence["call_graph_analysis"]
        code_intelligence = intelligence["code_intelligence"]
        dependency_analysis = intelligence["dependency_analysis"]
        impact_analysis = intelligence["impact_analysis"]
        search_index = intelligence["search_index"]
        repo_intel = intelligence["repo_intel"]
        repository_type = intelligence["repository_type"]
        highest_severity = intelligence["highest_severity"]
        quality_warnings = intelligence["quality_warnings"]
        internal_modules_list = intelligence["internal_modules_list"]
        external_dependencies_list = intelligence["external_dependencies_list"]

        # Load baseline and detect breaking changes
        baseline_store = BaselineStore()
        baseline_report = None
        baseline_commit = None
        breaking_changes = []
        baseline_required = bool(os.environ.get("CODE_DETECT_BASELINE_DIR")) and not new_user

        if not new_user:
            LOG.info("Loading baseline...")
            baseline_report = baseline_store.load_baseline(project_id, branch, commit_sha)
            if baseline_required and not baseline_report:
                print(
                    json.dumps(
                        {
                            "error": "Analysis failed",
                            "stage": "analysis",
                            "details": "Baseline commit required for breaking change detection",
                            "retry_possible": False,
                            "report": {},
                        },
                        ensure_ascii=True,
                        separators=(",", ":"),
                    )
                )
                sys.exit(1)
            if baseline_report:
                baseline_commit = baseline_report.get("commit_sha")
                LOG.info(f"Comparing against baseline commit: {baseline_commit}")
                breaking_changes = compare_reports(
                    baseline_report.get("report", {}),
                    current_normalized_report
                )
                schema_diffs = diff_schema_models(
                    baseline_report.get("report", {}),
                    current_normalized_report
                )
                breaking_changes.extend(schema_diffs)
                SEVERITY_ORDER = {"PATCH": 1, "MINOR": 2, "MAJOR": 3}
                breaking_changes = sorted(
                    breaking_changes,
                    key=lambda d: (
                        -SEVERITY_ORDER.get(str(d.get("severity", "PATCH")), 1),
                        str(d.get("file", "")),
                        str(d.get("entity", "")),
                    ),
                )

        breaking_detected = len([bc for bc in breaking_changes if bc.get("severity") == "MAJOR"]) > 0
        documentation_contract = build_documentation_contract(repository_type, breaking_detected)
        risk_analysis = score_report_risk(current_normalized_report, breaking_changes)

        generated_at = _stable_generated_at(metadata, commit_sha)

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
                    "relationships": schema_relationships,
                },
                "quality_warnings": quality_warnings,
                "affected_packages": sorted(raw_analysis["all_packages"]),
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
                "code_intelligence": code_intelligence,
            },
        }

        # Save baseline
        baseline_store.save_baseline(project_id, branch, commit_sha, {
            "commit_sha": commit_sha,
            "report": current_normalized_report,
        })
        LOG.info(f"Baseline saved for {project_id}/{branch}/{commit_sha}")

        return impact_report

    def validate_impact_report(self, report: dict) -> None:
        """Strict structural validation of the impact report. Aborts on failure."""
        r = report.get("report", {})
        ev = r.get("repository_evidence", {})

        components = {c["name"] for c in ev.get("modules", []) if "name" in c}
        seen_endpoints = set()

        for api in ev.get("apis", []):
            method = api.get("method", "").upper()
            path = api.get("path", "")
            key = f"{method} {path}"

            if key in seen_endpoints:
                LOG.warning(f"Validation Warning: Duplicate endpoint found - {key}")
                continue
            seen_endpoints.add(key)

            if not api.get("controller"):
                LOG.error(f"Validation Error: API endpoint {key} missing controller reference")
                sys.exit(2)

            comp = api.get("module")
            if not comp or comp not in components:
                LOG.error(f"Validation Error: API endpoint {key} references unknown or empty module '{comp}'")
                sys.exit(2)

        api_surface_auth = {f"{a.get('method', '').upper()} {a.get('path', '')}": bool(a.get("auth_required")) for a in r.get("api_surface", [])}
        for ep in r.get("api_contract", {}).get("endpoints", []):
            key = f"{ep.get('method', '').upper()} {ep.get('path', '')}"
            if key in api_surface_auth:
                if bool(ep.get("auth_required")) != api_surface_auth[key]:
                    LOG.error(f"Validation Error: Auth desync on {key}")
                    sys.exit(2)

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

        deps_class = r.get("dependency_classification", {})
        for pkg in deps_class.get("internal_modules", []):
            if not pkg.startswith(("./", "../", "src/", "app/")):
                LOG.error(f"Validation Error: internal_module {pkg} is not a valid relative/internal path.")
                sys.exit(2)
        for pkg in deps_class.get("external_dependencies", []):
            if pkg.startswith(("./", "../", "src/", "app/")):
                LOG.error(f"Validation Error: external_dependency {pkg} looks internal.")
                sys.exit(2)

        for entity in ev.get("entities", []):
            name = entity.get("name")
            src = entity.get("source_file")
            if not src:
                LOG.error(f"Validation Error: Entity '{name}' is missing a source_file")
                sys.exit(2)

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
        for repo in ev.get("repositories", []):
            if "name" in repo:
                valid_nodes.add(repo["name"])

        for rel in ev.get("relationships", []):
            rel_type = rel.get("type", "")
            frm = rel.get("from")
            to = rel.get("to")

            if rel_type in ("EXPOSES_API", "CALLS_SERVICE", "USES_ENTITY"):
                if frm not in valid_nodes:
                    LOG.error(f"Validation Error: Relationship '{rel_type}' has invalid 'from' node: {frm}")
                    sys.exit(2)
                if to not in valid_nodes:
                    LOG.error(f"Validation Error: Relationship '{rel_type}' has invalid 'to' node: {to}")
                    sys.exit(2)
            elif rel_type == "IMPORTS_MODULE":
                if frm not in valid_nodes:
                    LOG.error(f"Validation Error: Relationship '{rel_type}' has invalid 'from' node: {frm}")
                    sys.exit(2)
                if not str(to or "").strip():
                    LOG.error(f"Validation Error: Relationship '{rel_type}' has empty 'to' node")
                    sys.exit(2)

    def write_and_print_report(self, report: dict, output_path: str) -> None:
        dump_stable_file(output_path, report, pretty=True)
        LOG.info(f"Report written to: {output_path}")
        print(json.dumps(report, ensure_ascii=True, separators=(",", ":")))

    def generate_error_report(self, exception: Exception, repo_input: str, branch: str) -> dict:
        return {
            "schema_version": "epic1-impact/v3",
            "version": "1.0.0",
            "status": "error",
            "error": str(exception),
            "meta": {"tool_version": "1.0.0", "generated_at": _stable_generated_at({}, "error")},
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
                "code_intelligence": {
                    "symbol_index": [],
                    "call_graph": {"nodes": [], "edges": []},
                    "dependency_graph": {
                        "modules": [],
                        "dependencies": [],
                        "cycle_detected": False,
                        "circular_dependencies": [],
                    },
                    "repository_graph": {"nodes": [], "edges": []},
                },
            },
        }
