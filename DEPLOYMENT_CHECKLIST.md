# Deployment Lessons Learned - Epic-1 Deployment Issues

## Critical Issues Encountered & Fixes

### 1. **Flask Startup Decorator Incompatibility**
**Issue:** `AttributeError: 'Flask' object has no attribute 'before_serving'`
- Gunicorn worker boot failure
- `@app.before_serving` and `@app.after_serving` not supported in Flask 3.1.0

**Root Cause:** Using decorators that don't exist in the Flask version

**Fix:**
```python
# ❌ WRONG
@app.before_serving
def start_listener():
    runner.start()

@app.after_serving
def stop_listener():
    runner.stop()

# ✅ CORRECT
_listener_started = False

def start_listener() -> None:
    global _listener_started
    if _listener_started:
        return
    runner.start()
    _listener_started = True

@app.before_request
def ensure_listener_started() -> None:
    start_listener()
```

**Applied To:**
- `main.py`
- `api.py`

---

### 2. **Mismatched Entry Point (main:app vs api:app)**
**Issue:** `ModuleNotFoundError: No module named 'application'`
- Dockerfile pointed to `main:app`
- Procfile pointed to `api:app`
- Azure deployed using wrong entrypoint

**Root Cause:** Configuration inconsistency between deployment files

**Fix:**
- Updated Dockerfile: `CMD ["gunicorn", "-c", "gunicorn.conf.py", "api:app"]`
- Aligned all entry points to use `api.py` (the REST API)
- Integrated Service Bus listener into `api.py` with background runner

---

### 3. **Missing Workflow Action Parameters**
**Issue:** `Unexpected input(s) '_dockerfilePathKey_', '_targetLabelKey_', '_buildArgumentsKey_'`
- Azure deploy action rejected placeholder parameters
- Workflow had template placeholders not replaced

**Root Cause:** Azure auto-generated workflow template wasn't customized

**Fix:**
```yaml
# ❌ WRONG
with:
  appSourcePath: ${{ github.workspace }}
  _dockerfilePathKey_: _dockerfilePath_
  _targetLabelKey_: _targetLabel_
  _buildArgumentsKey_: _buildArgumentsValues_

# ✅ CORRECT
with:
  appSourcePath: ${{ github.workspace }}
  dockerfilePath: codeDetect/Dockerfile
  registryUrl: docpulseresgistry.azurecr.io
  registryUsername: ${{ secrets.CODEDETECT_REGISTRY_USERNAME }}
  registryPassword: ${{ secrets.CODEDETECT_REGISTRY_PASSWORD }}
```

---

### 4. **Incorrect Docker Build Context Paths**
**Issue:** `ERROR: failed to solve: failed to compute cache key: ..."/requirements.txt": not found`
- Dockerfile used paths relative to codeDetect/
- Build context was workspace root
- COPY commands missing `codeDetect/` prefix

**Root Cause:** Dockerfile written for local builds, not workspace root context

**Fix:**
```dockerfile
# ❌ WRONG
COPY requirements.txt .
COPY . .

# ✅ CORRECT
COPY codeDetect/requirements.txt .
COPY codeDetect/ .
```

---

### 5. **Empty CI Workflow File**
**Issue:** `.github/workflows/ci-docs.yml` was empty (only whitespace)
- No CI jobs executed
- "No jobs were run" warning

**Root Cause:** File created but not populated

**Fix:** Added comprehensive CI pipeline with:
- Lint & Test (flake8, pylint, pytest)
- Docker Build validation
- Startup validation (Flask decorator checks)
- Summary reporting

---

### 6. **Missing imageToDeploy Parameter in Azure Deploy Action**
**Issue:** `MANIFEST_UNKNOWN: manifest tagged by "SHA-HASH" is not found`
- Azure deploy action built image but didn't push it to ACR
- Tried to deploy non-existent SHA-tagged image
- Deployment failed with "image not found"

**Root Cause:** Missing `imageToDeploy` parameter - action generated SHA tag but didn't know which tag to actually deploy

**Fix:**
```yaml
# ❌ WRONG
with:
  imageToBuild: docpulseresgistry.azurecr.io/code-detect:latest
  # Missing: imageToDeploy (action used internal SHA hash by default)

# ✅ CORRECT
with:
  imageToBuild: docpulseresgistry.azurecr.io/code-detect:latest
  imageToDeploy: docpulseresgistry.azurecr.io/code-detect:latest  # Explicitly deploy latest tag
```

**Key Learning:** Both parameters needed:
- `imageToBuild` = what to name the image after building
- `imageToDeploy` = which image tag to actually deploy to container app

---

## Deployment Files Configuration

### Files That Must Match:
| File | Purpose | Current Value |
|------|---------|--------|
| `Dockerfile` | Container build | `CMD ["gunicorn", "-c", "gunicorn.conf.py", "api:app"]` |
| `Procfile` | Render/local dev | `web: gunicorn -c gunicorn.conf.py api:app` |
| `gunicorn.conf.py` | Worker config | Reads PORT, GUNICORN_* env vars |
| `.github/workflows/*.yml` | CI/CD automation | Correct action parameters |

### Environment Variables Required:
- `SERVICE_BUS_CONNECTION_STRING` - Azure Service Bus connection
- `EPIC1_QUEUE_NAME` - Input queue (default: code-detect-q)
- `NEXT_QUEUE_NAME` - Output queue (default: docs-gen-q)
- `PORT` - Server port (default: 5000)

---

## Pre-Deployment Checklist for Future Epics

### Before Pushing to Epic Branch:
- [ ] **Flask Routes:** Use `@app.get()`, `@app.post()` decorators (not legacy decorators)
- [ ] **Flask Startup:** Use `@app.before_request` if startup code needed (NOT `before_serving`)
- [ ] **Entry Point:** Ensure one clear entry file (e.g., `api.py`) with `app = Flask(__name__)`
- [ ] **Imports:** Verify all imports resolve (test locally: `python -c "import main"`)
- [ ] **Dockerfile Paths:**
  - If build context is workspace root, prefix COPY with `epicFolder/`
  - If build context is epicFolder, use relative paths
- [ ] **Procfile:** Must match Dockerfile CMD app entry point
- [ ] **GitHub Workflows:**
  - Remove placeholder parameters (anything with `_key_` or `_values_`)
  - Validate input names against action documentation
  - Use real file paths, not placeholders
  - **Azure Deploy Action:** Include both `imageToBuild` AND `imageToDeploy` with matching image tags
  - Test workflow syntax before committing
- [ ] **Environment Config:**
  - Verify `.env` has real credentials (not `replace-me` placeholders)
  - Document required env vars in README

### Docker Build Testing:
```bash
# Test locally before pushing
cd /path/to/epic
docker build -f Dockerfile -t test:local .
docker run -it test:local bash
python -c "import api; print(api.app)" # Should succeed
```

### Syntax Validation:
```bash
# Check Python syntax before commit
python -m py_compile main.py api.py service_bus.py
pylint --errors-only *.py src/

# Check YAML syntax
python -m yaml .github/workflows/*.yml
```

---

## Common Azure Deployment Workflow Issues

### Issue: Build succeeds, but container won't start
**Checklist:**
- [ ] Entry point exists and is correct: `api:app` matches `api.py` with `app = Flask(...)`
- [ ] All imports in entry file are available in container
- [ ] No unsupported Flask decorators (@before_serving, @after_serving)
- [ ] Environment variables are set in Azure Container Apps config

### Issue: Docker build fails with file not found
**Checklist:**
- [ ] Build context in workflow matches appSourcePath
- [ ] COPY commands use correct relative paths from build context
- [ ] No typos in filenames
- [ ] Test with: `docker build -f path/to/Dockerfile .`

### Issue: Service Bus not connecting
**Checklist:**
- [ ] SERVICE_BUS_CONNECTION_STRING has real key (not `replace-me`)
- [ ] SharedAccessKey in connection string is valid
- [ ] Queue names match (EPIC1_QUEUE_NAME, NEXT_QUEUE_NAME)
- [ ] Listener starts without errors: check logs for `background_runner_started`

---

## Testing & Validation

### Local Testing:
```bash
# 1. Install dependencies
pip install -r codeDetect/requirements.txt

# 2. Test Flask app imports
python -c "from codeDetect.api import app; print('✓ API loads')"

# 3. Run pytest
cd codeDetect && pytest tests/ -v

# 4. Test Docker build
docker build -f codeDetect/Dockerfile -t local-test:latest .

# 5. Test container startup
docker run --rm -e PYTHONUNBUFFERED=1 local-test:latest \
  gunicorn -c gunicorn.conf.py api:app --check-config
```

### Production Validation:
```bash
# After deployment, test endpoints
curl https://your-container-app.azurecontainerapps.io/health
curl https://your-container-app.azurecontainerapps.io/

# Check logs in Azure Portal:
# Container Apps → code-detect → Revision & replicas → View logs
```

---

## Summary of Fixes Applied

| Commit | Issue | Fix |
|--------|-------|-----|
| 906838b | Flask startup | @app.before_request hook |
| 4d36a8b | Empty CI workflow | Added comprehensive pipeline |
| 6655078 | main:app vs api:app | Aligned to api:app, integrated listener |
| 866a62d | Invalid workflow params | Removed placeholders, added real params |
| 86cd346 | SHA tag | Changed to latest tag |
| 9745a3c | Dockerfile paths | Added codeDetect/ prefix to COPY |
| 1c24ffa | Documentation | Added DEPLOYMENT_CHECKLIST.md |
| 8cbbad5 | Missing imageToDeploy | Added explicit image deploy tag |

---

## Future Epic Deployment Process

1. **Development**: Work in feature branch
2. **Testing**: Run local Docker build + pytest
3. **Pre-Commit Validation**:
   ```bash
   python -m py_compile codeDetect/*.py
   docker build -f codeDetect/Dockerfile -t test:latest .
   ```
4. **Create Commit**: Include only necessary changes
5. **Push to Epic Branch**: Triggers GitHub Actions
6. **Monitor Workflow**: Check Actions tab for build/deploy progress
7. **Validate Deployment**: Test live endpoints
8. **Document Issues**: Add to this checklist if new issues arise

---

**Last Updated:** March 4, 2026
**Applicable To:** Epic-1 and subsequent epics
