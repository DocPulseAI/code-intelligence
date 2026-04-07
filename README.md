# Code Change Detector

Automated code change analysis tool that generates detailed impact reports from Git repositories.

## Features

- **Smart Change Detection** - Analyzes code changes using Git diffs and AST parsing
- **GitHub Integration** - Works with both local repos and remote GitHub repositories
- **Token Authentication** - Secure access to private repositories
- **Detailed Analysis** - Extracts functions, components, APIs, and dependencies
- **Severity Scoring** - Automatic severity assessment (PATCH/MINOR/MAJOR)
- **Breaking Change Detection** - Identifies potential breaking changes
- **Complexity Metrics** - Calculates code complexity scores

## Usage

### Analyze Local Repository
```bash
python codeDetect/main.py /path/to/local/repo
```

### Analyze GitHub Repository

```bash
export GITHUB_TOKEN=your_github_personal_access_token
python codeDetect/main.py https://github.com/owner/repo
```

### Start API Service
```bash
python codeDetect/api.py
```
The API will be available at `http://localhost:5000`.

## Runtime Modes

- **CLI**: `python codeDetect/main.py <repo_url_or_local_path> [github_token] [branch] [--new-user]`
- **HTTP API**: `python codeDetect/api.py` for EPIC-1 service integration.

## Output

The tool generates `codeDetect/impact_report.json` with detailed analysis of the changes, including:
- Metadata about the commit and repository.
- Summary of analysis (total files, highest severity).
- Detailed changes per file with extracted features and complexity scores.

## Supported Languages

- JavaScript/TypeScript (.js, .jsx, .ts, .tsx)
- Java (.java)
- Python (.py)

## API Endpoints

The service provides REST API endpoints for analysis:

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/health/dependencies` | Runtime dependency and process-pool status |
| POST | `/analyze` | Analyze GitHub repository |
| POST | `/analyze/local` | Analyze local repository |

**Example API Request:**
```bash
curl -X POST http://localhost:5000/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/owner/repo",
    "branch": "main"
  }'
```

For requirements, please install dependencies:
```bash
pip install -r codeDetect/requirements.txt
```

## Observability

EPIC-1 API emits structured JSON logs with:

- request lifecycle (`EPIC1_HTTP_REQUEST_START` / `EPIC1_HTTP_REQUEST_END`)
- subprocess execution metrics for analysis calls (return code, duration, stderr preview)
- startup dependency diagnostics (`EPIC1_DEPENDENCY_CHECK`)

Every response includes an `X-Request-Id` header so API calls can be correlated end-to-end.

### Logging Controls

- `EPIC1_LOG_BODY_MAX_CHARS` (default `1200`): max stderr/body preview length included in logs.

## Troubleshooting

1. `GET /health/dependencies` returns `503` when runtime dependencies are degraded.
2. For failed analysis responses, check `stage`, `details`, and `retry_possible` in the API payload.
3. Match API failures with logs using `X-Request-Id` to identify the exact subprocess failure and stderr preview.
