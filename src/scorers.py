"""
Impact scoring module for code change analysis.
US-17: Severity Scoring
"""

from typing import Dict, List, Any, Optional


class SeverityCalculator:
    """
    Severity Levels:
    - MAJOR: Breaking changes (API, schema, contracts)
    - MINOR: Functional changes (new features, logic changes)
    - PATCH: Non-breaking changes (styling, docs, refactoring)
    """

    # Annotations/patterns that indicate MAJOR changes
    MAJOR_PATTERNS = {
        'java': [
            '@RestController', '@Controller', '@RequestMapping',
            '@GetMapping', '@PostMapping', '@PutMapping', '@DeleteMapping',
            '@Entity', '@Table', '@Column'
        ],
        'python': [
            '@app.route', '@router.', '@api.', '@blueprint.'
        ],
        'javascript': [
            'app.get', 'app.post', 'router.get', 'router.post'
        ]
    }

    # File patterns that indicate critical changes
    CRITICAL_FILES = [
        'pom.xml', 'build.gradle', 'package.json', 'requirements.txt',
        'Dockerfile', 'docker-compose', 'application.properties',
        'application.yml', '.env', 'schema', 'migration'
    ]

    @staticmethod
    def assess(ext: str, features: Dict[str, Any],
               schema_tags: List[str],
               config: Optional[Dict] = None) -> str:
        """
        Assess the severity level of a code change.

        Args:
            ext: File extension
            features: Extracted code features
            schema_tags: Schema-related tags (e.g., JPA_ENTITY)
            config: Optional configuration dictionary

        Returns:
            Severity level: "MAJOR", "MINOR", or "PATCH"
        """
        # Schema changes are always MAJOR
        if schema_tags:
            return "MAJOR"

        # Check for API endpoints
        if features.get("api_endpoints") or features.get("api_routes"):
            return "MAJOR"

        # 3. Check schema annotations
        if features.get("schema_annotations"):
            return "MAJOR"

        # 4. Language-specific checks
        if ext == '.java':
            return SeverityCalculator._assess_java(features)

        elif ext in ['.ts', '.tsx', '.js', '.jsx']:
            return SeverityCalculator._assess_javascript(features)

        elif ext == '.py':
            return SeverityCalculator._assess_python(features)

        elif ext in ['.sql']:
            return "MAJOR"  # SQL changes are always significant

        elif ext in ['.yaml', '.yml', '.json', '.properties', '.env']:
            return SeverityCalculator._assess_config(features, ext)

        # 5. Default to PATCH for unknown types
        return "PATCH"

    @staticmethod
    def _assess_java(features: Dict[str, Any]) -> str:
        """Assess severity for Java files."""
        annotations = features.get("annotations", [])

        # Check for Spring controller annotations
        for ann in annotations:
            if any(pattern in ann for pattern in SeverityCalculator.MAJOR_PATTERNS['java']):
                return "MAJOR"

        # Check for significant method changes
        methods = features.get("methods", [])
        if len(methods) > 3:
            return "MINOR"

        return "PATCH" if not methods else "MINOR"

    @staticmethod
    def _assess_javascript(features: Dict[str, Any]) -> str:
        """Assess severity for JavaScript/TypeScript files."""
        # Function changes indicate logic modifications
        functions = features.get("functions", [])
        if len(functions) > 2:
            return "MINOR"

        # Default styling/text changes
        return "PATCH" if not functions else "MINOR"

    @staticmethod
    def _assess_python(features: Dict[str, Any]) -> str:
        """Assess severity for Python files."""
        decorators = features.get("decorators", [])

        # Check for API decorators
        for dec in decorators:
            if any(pattern in dec for pattern in ['route', 'api', 'endpoint']):
                return "MAJOR"

        # Check for significant function/class changes
        functions = features.get("functions", [])
        classes = features.get("classes", [])

        if classes:
            return "MINOR"

        if len(functions) > 2:
            return "MINOR"

        return "PATCH"

    @staticmethod
    def _assess_config(features: Dict[str, Any], ext: str) -> str:
        """Assess severity for configuration files."""
        # Configuration changes can have wide impact
        return "MINOR"

    @staticmethod
    def get_severity_reason(severity: str, features: Dict, schema_tags: List) -> str:
        """Generate human-readable reason for severity assignment."""
        reasons = []

        if schema_tags:
            reasons.append(f"Schema changes detected: {', '.join(schema_tags)}")

        if features.get("api_endpoints") or features.get("api_routes"):
            reasons.append("API endpoint modifications")

        if features.get("methods"):
            reasons.append(f"Methods: {len(features['methods'])} modified")

        if features.get("functions"):
            reasons.append(f"Functions: {len(features['functions'])} modified")

        return "; ".join(reasons) if reasons else "General code changes"