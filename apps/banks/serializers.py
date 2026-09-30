"""Serializers for the banks app."""

from rest_framework import serializers

from .models import Choice, Question, QuestionBank, Subject


class SubjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subject
        fields = ["id", "name", "description"]


class ChoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Choice
        fields = ["id", "question", "text", "is_correct", "sort_order"]


class QuestionSerializer(serializers.ModelSerializer):
    choices = ChoiceSerializer(many=True, read_only=True)

    class Meta:
        model = Question
        fields = [
            "id",
            "bank",
            "qtype",
            "stem",
            "topic",
            "difficulty",
            "marks",
            "version",
            "created_at",
            "choices",
        ]
        read_only_fields = ["id", "version", "created_at", "choices"]


class QuestionBankSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuestionBank
        fields = ["id", "owner", "name", "description", "is_archived", "created_at"]
        read_only_fields = ["id", "owner", "created_at"]
