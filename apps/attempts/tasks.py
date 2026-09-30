"""Celery tasks for the attempts engine.

Beat schedule is configured by the parent project; these are the task bodies.
"""

import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def expire_attempts():
    """Expire in-progress attempts past their deadline, then grade each one.

    Each attempt is locked with ``select_for_update`` so a concurrent answer
    write cannot slip in between the status flip and grading.
    """
    from .models import Attempt
    from .services import grade_attempt

    now = timezone.now()
    candidate_ids = list(
        Attempt.objects.filter(status=Attempt.STATUS_IN_PROGRESS, deadline__lt=now).values_list(
            "id", flat=True
        )
    )
    expired = 0
    for attempt_id in candidate_ids:
        with transaction.atomic():
            attempt = Attempt.objects.select_for_update().get(pk=attempt_id)
            if attempt.status != Attempt.STATUS_IN_PROGRESS:
                continue
            attempt.status = Attempt.STATUS_EXPIRED
            attempt.submitted_at = now
            attempt.save(update_fields=["status", "submitted_at"])
            grade_attempt(attempt)
            expired += 1
    logger.info("expire_attempts: expired %d attempt(s)", expired)
    return expired


@shared_task
def send_result_email(attempt_id):
    """Stub result notification: logs instead of sending (no real SMTP needed)."""
    from .models import Attempt

    try:
        attempt = Attempt.objects.select_related("student").get(pk=attempt_id)
    except Attempt.DoesNotExist:
        logger.warning("send_result_email: attempt %s not found", attempt_id)
        return None
    logger.info(
        "email to %s: score %s", attempt.student.email, attempt.score_snapshot
    )
    return True
