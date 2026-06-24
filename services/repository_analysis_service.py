import os
import logging
from src.git_manager import GitManager
from src.file_filter import FileFilter
from src.parsers.ts_parser import TSParser
from src.parsers.js_parser import JSParser
from src.parsers.python_parser import PythonParser
from src.parsers.java_parser import JavaParser
from src.parsers.schema_detector import SchemaDetector
from src.scorers import SeverityCalculator

LOG = logging.getLogger("epic1.cli")
_GITHUB_TOKEN_PREFIXES = ("ghp_", "github_pat_", "gho_", "ghu_", "ghs_", "ghr_")

def _looks_like_github_token(value: str | None) -> bool:
    token = str(value or "").strip()
    return token.startswith(_GITHUB_TOKEN_PREFIXES)

class RepositoryAnalysisService:
    def __init__(self, repo_input: str, github_token: str | None, branch: str, new_user: bool):
        self.repo_input = repo_input
        self.github_token = github_token
        self.branch = branch
        self.new_user = new_user

    def analyze(self) -> dict:
        """
        Clones or checkouts target repo, lists all files, filters them, 
        and extracts raw file AST features and schema tags.
        """
        git_mgr = GitManager(self.repo_input, self.github_token, self.branch)
        
        # GitManager operations
        metadata = git_mgr.get_metadata()
        repo_name = git_mgr.repo_slug.split("/")[-1] if git_mgr.repo_slug else metadata.get("repository", "unknown")
        commit_sha = metadata.get("full_sha", metadata.get("commit_sha", "HEAD"))
        project_id = git_mgr.repo_slug or repo_name

        changed_files = []
        if not self.new_user:
            try:
                changed_files = git_mgr.get_changed_files(commit_sha)
                changed_files = [f for f in changed_files if f.get("change_type") != "ERROR"]
            except Exception:
                pass

        all_files = git_mgr.list_all_files()
        all_file_paths = [f["path"] for f in all_files if f.get("path") and f.get("change_type") != "ERROR"]

        def read_file(path: str):
            return git_mgr.get_file_content(path)

        files_to_analyze = changed_files if (changed_files and not self.new_user) else all_files
        file_paths_to_analyze = [
            f.get("path", "") for f in files_to_analyze
            if f.get("path") and f.get("change_type") != "ERROR"
        ]

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

        # Analyze at most 200 files for performance
        for file_entry in files_to_analyze[:200]:
            path = file_entry.get("path", "")
            if not path or FileFilter.should_exclude_from_analysis(path):
                continue

            ext = os.path.splitext(path)[1].lower()
            content = read_file(path) or ""
            if not content:
                continue

            features = {}
            parser = PARSER_MAP.get(ext)
            if parser:
                try:
                    features = parser.analyze(content)
                except Exception:
                    features = {}

            schema_tags = []
            try:
                schema_tags = SchemaDetector.analyze(path, content) or []
            except Exception:
                pass

            severity = SeverityCalculator.assess(ext, features, schema_tags)
            severity_counts[severity] = severity_counts.get(severity, 0) + 1

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

            endpoints = features.get("api_endpoints", []) or features.get("api_routes", []) or []
            for ep in endpoints:
                method = (ep.get("verb") or ep.get("method") or "GET").upper()
                if method == "USE":
                    continue
                all_endpoints.append({
                    "method": method,
                    "path": ep.get("route") or ep.get("path") or "",
                    "source_file": path,
                    "line": ep.get("line", 0),
                })

            for dep in features.get("dependencies", []):
                if dep.startswith((".", "..")):
                    file_dir = os.path.dirname(path)
                    norm_dep = os.path.normpath(os.path.join(file_dir, dep)).replace("\\", "/")
                    all_packages.add(norm_dep)
                else:
                    all_packages.add(dep)

            if schema_tags:
                database_models.append({"file": path, "tags": schema_tags})

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

        # Sort baselines root elements for determinism
        all_endpoints = sorted(all_endpoints, key=lambda ep: (ep.get("method", ""), ep.get("path", "")))
        database_models = sorted(database_models, key=lambda m: m.get("file", ""))
        changes = sorted(changes, key=lambda c: c.get("file", ""))

        return {
            "metadata": metadata,
            "repo_name": repo_name,
            "commit_sha": commit_sha,
            "project_id": project_id,
            "all_file_paths": all_file_paths,
            "read_file": read_file,
            "changes": changes,
            "all_endpoints": all_endpoints,
            "all_packages": all_packages,
            "severity_counts": severity_counts,
            "database_models": database_models,
            "file_features": file_features,
            "file_schema_tags": file_schema_tags,
            "git_manager": git_mgr,
        }
