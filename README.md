# Code Intelligence Module

This module houses the AST parsing, Git repository analytics, and code intelligence engines of the DocPulseAI organization. The primary deployable service within this module is **codeDetect**.

## Purpose
- **Business Purpose**: Support automated code documentation pipeline (DocPulseAI) by analyzing Git repositories and extracting precise code change footprints.
- **Technical Purpose**: Host the source libraries and container definitions for the code change intelligence component, linking repository diffs to AST syntax checks.

## Structure and Workspace Mapping
This module contains:
1. **codeDetect** (sub-folder): The Flask API service and command line executor.
2. **Dockerfile**: The containerization file configured with Git and Python dependencies.
3. **render.yaml**: Infrastructure-as-code declaration for deployment.

For detailed information on local setup, CLI usage, endpoint schemas, environment configuration, and test suites, please see the sub-service README:
👉 **[codeDetect Service README (codeDetect/README.md)](file:///Users/kireeti/Desktop/Projects/DocPulseAI/code-intelligence/codeDetect/README.md)**

## Component Matrix

| Sub-Component | Tech Stack | Target Location | Purpose |
|---|---|---|---|
| **codeDetect Engine** | Python 3.11, Tree-sitter | `codeDetect/` | Core AST extraction & Git diff parsing |
| **Gunicorn Daemon** | Python WSGI | `codeDetect/gunicorn.conf.py` | Web server gateway execution |

---

## Deployment & Docker Integration
The root Dockerfile in this directory compiles `codeDetect` into a production-ready container:
```bash
# From code-intelligence/ directory
docker build -t docpulse/codedetect -f Dockerfile .
```

*Note: Ensure that any Docker build commands use the root `code-intelligence` directory as the build context, rather than the `codeDetect` sub-directory, to maintain correct module inclusion paths.*
