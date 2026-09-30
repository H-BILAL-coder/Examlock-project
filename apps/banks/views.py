"""ViewSets for the banks app: subjects, question banks, questions and choices."""

from django.db import transaction
from django.db.models import ProtectedError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import IsAdmin, IsAnyRole, IsInstructor, IsInstructorOrAdmin, IsStudent
from apps.exams.models import ExamQuestion

from .models import Choice, Question, QuestionBank, Subject
from .serializers import QuestionBankSerializer, QuestionSerializer, SubjectSerializer


def validate_choice_payload(qtype, items):
    """Validate a choices-replace payload against the question's type.

    Raises DRF ValidationError (HTTP 400) when the payload is invalid.
    """
    if not isinstance(items, list):
        raise ValidationError({"choices": 'Expected a list of choice objects.'})
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not str(item.get("text") or "").strip():
            raise ValidationError(
                {"choices": f'Choice #{index + 1} must include non-empty "text".'}
            )
    correct = sum(1 for item in items if item.get("is_correct"))
    if qtype == Question.MCQ_SINGLE:
        if correct != 1:
            raise ValidationError({"choices": "mcq_single requires exactly 1 correct choice."})
    elif qtype == Question.MCQ_MULTI:
        if correct < 1:
            raise ValidationError({"choices": "mcq_multi requires at least 1 correct choice."})
    elif qtype == Question.TRUE_FALSE:
        if len(items) != 2:
            raise ValidationError({"choices": "true_false requires exactly 2 choices."})
        if correct != 1:
            raise ValidationError({"choices": "true_false requires exactly 1 correct choice."})
    elif qtype == Question.SHORT_ANSWER:
        if items:
            raise ValidationError({"choices": "short_answer questions must not have choices."})
    else:
        raise ValidationError({"qtype": f"Unknown question type: {qtype}."})


class SubjectViewSet(viewsets.ModelViewSet):
    """Subjects: readable by anyone authenticated, writable by admins only."""

    queryset = Subject.objects.all().order_by("name")
    serializer_class = SubjectSerializer

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsAdmin()]
        return [IsAuthenticated()]


class QuestionBankViewSet(viewsets.ModelViewSet):
    """Question banks: admins see all, instructors see their own, students see none."""

    serializer_class = QuestionBankSerializer

    def get_queryset(self):
        user = self.request.user
        role = getattr(user, "role", None)
        if role == "admin":
            return QuestionBank.objects.all().order_by("-created_at")
        if role == "instructor":
            return QuestionBank.objects.filter(owner=user).order_by("-created_at")
        return QuestionBank.objects.none()

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy", "questions"):
            return [IsAuthenticated(), IsInstructorOrAdmin()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    @action(detail=True, methods=["get", "post"], url_path="questions")
    def questions(self, request, pk=None):
        """List or create questions inside this bank (ownership enforced by scoping)."""
        bank = self.get_object()
        if request.method == "GET":
            questions = bank.questions.prefetch_related("choices").all()
            return Response(QuestionSerializer(questions, many=True).data)

        data = request.data.copy()
        data.pop("bank", None)  # the bank always comes from the URL
        serializer = QuestionSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        question = serializer.save(bank=bank)
        return Response(QuestionSerializer(question).data, status=status.HTTP_201_CREATED)


class QuestionViewSet(viewsets.ModelViewSet):
    """Questions scoped to banks the user owns (admins see all)."""

    serializer_class = QuestionSerializer

    def get_queryset(self):
        user = self.request.user
        role = getattr(user, "role", None)
        base = Question.objects.prefetch_related("choices")
        if role == "admin":
            return base.all().order_by("-created_at")
        if role == "instructor":
            return base.filter(bank__owner=user).order_by("-created_at")
        return base.none()

    def get_permissions(self):
        if self.action in (
            "create",
            "update",
            "partial_update",
            "destroy",
            "replace_choices",
        ):
            return [IsAuthenticated(), IsInstructorOrAdmin()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        bank = serializer.validated_data["bank"]
        if (
            getattr(self.request.user, "role", None) != "admin"
            and bank.owner_id != self.request.user.id
        ):
            raise PermissionDenied("You can only add questions to banks you own.")
        serializer.save()

    def perform_update(self, serializer):
        # Any edit bumps the question version (exams pin the version at attach time).
        serializer.save(version=serializer.instance.version + 1)

    def perform_destroy(self, instance):
        if ExamQuestion.objects.filter(question=instance, exam__status="published").exists():
            raise ValidationError(
                {"detail": "Cannot delete question: referenced by published exam."}
            )
        try:
            instance.delete()
        except ProtectedError:
            raise ValidationError(
                {"detail": "Cannot delete question: it is still attached to an exam."}
            )

    @action(detail=True, methods=["post"], url_path="choices")
    def replace_choices(self, request, pk=None):
        """Replace all choices of a question (validated per qtype); bumps version."""
        question = self.get_object()
        items = request.data.get("choices")
        validate_choice_payload(question.qtype, items)
        with transaction.atomic():
            Choice.objects.filter(question=question).delete()
            Choice.objects.bulk_create(
                [
                    Choice(
                        question=question,
                        text=item["text"],
                        is_correct=bool(item.get("is_correct", False)),
                        sort_order=item.get("sort_order", index),
                    )
                    for index, item in enumerate(items)
                ]
            )
            question.version += 1
            question.save(update_fields=["version"])
        # Re-fetch so the response reflects the replaced choices.
        question = Question.objects.prefetch_related("choices").get(pk=question.pk)
        return Response(QuestionSerializer(question).data)
