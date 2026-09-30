"""Django admin for the exams app."""

from django.contrib import admin

from .models import Enrollment, Exam, ExamQuestion


@admin.register(Exam)
class ExamAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "author",
        "status",
        "opens_at",
        "closes_at",
        "duration_minutes",
        "attempts_allowed",
        "results_published",
        "created_at",
    )
    list_filter = ("status", "results_published")
    search_fields = ("title",)


@admin.register(ExamQuestion)
class ExamQuestionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "exam",
        "question",
        "question_version",
        "pool_tag",
        "pool_draw",
        "marks_override",
        "sort_order",
    )
    list_filter = ("pool_tag",)


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ("id", "exam", "student", "invited_at")
