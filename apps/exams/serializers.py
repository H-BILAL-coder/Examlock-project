"""Serializers for the exams app."""

from rest_framework import serializers

from apps.banks.models import Question

from .models import Enrollment, Exam, ExamQuestion


class PinnedQuestionSerializer(serializers.ModelSerializer):
    """Read-only view of the bank-question fields an exam question pins."""

    class Meta:
        model = Question
        fields = ["id", "stem", "qtype", "topic", "difficulty"]
        read_only_fields = ["id", "stem", "qtype", "topic", "difficulty"]


class ExamQuestionSerializer(serializers.ModelSerializer):
    question = PinnedQuestionSerializer(read_only=True)
    question_id = serializers.PrimaryKeyRelatedField(
        queryset=Question.objects.all(),
        source="question",
        write_only=True,
    )

    class Meta:
        model = ExamQuestion
        fields = [
            "id",
            "exam",
            "question",
            "question_id",
            "question_version",
            "pool_tag",
            "pool_draw",
            "marks_override",
            "sort_order",
        ]
        read_only_fields = ["id", "exam", "question", "question_version"]


class ExamSerializer(serializers.ModelSerializer):
    exam_questions = ExamQuestionSerializer(many=True, read_only=True)

    class Meta:
        model = Exam
        fields = [
            "id",
            "author",
            "title",
            "instructions",
            "duration_minutes",
            "opens_at",
            "closes_at",
            "attempts_allowed",
            "shuffle_questions",
            "shuffle_choices",
            "policy",
            "status",
            "results_published",
            "created_at",
            "exam_questions",
        ]
        read_only_fields = ["id", "author", "status", "created_at", "exam_questions"]


class EnrollmentSerializer(serializers.ModelSerializer):
    student_email = serializers.EmailField(source="student.email", read_only=True)
    student_name = serializers.CharField(source="student.full_name", read_only=True)

    class Meta:
        model = Enrollment
        fields = [
            "id",
            "exam",
            "student",
            "student_email",
            "student_name",
            "invited_at",
            "access_password_hash",
        ]
        read_only_fields = ["id", "exam", "invited_at"]
