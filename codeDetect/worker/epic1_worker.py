"""
Epic1Worker — Code Intelligence Analysis Worker (Python)

Standalone Python worker that:
- Connects to RabbitMQ and consumes analysis-jobs-epic1-queue
- Validates inbound AnalysisRequested event envelopes
- Invokes the AnalysisPipeline (codeDetect)
- Publishes Epic1Completed or AnalysisFailed result events

This worker is designed to run as an independent process.
It does NOT call Epic2Worker directly — it only publishes events.

Usage:
    python -m worker.epic1_worker

Environment:
    RABBITMQ_URL     — amqp://user:pass@host:5672/
    EPIC1_QUEUE      — optional override (default: analysis-jobs-epic1-queue)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any, Dict, Optional

LOG = logging.getLogger("epic1.worker")


def _setup_logging() -> None:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )


# ─── Idempotency Store ───────────────────────────────────────────────────────

_processed_event_ids: set[str] = set()
MAX_PROCESSED_IDS = 10_000


def _is_duplicate(event_id: str) -> bool:
    """Returns True if this eventId was already processed (idempotency check)."""
    if event_id in _processed_event_ids:
        return True
    if len(_processed_event_ids) >= MAX_PROCESSED_IDS:
        # Simple eviction: remove oldest half
        to_remove = list(_processed_event_ids)[: MAX_PROCESSED_IDS // 2]
        for eid in to_remove:
            _processed_event_ids.discard(eid)
    _processed_event_ids.add(event_id)
    return False


# ─── Publisher helpers ───────────────────────────────────────────────────────

def _publish_to_queue(channel: Any, queue_name: str, envelope_dict: Dict) -> None:
    """Publish a dict as JSON to the named queue."""
    try:
        import amqp  # type: ignore
        channel.basic_publish(
            amqp.Message(
                json.dumps(envelope_dict),
                content_type="application/json",
                delivery_mode=2,
            ),
            exchange="",
            routing_key=queue_name,
        )
    except ImportError:
        # amqplib not available — log and continue (e.g., test environment)
        LOG.warning("amqp not available — skipping publish to %s", queue_name)


# ─── Message Handler ─────────────────────────────────────────────────────────

def handle_message(body: bytes, channel: Any) -> None:
    """
    Process a single message from the EPIC1 queue.
    Called by the amqp consumer callback.
    """
    from worker.events.schemas import (
        EventEnvelope,
        AnalysisRequestedPayload,
        ANALYSIS_REQUESTED,
        EPIC1_COMPLETED,
        ANALYSIS_FAILED,
        QUEUE_EPIC2,
        QUEUE_EPIC1_DLQ,
        build_envelope,
        is_compatible_version,
        EVENT_VERSION,
    )

    try:
        raw = json.loads(body)
    except json.JSONDecodeError as exc:
        LOG.error("Epic1Worker: failed to decode message JSON: %s", exc)
        return

    # Validate envelope
    try:
        envelope = EventEnvelope.model_validate(raw)
    except Exception as exc:
        LOG.error("Epic1Worker: invalid EventEnvelope: %s", exc)
        return

    event_id = envelope.eventId
    correlation_id = envelope.correlationId
    trace_id = envelope.traceId

    # Idempotency
    if _is_duplicate(event_id):
        LOG.warning("Epic1Worker: duplicate eventId=%s — skipping", event_id)
        return

    # Version check
    if not is_compatible_version(envelope.eventVersion):
        LOG.error(
            "Epic1Worker: incompatible event version %d (expected %d)",
            envelope.eventVersion, EVENT_VERSION,
        )
        return

    if envelope.eventType != ANALYSIS_REQUESTED:
        LOG.warning("Epic1Worker: unexpected eventType=%s — ignoring", envelope.eventType)
        return

    # Validate payload
    try:
        payload = AnalysisRequestedPayload.model_validate(envelope.payload)
    except Exception as exc:
        LOG.error("Epic1Worker: invalid AnalysisRequestedPayload: %s", exc)
        return

    run_id = payload.runId
    project_id = payload.projectId

    LOG.info(
        "Epic1Worker: processing AnalysisRequested run_id=%s project_id=%s correlation_id=%s",
        run_id, project_id, correlation_id,
    )

    # ── Run pipeline ─────────────────────────────────────────────────────────
    impact_report: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None

    try:
        # Import here to avoid circular imports at module load time
        from pipeline.analysis_pipeline import AnalysisPipeline

        pipeline = AnalysisPipeline(
            repo_input=payload.githubUrl,
            branch=payload.branch,
            new_user=payload.newUser,
            github_token=os.environ.get("GITHUB_TOKEN"),
        )
        # Run returns 0 on success, 1 on failure
        # We need the actual report — read from output file
        exit_code = pipeline.run()

        if exit_code == 0:
            output_path = os.path.join(
                os.path.dirname(os.path.dirname(__file__)), "impact_report.json"
            )
            if os.path.exists(output_path):
                with open(output_path, "r", encoding="utf-8") as f:
                    impact_report = json.load(f)
            else:
                error_message = "impact_report.json not found after successful pipeline run"
        else:
            error_message = f"AnalysisPipeline exited with code {exit_code}"

    except Exception as exc:
        error_message = str(exc)
        LOG.error("Epic1Worker: pipeline error: %s", error_message, exc_info=True)

    ctx = dict(correlation_id=correlation_id, trace_id=trace_id)

    # ── Publish result event ──────────────────────────────────────────────────
    if impact_report is not None:
        LOG.info("Epic1Worker: publishing Epic1Completed for run_id=%s", run_id)
        result_envelope = build_envelope(
            EPIC1_COMPLETED,
            EVENT_VERSION,
            {
                "runId": run_id,
                "projectId": project_id,
                "branch": payload.branch,
                "commitSha": payload.commitSha,
                "refName": payload.refName,
                "refType": payload.refType,
                "triggerType": payload.triggerType,
                "isPreview": payload.isPreview,
                "githubUrl": payload.githubUrl,
                "impactReport": impact_report,
            },
            **ctx,
        )
        _publish_to_queue(channel, QUEUE_EPIC2, result_envelope.model_dump())
    else:
        LOG.error("Epic1Worker: publishing AnalysisFailed for run_id=%s error=%s", run_id, error_message)
        fail_envelope = build_envelope(
            ANALYSIS_FAILED,
            EVENT_VERSION,
            {
                "runId": run_id,
                "projectId": project_id,
                "failedStage": "epic1",
                "errorMessage": error_message or "Unknown error",
                "retryCount": 0,
            },
            **ctx,
        )
        _publish_to_queue(channel, QUEUE_EPIC1_DLQ, fail_envelope.model_dump())


# ─── Worker Entry Point ───────────────────────────────────────────────────────

def run_worker() -> None:
    """Start the Epic1 worker and block until interrupted."""
    _setup_logging()
    rabbitmq_url = os.environ.get("RABBITMQ_URL", "amqp://localhost")
    queue_name = os.environ.get("EPIC1_QUEUE", "analysis-jobs-epic1-queue")

    LOG.info("Epic1Worker: connecting to %s", rabbitmq_url)

    try:
        import amqp  # type: ignore
    except ImportError:
        LOG.error("Epic1Worker: 'amqp' package not installed. Run: pip install amqp")
        sys.exit(1)

    while True:
        try:
            conn = amqp.Connection(rabbitmq_url)
            conn.connect()
            channel = conn.channel()
            channel.queue_declare(queue=queue_name, durable=True, auto_delete=False)
            channel.basic_qos(prefetch_count=1)

            LOG.info("Epic1Worker: consuming from %s", queue_name)

            def _on_message(message: Any) -> None:
                try:
                    handle_message(message.body, channel)
                    channel.basic_ack(message.delivery_tag)
                except Exception as exc:
                    LOG.error("Epic1Worker: handler error — nacking: %s", exc)
                    channel.basic_reject(message.delivery_tag, requeue=False)

            channel.basic_consume(queue_name, callback=_on_message, no_ack=False)

            while True:
                conn.drain_events(timeout=1)

        except KeyboardInterrupt:
            LOG.info("Epic1Worker: shutting down")
            break
        except Exception as exc:
            LOG.error("Epic1Worker: connection error — reconnecting in 5s: %s", exc)
            time.sleep(5)


if __name__ == "__main__":
    run_worker()
