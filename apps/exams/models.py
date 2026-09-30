"""Exams: exam definitions, pinned exam questions, and enrollments."""

import uuid

from django.conf import settings
from django.db import models

DEFAULT_EXAM_POLICY = {
    "watched_events": [
        "tab_hidden",
        "focus_lost",
        "fullscreen_exit",
        "copy_attempt",
        "paste_attempt",
        "devtools_signal",
        "heartbeat_gap",
    ],
    "severity_map": {
        "tab_hidden": "medium",
        "focus_lost": "low",
        "fullscreen_exit": "medium",
        "copy_attempt": "low",
        "paste_attempt": "medium",
        "devtools_signal": "high",
        "heartbeat_gap": "medium",
        "device_drift": "high",
        "ip_drift": "medium",
        "rate_limit_hit": "medium",
    },
    "points": {"low": 1, "medium": 3, "high": 10},
    "warn_threshold": 5,
    "flag_threshold": 15,
    "negative_marking": {"enabled": False, "penalty_fraction": 0.25},
}


class Exam(models.Model):
    DRAFT = "draft"
    PUBLISHED = "published"
    CLOSED = "closed"
    ARCHIVED = "archived"

    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (PUBLISHED, "Published"),
        (CLOSED, "Closed"),
        (ARCHIVED, "Archived"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="exams",
    )
    title = models.CharField(max_length=255)
    instructions = models.TextField(blank=True)
    duration_minutes = models.IntegerField()
    opens_at = models.DateTimeField()
    closes_at = models.DateTimeField()
    attempts_allowed = models.IntegerField(default=1)
    shuffle_questions = models.BooleanField(default=True)
    shuffle_choices = models.BooleanField(default=True)
    policy = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=DRAFT)
    results_published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class ExamQuestion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="exam_questions")
    question = models.ForeignKey("banks.Question", on_delete=models.PROTECT)
    question_version = models.IntegerField()
    pool_tag = models.CharField(max_length=255, null=True, blank=True)
    pool_draw = models.IntegerField(null=True, blank=True)
    marks_override = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.exam_id} <- {self.question_id} (v{self.question_version})"


class Enrollment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="enrollments")
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="enrollments",
    )
    invited_at = models.DateTimeField(auto_now_add=True)
    access_password_hash = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        unique_together = ("exam", "student")

    def __str__(self):
        return f"{self.student_id} enrolled in {self.exam_id}"
