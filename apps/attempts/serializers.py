"""Serializers for the attempts engine API."""

from rest_framework import serializers

from .models import Attempt, AuditLog, ViolationEvent


class AttemptListSerializer(serializers.ModelSerializer):
    student_email = serializers.EmailField(source="student.email", read_only=True)
    exam_title = serializers.CharField(source="exam.title", read_only=True)

    class Meta:
        model = Attempt
        fields = [
            "id",
            "exam",
            "exam_title",
            "student_email",
            "status",
            "started_at",
            "submitted_at",
            "deadline",
            "seq",
            "score_snapshot",
            "flagged",
        ]


class AnswerItemSerializer(serializers.Serializer):
    question_id = serializers.UUIDField()
    choice_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_null=True, default=list
    )
    text = serializers.CharField(required=False, allow_null=True, allow_blank=True, default=None)


class AnswerSubmitSerializer(serializers.Serializer):
    seq = serializers.IntegerField(min_value=0)
    idempotency_key = serializers.CharField(max_length=255)
    items = AnswerItemSerializer(many=True)


class HeartbeatSerializer(serializers.Serializer):
    device_fingerprint = serializers.CharField(allow_blank=True, max_length=255)


class ViolationEventInSerializer(serializers.Serializer):
    event_type = serializers.ChoiceField(choices=[code for code, _ in ViolationEvent.EVENT_CHOICES])
    client_ts = serializers.DateTimeField(required=False, allow_null=True, default=None)
    payload = serializers.DictField(required=False, default=dict)


class ViolationBeaconSerializer(serializers.Serializer):
    events = ViolationEventInSerializer(many=True)


class ViolationEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ViolationEvent
        fields = [
            "id",
            "attempt",
            "event_type",
            "severity",
            "client_ts",
            "server_ts",
            "payload",
            "adjudication",
        ]


class SubmitSerializer(serializers.Serializer):
    confirm = serializers.BooleanField()


class MarkSerializer(serializers.Serializer):
    marks_awarded = serializers.DecimalField(max_digits=7, decimal_places=2)
    feedback = serializers.CharField(required=False, allow_blank=True, default="")


class AdjudicateSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["uphold", "dismiss", "penalize"])
    reason = serializers.CharField()


class AppealSerializer(serializers.Serializer):
    reason = serializers.CharField()


class AuditLogSerializer(serializers.ModelSerializer):
    actor_email = serializers.EmailField(source="actor.email", read_only=True)

    class Meta:
        model = AuditLog
        fields = ["id", "actor_email", "action", "target", "before", "after", "ip", "created_at"]
