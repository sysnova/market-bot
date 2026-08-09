"""Publish source-agnostic entry setup evidence for Alert."""

from app.contracts import (
    ENTRY_SETUP_ASSESSMENT_EVENT,
    EntrySetupAssessment,
    EventEnvelope,
    entry_setup_assessment_subject,
)

from .event_fanout import EventPublisher


async def publish_entry_setup_assessment(
    publisher: EventPublisher,
    assessment: EntrySetupAssessment,
    *,
    source: str,
) -> None:
    await publisher.publish(
        entry_setup_assessment_subject(assessment.family, assessment.symbol),
        EventEnvelope(
            event_id=assessment.assessment_id,
            event_type=ENTRY_SETUP_ASSESSMENT_EVENT,
            occurred_at=assessment.assessed_at,
            source=source,
            subject=assessment.symbol,
            payload=assessment,
            causation_id=(
                assessment.source_event_ids[0]
                if assessment.source_event_ids
                else None
            ),
        ),
    )
