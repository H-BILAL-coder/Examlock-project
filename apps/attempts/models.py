"""Attempts engine models: attempt lifecycle, append-only answers, violation events, audit log."""

import uuid

from django.conf import settings
from django.db import models


class Attempt(models.Model):
    """One student's run at an exam: lifecycle state, timing, scoring snapshot, flags."""

    STATUS_IN_PROGRESS = "in_progress"
    STATUS_SUBMITTED = "submitted"
    STATUS_EXPIRED = "expired"
    STATUS_VOIDED = "voided"
    STATUS_CHOICES = (
        (STATUS_IN_PROGRESS, "In progress"),
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_VOIDED, "Voided"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exam = models.ForeignKey("exams.Exam", related_name="attempts", on_delete=models.CASCADE)
    student = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="attempts", on_delete=models.CASCADE)
    enrollment = models.ForeignKey("exams.Enrollment", on_delete=models.CASCADE)
    started_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    deadline = models.DateTimeField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_IN_PROGRESS)
    token_hash = models.CharField(max_length=255)
    device_fingerprint = models.CharField(max_length=255, blank=True, default="")
    start_ip = models.GenericIPAddressField(null=True, blank=True)
    seq = models.IntegerField(default=0)
    question_order = models.JSONField(default=dict)
    score_snapshot = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    flagged = models.BooleanField(default=False)
    flag_reason = models.TextField(blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["exam", "status"]),
            models.Index(fields=["student", "status"]),
        ]

    def __str__(self):
        return f"Attempt {self.id} [{self.status}]"


class Answer(models.Model):
    """Append-only answer revisions. Every save writes a new row; the latest revision wins."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attempt = models.ForeignKey(Attempt, related_name="answers", on_delete=models.CASCADE)
    question = models.ForeignKey("banks.Question", on_delete=models.CASCADE)
    selected_choice_ids = models.JSONField(null=True, blank=True)  # list of UUID strings
    text_answer = models.TextField(null=True, blank=True)
    revision = models.IntegerField(default=1)
    server_received_at = models.DateTimeField(auto_now_add=True)
    is_graded = models.BooleanField(default=False)
    marks_awarded = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    # Manual grading feedback (set via the mark endpoint). Small extension to the
    # contract's field list so instructor feedback has somewhere to live.
    feedback = models.TextField(blank=True, default="")
    idempotency_key = models.CharField(max_length=255, unique=True)

    class Meta:
        unique_together = ("attempt", "question", "revision")

    def __str__(self):
        return f"Answer {self.id} rev {self.revision}"


class ViolationEvent(models.Model):
    """Insert-only anti-cheat events. Rows are never updated or deleted; adjudication
    outcomes are appended into the ``adjudication`` JSON field via its own endpoint."""

    EVENT_CHOICES = (
        ("tab_hidden", "Tab hidden"),
        ("focus_lost", "Focus lost"),
        ("fullscreen_exit", "Fullscreen exit"),
        ("copy_attempt", "Copy attempt"),
        ("paste_attempt", "Paste attempt"),
        ("devtools_signal", "Devtools signal"),
        ("heartbeat_gap", "Heartbeat gap"),
        ("device_drift", "Device drift"),
        ("ip_drift", "IP drift"),
        ("rate_limit_hit", "Rate limit hit"),
    )
    SEVERITY_CHOICES = (
        ("low", "Low"),
        ("medium", "Medium"),
        ("high", "High"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attempt = models.ForeignKey(Attempt, related_name="violations", on_delete=models.CASCADE)
    event_type = models.CharField(max_length=32, choices=EVENT_CHOICES)
    severity = models.CharField(max_length=16, choices=SEVERITY_CHOICES)
    client_ts = models.DateTimeField(null=True, blank=True)
    server_ts = models.DateTimeField(auto_now_add=True)
    payload = models.JSONField(default=dict)
    adjudication = models.JSONField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["attempt", "server_ts"]),
            models.Index(fields=["event_type", "severity"]),
        ]

    def __str__(self):
        return f"Violation {self.event_type} ({self.severity})"


class AuditLog(models.Model):
    """Immutable-ish record of sensitive staff actions (e.g. force-submit)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=255)
    target = models.CharField(max_length=255)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Audit {self.action} -> {self.target}"
