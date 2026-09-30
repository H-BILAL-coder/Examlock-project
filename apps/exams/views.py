"""ViewSets for the exams app: exams, exam questions, enrollments, publish/close."""

import copy
import uuid

from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.banks.models import Question
from apps.core.permissions import IsAdmin, IsAnyRole, IsInstructor, IsInstructorOrAdmin, IsStudent

from .models import DEFAULT_EXAM_POLICY, Enrollment, Exam
from .serializers import EnrollmentSerializer, ExamQuestionSerializer, ExamSerializer


def validated_uuid(value, field_name):
    """Return value as a UUID or raise a 400 ValidationError."""
    if not value:
        raise ValidationError({field_name: "This field is required."})
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise ValidationError({field_name: "Must be a valid UUID."})


class ExamViewSet(viewsets.ModelViewSet):
    """Exams: admins see all; instructors see their own; students see published
    exams they are enrolled in. Status changes go through publish/close."""

    serializer_class = ExamSerializer

    #: Fields that may still be edited after an exam leaves draft status.
    NON_DRAFT_EDITABLE_FIELDS = {"title", "instructions", "results_published"}

    def get_queryset(self):
        user = self.request.user
        role = getattr(user, "role", None)
        if role == "admin":
            return Exam.objects.all().order_by("-created_at")
        if role == "instructor":
            return Exam.objects.filter(author=user).order_by("-created_at")
        return Exam.objects.filter(
            status=Exam.PUBLISHED,
            enrollments__student=user,
        ).order_by("-created_at")

    def get_permissions(self):
        write_actions = {
            "create",
            "update",
            "partial_update",
            "destroy",
            "publish",
            "close",
            "enrollments",
        }
        if self.action in write_actions:
            return [IsAuthenticated(), IsInstructorOrAdmin()]
        return [IsAuthenticated(), IsAnyRole()]

    def perform_create(self, serializer):
        serializer.save(author=self.request.user)

    def perform_update(self, serializer):
        instance = serializer.instance
        if instance.status != Exam.DRAFT:
            offending = [
                field
                for field in serializer.validated_data
                if field not in self.NON_DRAFT_EDITABLE_FIELDS
            ]
            if offending:
                raise ValidationError(
                    {
                        field: "This field cannot be changed once the exam has left draft status."
                        for field in offending
                    }
                )
        serializer.save()

    # -- helpers ------------------------------------------------------

    def _check_author_or_admin(self, exam):
        user = self.request.user
        if getattr(user, "role", None) != "admin" and exam.author_id != user.id:
            raise PermissionDenied("Only the exam author or an admin can perform this action.")

    # -- custom actions -----------------------------------------------

    @action(detail=True, methods=["get", "post"], url_path="questions")
    def questions(self, request, pk=None):
        """List pinned exam questions, or attach a bank question (draft only)."""
        exam = self.get_object()
        if request.method == "GET":
            exam_questions = exam.exam_questions.select_related("question").all()
            return Response(ExamQuestionSerializer(exam_questions, many=True).data)

        self._check_author_or_admin(exam)
        if exam.status != Exam.DRAFT:
            raise ValidationError({"detail": "Questions can only be attached to draft exams."})
        question_id = validated_uuid(request.data.get("question_id"), "question_id")
        question = get_object_or_404(Question, pk=question_id)
        if (
            getattr(request.user, "role", None) != "admin"
            and question.bank.owner_id != request.user.id
        ):
            raise PermissionDenied(
                "You can only attach questions from your own question banks."
            )
        serializer = ExamQuestionSerializer(
            data={
                "question_id": str(question.pk),
                "pool_tag": request.data.get("pool_tag"),
                "pool_draw": request.data.get("pool_draw"),
                "marks_override": request.data.get("marks_override"),
                "sort_order": request.data.get("sort_order", 0),
            }
        )
        serializer.is_valid(raise_exception=True)
        exam_question = serializer.save(exam=exam, question_version=question.version)
        return Response(
            ExamQuestionSerializer(exam_question).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get", "post"], url_path="enrollments")
    def enrollments(self, request, pk=None):
        """List enrollments, or enroll students (duplicates are skipped)."""
        exam = self.get_object()
        self._check_author_or_admin(exam)
        if request.method == "GET":
            enrollments = exam.enrollments.select_related("student").all()
            return Response(EnrollmentSerializer(enrollments, many=True).data)

        student_ids = request.data.get("student_ids") or []
        if isinstance(student_ids, str):
            student_ids = [student_ids]
        student_ids = list(student_ids)
        single_id = request.data.get("student_id")
        if single_id:
            student_ids.append(single_id)
        if not student_ids:
            raise ValidationError({"detail": 'Provide "student_id" or "student_ids".'})

        User = get_user_model()
        created = []
        seen = set()
        for raw_id in student_ids:
            student_id = validated_uuid(raw_id, "student_ids")
            if student_id in seen:
                continue
            seen.add(student_id)
            student = get_object_or_404(User, pk=student_id)
            enrollment, was_created = Enrollment.objects.get_or_create(
                exam=exam, student=student
            )
            if was_created:
                created.append(enrollment)
        return Response(
            EnrollmentSerializer(created, many=True).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="publish")
    def publish(self, request, pk=None):
        """Validate a draft exam and move it to published."""
        exam = self.get_object()
        self._check_author_or_admin(exam)
        errors = {}
        if not exam.exam_questions.exists():
            errors["exam_questions"] = (
                "An exam needs at least one question before it can be published."
            )
        if not exam.enrollments.exists():
            errors["enrollments"] = (
                "An exam needs at least one enrolled student before it can be published."
            )
        if exam.opens_at >= exam.closes_at:
            errors["closes_at"] = "closes_at must be after opens_at."
        if exam.duration_minutes <= 0:
            errors["duration_minutes"] = "duration_minutes must be greater than 0."
        if errors:
            raise ValidationError(errors)
        if not exam.policy:
            exam.policy = copy.deepcopy(DEFAULT_EXAM_POLICY)
        exam.status = Exam.PUBLISHED
        exam.save(update_fields=["policy", "status"])
        return Response(ExamSerializer(exam).data)

    @action(detail=True, methods=["post"], url_path="close")
    def close(self, request, pk=None):
        """Move an exam to closed."""
        exam = self.get_object()
        self._check_author_or_admin(exam)
        exam.status = Exam.CLOSED
        exam.save(update_fields=["status"])
        return Response(ExamSerializer(exam).data)
