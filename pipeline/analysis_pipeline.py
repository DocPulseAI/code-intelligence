import os
import sys
import logging
import json
from services.repository_analysis_service import RepositoryAnalysisService
from services.intelligence_service import IntelligenceService
from services.report_generation_service import ReportGenerationService

LOG = logging.getLogger("epic1.cli")

class AnalysisPipeline:
    def __init__(self, repo_input: str, branch: str, new_user: bool, github_token: str | None = None):
        self.repo_input = repo_input
        self.branch = branch
        self.new_user = new_user
        self.github_token = github_token

    def run(self) -> int:
        analysis_service = RepositoryAnalysisService(
            self.repo_input, self.github_token, self.branch, self.new_user
        )
        intel_service = IntelligenceService()
        report_service = ReportGenerationService()

        raw_analysis = None
        try:
            raw_analysis = analysis_service.analyze()
            raw_analysis["branch"] = self.branch

            LOG.info("Building intelligence layers...")
            intelligence = intel_service.build_intelligence_layers(raw_analysis)

            LOG.info("Generating final report...")
            report = report_service.generate_report(raw_analysis, intelligence, self.new_user)

            LOG.info("Validating report integrity...")
            report_service.validate_impact_report(report)

            output_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "impact_report.json")
            report_service.write_and_print_report(report, output_path)
            return 0

        except Exception as e:
            LOG.error(f"Analysis failed: {str(e)}", exc_info=True)
            error_report = report_service.generate_error_report(e, self.repo_input, self.branch)
            output_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "impact_report.json")
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(error_report, f, indent=2, ensure_ascii=True)
            print(json.dumps(error_report, ensure_ascii=True, separators=(",", ":")))
            return 1

        finally:
            if raw_analysis and "git_manager" in raw_analysis:
                raw_analysis["git_manager"].cleanup()
