"""
Code Change Detector - CLI Entry Point (Refactored)
"""

import os
import sys
import logging
from pipeline.analysis_pipeline import AnalysisPipeline
from services.intelligence_service import _build_api_contract_endpoints
from services.repository_analysis_service import _looks_like_github_token

LOG = logging.getLogger("epic1.cli")
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def main():
    args = sys.argv[1:]
    if len(args) < 1:
        LOG.error("Usage: python main.py <repo_url_or_path> [branch] [--new-user]")
        sys.exit(1)

    repo_input = args[0]
    branch = "main"
    new_user = False

    github_token = os.environ.pop("GITHUB_TOKEN_CURRENT_REQUEST", None) or os.environ.get("GITHUB_TOKEN") or None

    positional_args = []
    for arg in args[1:]:
        low = arg.lower()
        if low in {"--new-user", "--new-user=true", "--new-user=1", "--new-user=yes", "--new-user=y", "--new-user=on"}:
            new_user = True
            continue
        if low in {"--new-user=false", "--new-user=0", "--new-user=no", "--new-user=n", "--new-user=off"}:
            new_user = False
            continue
        if arg.startswith("--"):
            continue
        positional_args.append(arg)

    if positional_args:
        branch = positional_args[0]

    pipeline = AnalysisPipeline(
        repo_input=repo_input,
        branch=branch,
        new_user=new_user,
        github_token=github_token
    )
    sys.exit(pipeline.run())


if __name__ == "__main__":
    main()
