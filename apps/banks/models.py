"""Question banks: subjects, banks, questions and choices."""

import uuid

from django.conf import settings
from django.db import models


class Subject(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class QuestionBank(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="banks",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Question(models.Model):
    MCQ_SINGLE = "mcq_single"
    MCQ_MULTI = "mcq_multi"
    TRUE_FALSE = "true_false"
    SHORT_ANSWER = "short_answer"

    QTYPE_CHOICES = [
        (MCQ_SINGLE, "MCQ single"),
        (MCQ_MULTI, "MCQ multi"),
        (TRUE_FALSE, "True/False"),
        (SHORT_ANSWER, "Short answer"),
    ]

    DIFFICULTY_CHOICES = [
        ("easy", "Easy"),
        ("medium", "Medium"),
        ("hard", "Hard"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bank = models.ForeignKey(QuestionBank, on_delete=models.CASCADE, related_name="questions")
    qtype = models.CharField(max_length=20, choices=QTYPE_CHOICES)
    stem = models.TextField()
    topic = models.CharField(max_length=255, blank=True)
    difficulty = models.CharField(max_length=20, choices=DIFFICULTY_CHOICES, default="medium")
    marks = models.DecimalField(max_digits=5, decimal_places=2, default=1)
    version = models.IntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_qtype_display()}: {self.stem[:60]}"


class Choice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="choices")
    text = models.TextField()
    is_correct = models.BooleanField(default=False)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        marker = "correct" if self.is_correct else "incorrect"
        return f"{marker}: {self.text[:60]}"
