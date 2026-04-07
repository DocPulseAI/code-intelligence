"""
File filtering utilities for code change detection.
"""

import os
import mimetypes
import fnmatch
import re
from typing import List, Dict, Optional
import yaml


class ConfigLoader:
    """
    Loads configuration from YAML file.
    """

    @staticmethod
    def load(config_path: str) -> Dict:
        """Load configuration from YAML file."""
        default_config = {
            "ignore_patterns": [],
            "critical_paths": [],
            "api_patterns": {},
            "schema_patterns": {}
        }

        if not config_path or not os.path.exists(config_path):
            return default_config

        try:
            with open(config_path, 'r') as f:
                loaded = yaml.safe_load(f)
                if loaded:
                    default_config.update(loaded)
        except Exception as e:
            print(f"Warning: Could not load config: {e}")

        return default_config


class FileFilter:
    """
    Filters files based on patterns and safety checks.
    """

    IGNORED_EXTENSIONS = {
        '.png', '.jpg', '.jpeg', '.gif', '.ico', '.svg', '.webp',
        '.pyc', '.pyo', '.pyd',
        '.log', '.tmp', '.temp',
        '.class', '.jar', '.war', '.ear',
        '.dll', '.exe', '.so', '.dylib',
        '.zip', '.gz', '.tar', '.rar', '.7z',
        '.pdf', '.doc', '.docx', '.xls', '.xlsx',
        '.woff', '.woff2', '.ttf', '.eot', '.otf',
        '.mp3', '.mp4', '.avi', '.mov', '.wav',
        '.db', '.sqlite', '.sqlite3'
    }

    IGNORED_FILES = {
        '.DS_Store',
        'Thumbs.db',
        '.gitignore',
        '.gitattributes',
        'package-lock.json',
        'yarn.lock',
        'Pipfile.lock',
        'poetry.lock'
    }

    IGNORED_DIRS = {
        'dist',
        '__pycache__',
        'node_modules',
        '.git',
        '.idea',
        '.vscode',
        '.settings',
        'target',
        'build',
        'bin',
        'obj',
        '.gradle',
        '.mvn',
        'venv',
        'env',
        '.env',
        'coverage',
        '.nyc_output',
        '.pytest_cache',
        '.tox',
        'htmlcov',
        'eggs',
        '.eggs',
        # Strict non-runtime folders to reduce static-analysis false positives
        'test',
        'tests',
        '__tests__',
        'fixtures',
        'migrations',
        'scripts',
        'examples',
        'docs',
    }

    # Runtime/source roots used by EPIC-1 extraction heuristics.
    SOURCE_ROOT_DIRS = {
        'src',
        'server',
        'backend',
        'app',
        'routes',
        'controllers',
        'models',
    }

    # Root entry files are still valid source files even when outside SOURCE_ROOT_DIRS.
    SOURCE_ENTRY_FILES = {
        'app.js', 'app.ts',
        'server.js', 'server.ts',
        'index.js', 'index.ts',
        'main.js', 'main.ts',
        # Python entry files for FastAPI/Flask projects.
        'app.py', 'server.py', 'index.py', 'main.py',
    }

    ALLOWED_DATA_EXTS = {
        '.json', '.xml', '.yaml', '.yml',
        '.html', '.css', '.scss', '.less',
        '.md', '.txt', '.rst',
        '.sql',
        '.properties',
        '.gitignore', '.dockerignore',
        '.env', '.env.example',
        '.toml', '.ini', '.cfg'
    }

    @staticmethod
    def _matches_ignore_patterns(file_path: str, additional_ignores: Optional[List[str]] = None) -> bool:
        """Check ignore patterns from config and default ignored dirs/files."""
        filename = os.path.basename(file_path)
        parts = file_path.replace('\\', '/').split('/')

        if filename in FileFilter.IGNORED_FILES:
            return True

        if any(part in FileFilter.IGNORED_DIRS for part in parts):
            return True

        if additional_ignores:
            for pattern in additional_ignores:
                if fnmatch.fnmatch(file_path, pattern):
                    return True
                if fnmatch.fnmatch(filename, pattern):
                    return True
                if pattern.endswith('/') and any(part == pattern.rstrip('/') for part in parts):
                    return True

        return False

    @staticmethod
    def should_exclude_from_analysis(file_path: str, additional_ignores: Optional[List[str]] = None) -> bool:
        """Return True when file should be fully excluded from analysis."""
        if FileFilter._matches_ignore_patterns(file_path, additional_ignores):
            return True

        normalized = file_path.replace('\\', '/').lower()
        parts = [p for p in normalized.split('/') if p]
        filename = os.path.basename(normalized)
        ext = os.path.splitext(filename)[1]

        # Exclude known non-runtime trees aggressively.
        if any(part in FileFilter.IGNORED_DIRS for part in parts):
            return True

        # Keep source-like files constrained to source roots or known entry files.
        source_exts = {
            '.java', '.js', '.ts', '.py', '.jsx', '.tsx',
            '.go', '.rs', '.rb', '.php', '.c', '.cpp', '.cs',
        }
        if ext in source_exts:
            in_source_root = any(part in FileFilter.SOURCE_ROOT_DIRS for part in parts[:-1])
            is_entry = filename in FileFilter.SOURCE_ENTRY_FILES
            has_domain_suffix = bool(
                re.search(r"(routes?|controller|service|model|repository)\.(js|ts|jsx|tsx|py|java)$", filename)
            )
            if not in_source_root and not is_entry and not has_domain_suffix:
                return True

        return False

    @staticmethod
    def should_exclude_from_entity_analysis(file_path: str) -> bool:
        """
        Stricter entity/schema filtering than generic file analysis.
        Used by data model extraction to avoid tests/config pollution.
        """
        if FileFilter.should_exclude_from_analysis(file_path):
            return True

        normalized = file_path.replace('\\', '/').lower()
        parts = [p for p in normalized.split('/') if p]
        ext = os.path.splitext(normalized)[1]
        if ext in {'.sql', '.prisma'}:
            return False
        # Entity extraction only from source roots (or root entry files for small repos).
        in_source_root = any(part in FileFilter.SOURCE_ROOT_DIRS for part in parts[:-1])
        is_entry = os.path.basename(normalized) in FileFilter.SOURCE_ENTRY_FILES
        return not (in_source_root or is_entry)

    @staticmethod
    def is_known_binary_extension(file_path: str) -> bool:
        """Return True when file extension is known binary/non-source."""
        ext = os.path.splitext(file_path)[1].lower()
        return ext in FileFilter.IGNORED_EXTENSIONS

    @staticmethod
    def is_safe_to_read(file_path: str, additional_ignores: Optional[List[str]] = None) -> bool:
        """
        Check if file is safe to read and should be analyzed.

        Args:
            file_path: Path to the file
            additional_ignores: Additional patterns from config.yaml

        Returns:
            True if file should be analyzed, False otherwise
        """
        if FileFilter._matches_ignore_patterns(file_path, additional_ignores):
            return False

        # Check extension
        ext = os.path.splitext(file_path)[1].lower()
        if ext in FileFilter.IGNORED_EXTENSIONS:
            return False

        # Allow known data files
        if ext in FileFilter.ALLOWED_DATA_EXTS:
            return True

        # Check MIME type for unknown extensions
        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type:
            if not mime_type.startswith('text'):
                # Allow specific source code files even if MIME is wrong
                source_exts = {'.java', '.js', '.ts', '.py', '.jsx', '.tsx',
                              '.go', '.rs', '.rb', '.php', '.c', '.cpp', '.cs'}
                if ext not in source_exts:
                    return False

        return True

    @staticmethod
    def is_critical_file(file_path: str, critical_paths: Optional[List[str]] = None) -> bool:
        """
        Check if file is a critical configuration file.

        Args:
            file_path: Path to the file
            critical_paths: List of critical file patterns from config

        Returns:
            True if file is critical
        """
        filename = os.path.basename(file_path)

        # Default critical files
        default_critical = {
            'pom.xml', 'build.gradle', 'build.gradle.kts',
            'package.json', 'requirements.txt', 'setup.py', 'pyproject.toml',
            'Dockerfile', 'docker-compose.yml', 'docker-compose.yaml',
            '.env', '.env.example', '.env.production',
            'application.properties', 'application.yml', 'application.yaml'
        }

        if filename in default_critical:
            return True

        # Check custom critical paths
        if critical_paths:
            for pattern in critical_paths:
                if fnmatch.fnmatch(file_path, pattern):
                    return True
                if fnmatch.fnmatch(filename, pattern):
                    return True

        # Check for migration/schema files
        if 'migration' in file_path.lower() or 'schema' in file_path.lower():
            return True

        return False

    @staticmethod
    def categorize_file(file_path: str) -> str:
        """
        Categorize file by type for reporting.

        Returns one of: 'source', 'config', 'test', 'doc', 'asset', 'other'
        """
        filename = os.path.basename(file_path).lower()
        ext = os.path.splitext(file_path)[1].lower()
        path_lower = file_path.lower()

        # Test files
        if 'test' in path_lower or 'spec' in path_lower:
            return 'test'

        # Documentation
        if ext in {'.md', '.rst', '.txt', '.adoc'} or 'doc' in path_lower:
            return 'doc'

        # Configuration
        if ext in {'.json', '.yaml', '.yml', '.xml', '.properties', '.toml', '.ini', '.env'}:
            return 'config'

        # Source code
        if ext in {'.java', '.js', '.ts', '.py', '.jsx', '.tsx', '.go', '.rs', '.rb', '.php', '.c', '.cpp', '.cs'}:
            return 'source'

        # Styles
        if ext in {'.css', '.scss', '.less', '.sass'}:
            return 'style'

        # Assets
        if ext in FileFilter.IGNORED_EXTENSIONS:
            return 'asset'

        return 'other'
