"""Django admin for the banks app."""

from django.contrib import admin

from .models import Choice, Question, QuestionBank, Subject


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("id", "name")
    search_fields = ("name",)


@admin.register(QuestionBank)
class QuestionBankAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "owner", "is_archived", "created_at")
    list_filter = ("is_archived",)
    search_fields = ("name",)


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "bank",
        "qtype",
        "topic",
        "difficulty",
        "marks",
        "version",
        "created_at",
    )
    list_filter = ("qtype", "difficulty")


@admin.register(Choice)
class ChoiceAdmin(admin.ModelAdmin):
    list_display = ("id", "question", "text", "is_correct", "sort_order")
    list_filter = ("is_correct",)
