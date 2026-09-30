"""Django admin for the attempts engine.

Answers, violation events and audit logs are append-only: no add/change/delete
through the admin, only inspection.
"""

from django.contrib import admin

from .models import Answer, Attempt, AuditLog, ViolationEvent


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Attempt)
class AttemptAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "exam",
        "student",
        "status",
        "started_at",
        "deadline",
        "score_snapshot",
        "flagged",
    ]
    list_filter = ["status", "flagged"]
    search_fields = ["student__email", "exam__title"]
    readonly_fields = ["id", "token_hash", "started_at", "question_order"]


@admin.register(Answer)
class AnswerAdmin(ReadOnlyAdmin):
    list_display = [
        "id",
        "attempt",
        "question",
        "revision",
        "is_graded",
        "marks_awarded",
        "server_received_at",
    ]
    readonly_fields = [field.name for field in Answer._meta.fields]


@admin.register(ViolationEvent)
class ViolationEventAdmin(ReadOnlyAdmin):
    list_display = ["id", "attempt", "event_type", "severity", "server_ts"]
    list_filter = ["event_type", "severity"]
    readonly_fields = [field.name for field in ViolationEvent._meta.fields]


@admin.register(AuditLog)
class AuditLogAdmin(ReadOnlyAdmin):
    list_display = ["id", "actor", "action", "target", "created_at"]
    list_filter = ["action"]
    readonly_fields = [field.name for field in AuditLog._meta.fields]
