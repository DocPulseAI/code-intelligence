"""
Python event schema unit tests for all event contracts.

Tests that:
- All Pydantic models validate correct payloads
- All models reject invalid payloads with clear errors
- EventEnvelope validates correctly
- build_envelope auto-generates required fields
- is_compatible_version works correctly
- parse_payload dispatches to the right model
"""
import pytest
import uuid
from worker.events.schemas import (
    EventEnvelope,
    build_envelope,
    parse_payload,
    is_compatible_version,
    EVENT_VERSION,
    ANALYSIS_REQUESTED,
    EPIC1_COMPLETED,
    EPIC2_COMPLETED,
    EPIC3_COMPLETED,
    EPIC4_COMPLETED,
    ANALYSIS_FAILED,
    QUEUE_EPIC1,
    QUEUE_EPIC2,
    QUEUE_EPIC3,
    QUEUE_EPIC4,
    QUEUE_EPIC1_DLQ,
    QUEUE_EPIC2_DLQ,
    QUEUE_EPIC3_DLQ,
    QUEUE_EPIC4_DLQ,
    AnalysisRequestedPayload,
    Epic1CompletedPayload,
    Epic2CompletedPayload,
    Epic3CompletedPayload,
    Epic4CompletedPayload,
    AnalysisFailedPayload,
)

VALID_UUID = str(uuid.uuid4())
VALID_UUID2 = str(uuid.uuid4())
VALID_SHA = "deadbeef123"
VALID_URL = "https://github.com/org/repo"


# ─── Queue Name Constants ────────────────────────────────────────────────────

class TestQueueNames:
    def test_epic1_queue(self):
        assert QUEUE_EPIC1 == "analysis-jobs-epic1-queue"

    def test_epic2_queue(self):
        assert QUEUE_EPIC2 == "analysis-jobs-epic2-queue"

    def test_epic3_queue(self):
        assert QUEUE_EPIC3 == "analysis-jobs-epic3-queue"

    def test_epic4_queue(self):
        assert QUEUE_EPIC4 == "analysis-jobs-epic4-queue"

    def test_dlq_names(self):
        assert QUEUE_EPIC1_DLQ == "analysis-jobs-epic1-dlq"
        assert QUEUE_EPIC2_DLQ == "analysis-jobs-epic2-dlq"
        assert QUEUE_EPIC3_DLQ == "analysis-jobs-epic3-dlq"
        assert QUEUE_EPIC4_DLQ == "analysis-jobs-epic4-dlq"


# ─── EventEnvelope ────────────────────────────────────────────────────────────

class TestEventEnvelope:
    def _valid(self) -> dict:
        return {
            "eventId": str(uuid.uuid4()),
            "eventType": "AnalysisRequested",
            "eventVersion": 1,
            "correlationId": str(uuid.uuid4()),
            "traceId": str(uuid.uuid4()),
            "timestamp": "2026-06-17T12:00:00+00:00",
            "payload": {"runId": VALID_UUID},
        }

    def test_validates_correct_envelope(self):
        env = EventEnvelope.model_validate(self._valid())
        assert env.eventType == "AnalysisRequested"
        assert env.eventVersion == 1

    def test_rejects_missing_event_id(self):
        data = self._valid()
        del data["eventId"]
        with pytest.raises(Exception):
            EventEnvelope.model_validate(data)

    def test_rejects_negative_event_version(self):
        data = {**self._valid(), "eventVersion": 0}
        with pytest.raises(Exception):
            EventEnvelope.model_validate(data)

    def test_rejects_missing_event_type(self):
        data = self._valid()
        del data["eventType"]
        with pytest.raises(Exception):
            EventEnvelope.model_validate(data)


# ─── build_envelope ───────────────────────────────────────────────────────────

class TestBuildEnvelope:
    def test_auto_generates_event_id(self):
        env = build_envelope("Test", 1, {"x": 1})
        assert env.eventId
        assert len(env.eventId) >= 8

    def test_auto_generates_correlation_id(self):
        env = build_envelope("Test", 1, {})
        assert env.correlationId

    def test_propagates_correlation_id(self):
        cid = str(uuid.uuid4())
        env = build_envelope("Test", 1, {}, correlation_id=cid)
        assert env.correlationId == cid

    def test_propagates_trace_id(self):
        tid = "trace-xyz-123"
        env = build_envelope("Test", 1, {}, trace_id=tid)
        assert env.traceId == tid

    def test_sets_timestamp(self):
        env = build_envelope("Test", 1, {})
        assert env.timestamp
        assert "T" in env.timestamp  # ISO 8601

    def test_unique_event_ids_across_calls(self):
        ids = {build_envelope("Test", 1, {}).eventId for _ in range(10)}
        assert len(ids) == 10  # all unique


# ─── Version Compatibility ────────────────────────────────────────────────────

class TestVersionCompatibility:
    def test_current_version_is_compatible(self):
        assert is_compatible_version(EVENT_VERSION) is True

    def test_v2_is_not_compatible_with_v1_consumer(self):
        assert is_compatible_version(2) is False

    def test_v0_is_not_compatible(self):
        assert is_compatible_version(0) is False


# ─── AnalysisRequested ───────────────────────────────────────────────────────

class TestAnalysisRequestedPayload:
    def _valid(self) -> dict:
        return {
            "runId": VALID_UUID,
            "projectId": VALID_UUID2,
            "githubUrl": VALID_URL,
            "branch": "main",
            "commitSha": VALID_SHA,
            "refName": "main",
            "refType": "default_branch",
            "triggerType": "manual",
            "isPreview": False,
            "newUser": False,
        }

    def test_validates_correct_payload(self):
        p = AnalysisRequestedPayload.model_validate(self._valid())
        assert p.runId == VALID_UUID
        assert p.branch == "main"

    def test_defaults_is_preview_and_new_user(self):
        data = {**self._valid()}
        del data["isPreview"]
        del data["newUser"]
        p = AnalysisRequestedPayload.model_validate(data)
        assert p.isPreview is False
        assert p.newUser is False

    def test_rejects_invalid_trigger_type(self):
        with pytest.raises(Exception):
            AnalysisRequestedPayload.model_validate({**self._valid(), "triggerType": "invalid"})

    def test_event_type_constant(self):
        assert ANALYSIS_REQUESTED == "AnalysisRequested"


# ─── Epic1Completed ──────────────────────────────────────────────────────────

class TestEpic1CompletedPayload:
    def _valid(self) -> dict:
        return {
            "runId": VALID_UUID,
            "projectId": VALID_UUID2,
            "branch": "main",
            "commitSha": VALID_SHA,
            "refName": "main",
            "refType": "default_branch",
            "triggerType": "manual",
            "isPreview": False,
            "githubUrl": VALID_URL,
            "impactReport": {"changes": []},
        }

    def test_validates_correct_payload(self):
        p = Epic1CompletedPayload.model_validate(self._valid())
        assert p.impactReport == {"changes": []}

    def test_rejects_missing_impact_report(self):
        data = {**self._valid()}
        del data["impactReport"]
        # impactReport has a default_factory, so it should not fail
        p = Epic1CompletedPayload.model_validate(data)
        assert p.impactReport == {}

    def test_event_type_constant(self):
        assert EPIC1_COMPLETED == "Epic1Completed"


# ─── Epic2Completed ──────────────────────────────────────────────────────────

class TestEpic2CompletedPayload:
    def test_validates_correct_payload(self):
        p = Epic2CompletedPayload.model_validate({
            "runId": VALID_UUID, "projectId": VALID_UUID2,
            "branch": "main", "commitSha": VALID_SHA,
            "refName": "main", "refType": "default_branch",
            "triggerType": "manual", "isPreview": False,
            "githubUrl": VALID_URL,
            "impactReport": {}, "docSnapshot": {"generated_files": []},
            "artifactManifest": {"artifact_count": 2},
        })
        assert p.docSnapshot["generated_files"] == []

    def test_event_type_constant(self):
        assert EPIC2_COMPLETED == "Epic2Completed"


# ─── Epic3Completed ──────────────────────────────────────────────────────────

class TestEpic3CompletedPayload:
    def test_validates_with_drift_succeeded_false(self):
        p = Epic3CompletedPayload.model_validate({
            "runId": VALID_UUID, "projectId": VALID_UUID2,
            "branch": "main", "commitSha": VALID_SHA,
            "refName": "main", "refType": "default_branch",
            "triggerType": "manual", "isPreview": False,
            "githubUrl": VALID_URL,
            "impactReport": {}, "docSnapshot": {}, "artifactManifest": {},
            "driftReport": {"drift_detected": False}, "driftSucceeded": False,
        })
        assert p.driftSucceeded is False

    def test_defaults_drift_succeeded_to_false(self):
        data = {
            "runId": VALID_UUID, "projectId": VALID_UUID2,
            "branch": "main", "commitSha": VALID_SHA,
            "refName": "main", "refType": "default_branch",
            "triggerType": "manual", "isPreview": False,
            "githubUrl": VALID_URL,
            "impactReport": {}, "docSnapshot": {}, "artifactManifest": {},
            "driftReport": {},
        }
        p = Epic3CompletedPayload.model_validate(data)
        assert p.driftSucceeded is False

    def test_event_type_constant(self):
        assert EPIC3_COMPLETED == "Epic3Completed"


# ─── Epic4Completed ──────────────────────────────────────────────────────────

class TestEpic4CompletedPayload:
    def test_validates_correct_payload(self):
        p = Epic4CompletedPayload.model_validate({
            "runId": VALID_UUID, "projectId": VALID_UUID2,
            "branch": "main", "commitSha": VALID_SHA,
            "refName": "main", "refType": "default_branch",
            "triggerType": "manual", "isPreview": False,
            "manifestValidated": True,
        })
        assert p.manifestValidated is True

    def test_event_type_constant(self):
        assert EPIC4_COMPLETED == "Epic4Completed"


# ─── AnalysisFailed ──────────────────────────────────────────────────────────

class TestAnalysisFailedPayload:
    def test_validates_correct_payload(self):
        p = AnalysisFailedPayload.model_validate({
            "runId": VALID_UUID, "projectId": VALID_UUID2,
            "failedStage": "epic2",
            "errorMessage": "HTTP 502: Bad Gateway",
            "retryCount": 3,
        })
        assert p.failedStage == "epic2"
        assert p.retryCount == 3

    def test_rejects_invalid_failed_stage(self):
        with pytest.raises(Exception):
            AnalysisFailedPayload.model_validate({
                "runId": VALID_UUID, "projectId": VALID_UUID2,
                "failedStage": "epic5",
                "errorMessage": "error",
                "retryCount": 0,
            })

    def test_rejects_negative_retry_count(self):
        with pytest.raises(Exception):
            AnalysisFailedPayload.model_validate({
                "runId": VALID_UUID, "projectId": VALID_UUID2,
                "failedStage": "epic1",
                "errorMessage": "error",
                "retryCount": -1,
            })

    def test_rejects_empty_error_message(self):
        with pytest.raises(Exception):
            AnalysisFailedPayload.model_validate({
                "runId": VALID_UUID, "projectId": VALID_UUID2,
                "failedStage": "epic1",
                "errorMessage": "",
                "retryCount": 0,
            })

    def test_event_type_constant(self):
        assert ANALYSIS_FAILED == "AnalysisFailed"


# ─── parse_payload dispatcher ────────────────────────────────────────────────

class TestParsePayload:
    def test_dispatches_analysis_requested(self):
        p = parse_payload(ANALYSIS_REQUESTED, {
            "runId": VALID_UUID, "projectId": VALID_UUID2,
            "githubUrl": VALID_URL, "branch": "main",
            "commitSha": VALID_SHA, "refName": "main",
            "refType": "default_branch", "triggerType": "manual",
        })
        assert isinstance(p, AnalysisRequestedPayload)

    def test_dispatches_epic1_completed(self):
        p = parse_payload(EPIC1_COMPLETED, {
            "runId": VALID_UUID, "projectId": VALID_UUID2,
            "branch": "main", "commitSha": VALID_SHA,
            "refName": "main", "refType": "default_branch",
            "triggerType": "manual", "isPreview": False,
            "githubUrl": VALID_URL,
        })
        assert isinstance(p, Epic1CompletedPayload)

    def test_raises_for_unknown_event_type(self):
        with pytest.raises(ValueError, match="Unknown event type"):
            parse_payload("UnknownEvent", {})
