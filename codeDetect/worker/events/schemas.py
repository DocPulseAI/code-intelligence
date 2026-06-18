"""
DocPulseAI — Python Event Schemas (v1)

Mirror of the TypeScript event contracts.
Used by all EPIC service workers to validate inbound and outbound events.

Backward Compatibility:
  - Fields may be ADDED without bumping version.
  - Fields may NEVER be REMOVED or RENAMED in v1.
  - Introduce v2 subclasses for breaking changes.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional
from pydantic import BaseModel, Field, field_validator


# ─── Event Envelope ──────────────────────────────────────────────────────────

class EventEnvelope(BaseModel):
    """
    Generic wrapper for all DocPulseAI events.

    eventId:       Unique per emission — used for idempotency.
    eventType:     Discriminator matching the schema's EVENT_TYPE constant.
    eventVersion:  Schema version. Increment only on breaking changes.
    correlationId: Same for all events in a single analysis run.
    traceId:       Trace identifier for distributed tracing.
    timestamp:     ISO 8601 UTC emission time.
    payload:       Event-specific payload (validated per event type).
    """
    eventId: str = Field(..., description="UUID v4 — unique per emission")
    eventType: str = Field(..., min_length=1)
    eventVersion: int = Field(..., gt=0)
    correlationId: str = Field(..., description="Same for all events in one run")
    traceId: str = Field(..., min_length=1)
    timestamp: str = Field(..., description="ISO 8601 UTC")
    payload: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("eventId", "correlationId")
    @classmethod
    def must_be_uuid_like(cls, v: str) -> str:
        """Basic UUID format check (not strict — allows trace IDs too)."""
        if not v or len(v) < 8:
            raise ValueError(f"Field must be non-empty: {v!r}")
        return v


def build_envelope(
    event_type: str,
    event_version: int,
    payload: Dict[str, Any],
    *,
    correlation_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> EventEnvelope:
    """Build a complete EventEnvelope with auto-generated metadata."""
    return EventEnvelope(
        eventId=str(uuid.uuid4()),
        eventType=event_type,
        eventVersion=event_version,
        correlationId=correlation_id or str(uuid.uuid4()),
        traceId=trace_id or str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc).isoformat(),
        payload=payload,
    )


# ─── Event Type Constants ────────────────────────────────────────────────────

ANALYSIS_REQUESTED = "AnalysisRequested"
EPIC1_COMPLETED = "Epic1Completed"
EPIC2_COMPLETED = "Epic2Completed"
EPIC3_COMPLETED = "Epic3Completed"
EPIC4_COMPLETED = "Epic4Completed"
ANALYSIS_FAILED = "AnalysisFailed"

EVENT_VERSION = 1


# ─── Queue Name Constants ────────────────────────────────────────────────────

QUEUE_EPIC1 = "analysis-jobs-epic1-queue"
QUEUE_EPIC2 = "analysis-jobs-epic2-queue"
QUEUE_EPIC3 = "analysis-jobs-epic3-queue"
QUEUE_EPIC4 = "analysis-jobs-epic4-queue"
QUEUE_EPIC1_DLQ = "analysis-jobs-epic1-dlq"
QUEUE_EPIC2_DLQ = "analysis-jobs-epic2-dlq"
QUEUE_EPIC3_DLQ = "analysis-jobs-epic3-dlq"
QUEUE_EPIC4_DLQ = "analysis-jobs-epic4-dlq"


# ─── Payload Models ──────────────────────────────────────────────────────────

class AnalysisRequestedPayload(BaseModel):
    """Payload for AnalysisRequested event. Triggers Epic1Worker."""
    runId: str
    projectId: str
    githubUrl: str
    branch: str = "main"
    commitSha: str
    refName: str
    refType: str
    triggerType: Literal["manual", "push", "pull_request", "system"] = "manual"
    isPreview: bool = False
    newUser: bool = False


class Epic1CompletedPayload(BaseModel):
    """Payload for Epic1Completed event. Triggers Epic2Worker."""
    runId: str
    projectId: str
    branch: str
    commitSha: str
    refName: str
    refType: str
    triggerType: Literal["manual", "push", "pull_request", "system"] = "manual"
    isPreview: bool = False
    githubUrl: str
    impactReport: Dict[str, Any] = Field(default_factory=dict)


class Epic2CompletedPayload(BaseModel):
    """Payload for Epic2Completed event. Triggers Epic3Worker."""
    runId: str
    projectId: str
    branch: str
    commitSha: str
    refName: str
    refType: str
    triggerType: Literal["manual", "push", "pull_request", "system"] = "manual"
    isPreview: bool = False
    githubUrl: str
    impactReport: Dict[str, Any] = Field(default_factory=dict)
    docSnapshot: Dict[str, Any] = Field(default_factory=dict)
    artifactManifest: Dict[str, Any] = Field(default_factory=dict)


class Epic3CompletedPayload(BaseModel):
    """
    Payload for Epic3Completed event. Triggers Epic4Worker.
    Note: driftSucceeded=False is valid — Epic3 is non-blocking.
    """
    runId: str
    projectId: str
    branch: str
    commitSha: str
    refName: str
    refType: str
    triggerType: Literal["manual", "push", "pull_request", "system"] = "manual"
    isPreview: bool = False
    githubUrl: str
    impactReport: Dict[str, Any] = Field(default_factory=dict)
    docSnapshot: Dict[str, Any] = Field(default_factory=dict)
    artifactManifest: Dict[str, Any] = Field(default_factory=dict)
    driftReport: Dict[str, Any] = Field(default_factory=dict)
    driftSucceeded: bool = False


class Epic4CompletedPayload(BaseModel):
    """Payload for Epic4Completed event. Signals end of pipeline."""
    runId: str
    projectId: str
    branch: str
    commitSha: str
    refName: str
    refType: str
    triggerType: Literal["manual", "push", "pull_request", "system"] = "manual"
    isPreview: bool = False
    manifestValidated: bool = False


class AnalysisFailedPayload(BaseModel):
    """Payload for AnalysisFailed event. Published to DLQ on unrecoverable failure."""
    runId: str
    projectId: str
    failedStage: Literal["epic1", "epic2", "epic3", "epic4"]
    errorMessage: str = Field(..., min_length=1)
    retryCount: int = Field(..., ge=0)


# ─── Payload parsers per event type ─────────────────────────────────────────

PAYLOAD_MODELS = {
    ANALYSIS_REQUESTED: AnalysisRequestedPayload,
    EPIC1_COMPLETED: Epic1CompletedPayload,
    EPIC2_COMPLETED: Epic2CompletedPayload,
    EPIC3_COMPLETED: Epic3CompletedPayload,
    EPIC4_COMPLETED: Epic4CompletedPayload,
    ANALYSIS_FAILED: AnalysisFailedPayload,
}


def parse_payload(event_type: str, raw_payload: Dict[str, Any]) -> BaseModel:
    """
    Parse and validate a raw payload dict into the typed model for event_type.
    Raises ValidationError if the payload does not match.
    """
    model_cls = PAYLOAD_MODELS.get(event_type)
    if model_cls is None:
        raise ValueError(f"Unknown event type: {event_type!r}")
    return model_cls.model_validate(raw_payload)


# ─── Version compatibility helpers ──────────────────────────────────────────

def is_compatible_version(received_version: int, expected_version: int = EVENT_VERSION) -> bool:
    """
    Returns True if the received event version is compatible.
    v1 consumers can only handle v1 events.
    For additive changes (same major version), this is always True.
    """
    return received_version == expected_version
