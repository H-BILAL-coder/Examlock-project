"""Attempts engine API: lifecycle, write gate, violations, grading, analytics."""

import csv
import random
import secrets
import statistics
from datetime import timedelta
from decimal import Decimal

from django.db import models, transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import IsAdmin, IsAnyRole, IsInstructor, IsInstructorOrAdmin, IsStudent

from .models import Answer, Attempt, AuditLog, ViolationEvent
from .serializers import (
    AdjudicateSerializer,
    AnswerSubmitSerializer,
    AppealSerializer,
    AttemptListSerializer,
    AuditLogSerializer,
    HeartbeatSerializer,
    MarkSerializer,
    SubmitSerializer,
    ViolationBeaconSerializer,
    ViolationEventSerializer,
)
from .services import (
    grade_attempt,
    hash_attempt_token,
    recompute_score_snapshot,
    validate_attempt_token,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _get_exam_for_staff(exam_id, user):
    """Return the exam if the user is admin or the exam's instructor, else 403."""
    from apps.exams.models import Exam

    exam = get_object_or_404(Exam, pk=exam_id)
    role = getattr(user, "role", None)
    if role == "admin":
        return exam
    if role == "instructor" and exam.author_id == user.id:
        return exam
    raise PermissionDenied("You do not have access to this exam.")


def _attempt_detail_payload(attempt):
    """Serialize an attempt with its questions (no correct answers) and latest answers."""
    from apps.banks.models import Question
    from apps.exams.models import ExamQuestion

    exam = attempt.exam
    question_ids = (attempt.question_order or {}).get("questions", []) or []
    choice_order = (attempt.question_order or {}).get("choices", {}) or {}

    questions = {
        str(pk): q for pk, q in Question.objects.filter(id__in=question_ids).in_bulk().items()
    }
    exam_questions = {
        str(eq.question_id): eq
        for eq in ExamQuestion.objects.filter(exam=exam, question_id__in=question_ids)
    }

    latest = {}
    for answer in attempt.answers.filter(question_id__in=question_ids).order_by(
        "question_id", "-revision"
    ):
        key = str(answer.question_id)
        if key not in latest:
            latest[key] = answer

    question_payloads = []
    for qid in question_ids:
        question = questions.get(qid)
        if question is None:
            continue
        eq = exam_questions.get(qid)
        marks = eq.marks_override if (eq and eq.marks_override is not None) else question.marks
        ordered_choice_ids = choice_order.get(qid) or [str(c.id) for c in question.choices.order_by("sort_order")]
        choice_by_id = {str(c.id): c for c in question.choices.all()}
        choices = [
            {"id": cid, "text": choice_by_id[cid].text}
            for cid in ordered_choice_ids
            if cid in choice_by_id
        ]
        question_payloads.append(
            {
                "id": qid,
                "stem": question.stem,
                "qtype": question.qtype,
                "topic": question.topic,
                "difficulty": question.difficulty,
                "marks": str(marks),
                "choices": choices,
            }
        )

    answers_payload = {}
    for qid, answer in latest.items():
        answers_payload[qid] = {
            "selected_choice_ids": answer.selected_choice_ids or [],
            "text_answer": answer.text_answer,
            "revision": answer.revision,
            "is_graded": answer.is_graded,
            "marks_awarded": str(answer.marks_awarded) if answer.marks_awarded is not None else None,
        }

    now = timezone.now()
    return {
        "id": str(attempt.id),
        "exam": str(attempt.exam_id),
        "exam_title": exam.title,
        "status": attempt.status,
        "started_at": attempt.started_at.isoformat() if attempt.started_at else None,
        "submitted_at": attempt.submitted_at.isoformat() if attempt.submitted_at else None,
        "deadline": attempt.deadline.isoformat() if attempt.deadline else None,
        "remaining_seconds": max(0, int((attempt.deadline - now).total_seconds())) if attempt.deadline else 0,
        "seq": attempt.seq,
        "flagged": attempt.flagged,
        "flag_reason": attempt.flag_reason,
        "score_snapshot": str(attempt.score_snapshot) if attempt.score_snapshot is not None else None,
        "questions": question_payloads,
        "answers": answers_payload,
    }


# ---------------------------------------------------------------------------
# Exam-scoped endpoints
# ---------------------------------------------------------------------------

class StartAttemptView(APIView):
    """POST /api/v1/exams/<exam_id>/attempts/ — start a new attempt (students only)."""

    permission_classes = [IsAuthenticated, IsStudent]

    def post(self, request, exam_id):
        from apps.banks.models import Question
        from apps.exams.models import Enrollment, Exam, ExamQuestion

        exam = get_object_or_404(Exam, pk=exam_id)
        now = timezone.now()

        if exam.status != "published":
            return Response({"detail": "Exam is not published."}, status=status.HTTP_400_BAD_REQUEST)
        if exam.opens_at and now < exam.opens_at:
            return Response({"detail": "Exam has not opened yet."}, status=status.HTTP_400_BAD_REQUEST)
        if exam.closes_at and now > exam.closes_at:
            return Response({"detail": "Exam is closed."}, status=status.HTTP_400_BAD_REQUEST)

        enrollment = Enrollment.objects.filter(exam=exam, student=request.user).first()
        if enrollment is None:
            return Response(
                {"detail": "You are not enrolled in this exam."},
                status=status.HTTP_403_FORBIDDEN,
            )

        used = (
            Attempt.objects.filter(exam=exam, student=request.user)
            .exclude(status=Attempt.STATUS_VOIDED)
            .count()
        )
        if used >= exam.attempts_allowed:
            return Response(
                {"detail": "No attempts remaining."}, status=status.HTTP_409_CONFLICT
            )
        if Attempt.objects.filter(
            exam=exam, student=request.user, status=Attempt.STATUS_IN_PROGRESS
        ).exists():
            return Response(
                {"detail": "An attempt is already in progress."},
                status=status.HTTP_409_CONFLICT,
            )

        # Build the question list: fixed (pinned) questions plus pool draws.
        chosen = []
        fixed_rows = (
            ExamQuestion.objects.filter(exam=exam, pool_tag__isnull=True)
            .order_by("sort_order")
            .select_related("question")
        )
        for row in fixed_rows:
            chosen.append(row.question)

        pool_rows = ExamQuestion.objects.filter(exam=exam).exclude(pool_tag__isnull=True).exclude(pool_tag="")
        pools = {}
        for row in pool_rows:
            pools[row.pool_tag] = max(pools.get(row.pool_tag, 0), row.pool_draw or 0)
        chosen_ids = {q.id for q in chosen}
        for tag, draw in pools.items():
            candidates = list(
                Question.objects.filter(topic=tag, bank__owner=exam.author).exclude(id__in=chosen_ids)
            )
            for question in random.sample(candidates, min(draw, len(candidates))):
                chosen.append(question)
                chosen_ids.add(question.id)

        if not chosen:
            return Response(
                {"detail": "Exam has no questions."}, status=status.HTTP_400_BAD_REQUEST
            )

        question_ids = [str(q.id) for q in chosen]
        if exam.shuffle_questions:
            random.shuffle(question_ids)

        choice_order = {}
        for question in chosen:
            cids = [str(c.id) for c in question.choices.order_by("sort_order")]
            if exam.shuffle_choices:
                random.shuffle(cids)
            choice_order[str(question.id)] = cids

        deadline = now + timedelta(minutes=exam.duration_minutes)
        raw_token = secrets.token_urlsafe(32)
        attempt = Attempt.objects.create(
            exam=exam,
            student=request.user,
            enrollment=enrollment,
            deadline=deadline,
            token_hash=hash_attempt_token(raw_token),
            start_ip=get_client_ip(request),
            question_order={"questions": question_ids, "choices": choice_order},
        )
        return Response(
            {
                "attempt_id": str(attempt.id),
                "token": raw_token,  # returned once; client sends it as X-Attempt-Token
                "deadline": deadline.isoformat(),
                "remaining_seconds": max(0, int((deadline - now).total_seconds())),
            },
            status=status.HTTP_201_CREATED,
        )


class ExamResultsView(APIView):
    """GET /api/v1/exams/<exam_id>/results/ — attempt list for instructors/admins."""

    permission_classes = [IsAuthenticated, IsInstructorOrAdmin]

    def get(self, request, exam_id):
        exam = _get_exam_for_staff(exam_id, request.user)
        attempts = (
            Attempt.objects.filter(exam=exam)
            .select_related("student")
            .order_by("-started_at")
        )
        return Response(
            [
                {
                    "attempt_id": str(a.id),
                    "student_email": a.student.email,
                    "status": a.status,
                    "score_snapshot": str(a.score_snapshot) if a.score_snapshot is not None else None,
                    "flagged": a.flagged,
                    "started_at": a.started_at.isoformat() if a.started_at else None,
                    "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None,
                }
                for a in attempts
            ]
        )


class PublishResultsView(APIView):
    """POST /api/v1/exams/<exam_id>/results/publish — publish results."""

    permission_classes = [IsAuthenticated, IsInstructorOrAdmin]

    def post(self, request, exam_id):
        exam = _get_exam_for_staff(exam_id, request.user)
        exam.results_published = True
        exam.save(update_fields=["results_published"])
        return Response({"results_published": True})


class ExamAnalyticsView(APIView):
    """GET /api/v1/exams/<exam_id>/analytics — score stats and per-question pass rates."""

    permission_classes = [IsAuthenticated, IsInstructorOrAdmin]

    def get(self, request, exam_id):
        from apps.banks.models import Question
        from apps.exams.models import ExamQuestion

        exam = _get_exam_for_staff(exam_id, request.user)
        attempts = list(
            Attempt.objects.filter(
                exam=exam, status__in=[Attempt.STATUS_SUBMITTED, Attempt.STATUS_EXPIRED]
            ).select_related("student")
        )

        scores = [a.score_snapshot for a in attempts if a.score_snapshot is not None]
        floats = [float(s) for s in scores]
        summary = {
            "count": len(floats),
            "mean": round(statistics.fmean(floats), 2) if floats else 0,
            "median": round(statistics.median(floats), 2) if floats else 0,
            "min": round(min(floats), 2) if floats else 0,
            "max": round(max(floats), 2) if floats else 0,
        }

        # Question universe: pinned exam questions plus anything actually attempted.
        pinned = {str(eq.question_id): eq for eq in ExamQuestion.objects.filter(exam=exam)}
        qids = set(pinned)
        for attempt in attempts:
            qids.update((attempt.question_order or {}).get("questions", []) or [])
        questions = {
            str(pk): q for pk, q in Question.objects.filter(id__in=qids).in_bulk().items()
        }

        full_marks_map = {}
        total_possible = Decimal("0")
        for qid in qids:
            question = questions.get(qid)
            if question is None:
                continue
            eq = pinned.get(qid)
            full = eq.marks_override if (eq and eq.marks_override is not None) else question.marks
            full_marks_map[qid] = full
            total_possible += full

        buckets = [0] * 10
        for value in floats:
            pct = (value / float(total_possible) * 100) if total_possible else 0
            buckets[min(int(pct // 10), 9)] += 1
        distribution = [
            {"bucket": f"{i * 10}-{(i + 1) * 10}", "count": buckets[i]} for i in range(10)
        ]

        per_question = []
        for qid in sorted(qids):
            question = questions.get(qid)
            if question is None:
                continue
            full = full_marks_map[qid]
            answered = correct = 0
            for attempt in attempts:
                answer = (
                    attempt.answers.filter(question_id=qid).order_by("-revision").first()
                )
                if answer is None:
                    continue
                answered += 1
                if answer.marks_awarded is not None and answer.marks_awarded == full:
                    correct += 1
            per_question.append(
                {
                    "question_id": qid,
                    "stem_truncated": question.stem[:60],
                    "attempts": answered,
                    "correct": correct,
                    "pass_rate": round(correct / answered, 3) if answered else 0,
                }
            )

        return Response({**summary, "distribution": distribution, "per_question": per_question})


class ExportResultsView(APIView):
    """GET /api/v1/exams/<exam_id>/results/export — CSV download."""

    permission_classes = [IsAuthenticated, IsInstructorOrAdmin]

    def get(self, request, exam_id):
        exam = _get_exam_for_staff(exam_id, request.user)
        attempts = (
            Attempt.objects.filter(exam=exam).select_related("student").order_by("started_at")
        )
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="exam_{exam_id}_results.csv"'
        writer = csv.writer(response)
        writer.writerow(["student_email", "status", "score", "flagged"])
        for attempt in attempts:
            writer.writerow(
                [
                    attempt.student.email,
                    attempt.status,
                    attempt.score_snapshot if attempt.score_snapshot is not None else "",
                    attempt.flagged,
                ]
            )
        return response


# ---------------------------------------------------------------------------
# Attempt viewset
# ---------------------------------------------------------------------------

class AttemptViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Student/instructor/admin access to attempts plus the write-gate actions."""

    serializer_class = AttemptListSerializer

    def get_queryset(self):
        user = self.request.user
        qs = Attempt.objects.select_related("exam", "student", "enrollment").order_by("-started_at")
        role = getattr(user, "role", None)
        if role == "admin":
            return qs
        if role == "instructor":
            return qs.filter(exam__author=user)
        return qs.filter(student=user)

    def get_permissions(self):
        if self.action == "force_submit":
            return [IsAuthenticated(), IsAdmin()]
        if self.action in ("grade", "mark"):
            return [IsAuthenticated(), IsInstructorOrAdmin()]
        return [IsAuthenticated(), IsAnyRole()]

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        exam_id = request.query_params.get("exam")
        status_param = request.query_params.get("status")
        if exam_id:
            qs = qs.filter(exam_id=exam_id)
        if status_param:
            qs = qs.filter(status=status_param)
        page = self.paginate_queryset(qs)
        serializer = AttemptListSerializer(page if page is not None else qs, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        return Response(_attempt_detail_payload(self.get_object()))

    @action(detail=True, methods=["get"], url_path="status")
    def attempt_status(self, request, pk=None):
        """Server is the time authority: remaining time always comes from here."""
        attempt = self.get_object()
        now = timezone.now()
        return Response(
            {
                "server_now": now.isoformat(),
                "deadline": attempt.deadline.isoformat() if attempt.deadline else None,
                "remaining_seconds": max(0, int((attempt.deadline - now).total_seconds()))
                if attempt.deadline
                else 0,
                "seq": attempt.seq,
                "status": attempt.status,
                "flagged": attempt.flagged,
            }
        )

    @action(detail=True, methods=["post"], url_path="answers")
    def submit_answers(self, request, pk=None):
        """THE WRITE GATE: every answer save passes through here.

        Enforces, in order: attempt in progress (403), deadline (409), attempt
        token (403), monotonic seq (409), idempotency (200 dedup), then appends
        one Answer row per item with an incremented revision.
        """
        attempt = self.get_object()
        if attempt.status != Attempt.STATUS_IN_PROGRESS:
            return Response(
                {"detail": "Attempt is not in progress."}, status=status.HTTP_403_FORBIDDEN
            )
        if timezone.now() > attempt.deadline:
            return Response({"detail": "Deadline passed."}, status=status.HTTP_409_CONFLICT)
        if not validate_attempt_token(attempt, request.headers.get("X-Attempt-Token")):
            return Response(
                {"detail": "Invalid or missing attempt token."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = AnswerSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        seq = data["seq"]
        idempotency_key = data["idempotency_key"]
        items = data["items"]

        known_questions = set((attempt.question_order or {}).get("questions", []) or [])
        for item in items:
            if str(item["question_id"]) not in known_questions:
                return Response(
                    {"detail": f"Unknown question {item['question_id']}."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        with transaction.atomic():
            locked = Attempt.objects.select_for_update().get(pk=attempt.pk)
            if locked.status != Attempt.STATUS_IN_PROGRESS:
                return Response(
                    {"detail": "Attempt is not in progress."},
                    status=status.HTTP_403_FORBIDDEN,
                )

            # Idempotency is checked BEFORE seq staleness: a retried batch
            # legitimately reuses its original seq, so dedup must win over the
            # stale-seq rejection. Exact batch key, or the per-item derived keys
            # (f"{key}:{question_id}") from a retried multi-item batch.
            already = (
                Answer.objects.filter(attempt=locked)
                .filter(
                    models.Q(idempotency_key=idempotency_key)
                    | models.Q(idempotency_key__startswith=idempotency_key + ":")
                )
                .exists()
            )
            if already:
                return Response({"deduplicated": True, "saved": 0})

            if seq <= locked.seq:
                return Response({"detail": "Stale seq."}, status=status.HTTP_409_CONFLICT)

            saved = 0
            for item in items:
                qid = str(item["question_id"])
                max_rev = (
                    Answer.objects.filter(attempt=locked, question_id=qid).aggregate(
                        models.Max("revision")
                    )["revision__max"]
                    or 0
                )
                row_key = idempotency_key if len(items) == 1 else f"{idempotency_key}:{qid}"
                Answer.objects.create(
                    attempt=locked,
                    question_id=qid,
                    selected_choice_ids=[str(c) for c in (item.get("choice_ids") or [])],
                    text_answer=item.get("text"),
                    revision=max_rev + 1,
                    idempotency_key=row_key,
                )
                saved += 1
            locked.seq = seq
            locked.save(update_fields=["seq"])

        return Response({"saved": saved})

    @action(detail=True, methods=["post"], url_path="heartbeat")
    def heartbeat(self, request, pk=None):
        """Device/IP drift detection. Server time returned for client clock sync."""
        attempt = self.get_object()
        if attempt.status != Attempt.STATUS_IN_PROGRESS:
            return Response(
                {"detail": "Attempt is not in progress."}, status=status.HTTP_409_CONFLICT
            )
        serializer = HeartbeatSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        fingerprint = serializer.validated_data["device_fingerprint"]

        if attempt.device_fingerprint:
            if fingerprint and fingerprint != attempt.device_fingerprint:
                ViolationEvent.objects.create(
                    attempt=attempt,
                    event_type="device_drift",
                    severity="high",
                    payload={
                        "previous_fingerprint": attempt.device_fingerprint,
                        "current_fingerprint": fingerprint,
                    },
                )
        elif fingerprint:
            attempt.device_fingerprint = fingerprint
            attempt.save(update_fields=["device_fingerprint"])

        current_ip = get_client_ip(request)
        if attempt.start_ip and current_ip and current_ip != attempt.start_ip:
            ViolationEvent.objects.create(
                attempt=attempt,
                event_type="ip_drift",
                severity="medium",
                payload={"start_ip": attempt.start_ip, "current_ip": current_ip},
            )

        return Response({"ok": True, "server_now": timezone.now().isoformat()})

    @action(detail=True, methods=["get", "post"], url_path="violations")
    def violations(self, request, pk=None):
        """GET: read-only violation timeline. POST: the anti-cheat beacon."""
        attempt = self.get_object()
        if request.method == "GET":
            events = attempt.violations.order_by("server_ts")
            return Response(ViolationEventSerializer(events, many=True).data)

        serializer = ViolationBeaconSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        policy = attempt.exam.policy or {}
        severity_map = policy.get("severity_map") or {}
        points_map = policy.get("points") or {}

        to_create = []
        for event in serializer.validated_data["events"]:
            to_create.append(
                ViolationEvent(
                    attempt=attempt,
                    event_type=event["event_type"],
                    severity=severity_map.get(event["event_type"], "low"),
                    client_ts=event.get("client_ts"),
                    payload=event.get("payload") or {},
                )
            )
        ViolationEvent.objects.bulk_create(to_create)

        # Cumulative violation points across the whole attempt drive auto-flagging.
        total = 0
        for violation in attempt.violations.all():
            try:
                total += int(points_map.get(violation.severity, 0) or 0)
            except (TypeError, ValueError):
                continue
        threshold = policy.get("flag_threshold")
        if (
            threshold is not None
            and total >= int(threshold)
            and not attempt.flagged
            and attempt.status == Attempt.STATUS_IN_PROGRESS
        ):
            attempt.flagged = True
            attempt.flag_reason = f"violation points {total} >= threshold {threshold}"
            attempt.save(update_fields=["flagged", "flag_reason"])

        return Response({"received": len(to_create), "flagged": attempt.flagged})

    @action(detail=True, methods=["post"], url_path="submit")
    def submit(self, request, pk=None):
        attempt = self.get_object()
        serializer = SubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not serializer.validated_data["confirm"]:
            return Response(
                {"detail": "Submission requires confirm=true."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        with transaction.atomic():
            locked = Attempt.objects.select_for_update().get(pk=attempt.pk)
            if locked.status != Attempt.STATUS_IN_PROGRESS:
                return Response(
                    {"detail": f"Attempt already {locked.status}."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            now = timezone.now()
            locked.status = (
                Attempt.STATUS_EXPIRED if now > locked.deadline else Attempt.STATUS_SUBMITTED
            )
            locked.submitted_at = now
            locked.save(update_fields=["status", "submitted_at"])
            breakdown = grade_attempt(locked)
        return Response({"status": locked.status, "score": breakdown["total"]})

    @action(detail=True, methods=["post"], url_path="force-submit")
    def force_submit(self, request, pk=None):
        """Admin override: force-submit and grade, with an audit trail."""
        attempt = self.get_object()
        with transaction.atomic():
            locked = Attempt.objects.select_for_update().get(pk=attempt.pk)
            before = {"status": locked.status}
            now = timezone.now()
            locked.status = Attempt.STATUS_SUBMITTED
            locked.submitted_at = now
            locked.save(update_fields=["status", "submitted_at"])
            breakdown = grade_attempt(locked)
            AuditLog.objects.create(
                actor=request.user,
                action="attempt.force_submit",
                target=str(locked.id),
                before=before,
                after={"status": locked.status, "score": breakdown["total"]},
                ip=get_client_ip(request),
            )
        return Response({"status": locked.status, "score": breakdown["total"]})

    @action(detail=True, methods=["post"], url_path="grade")
    def grade(self, request, pk=None):
        """(Re)run server-side grading. Idempotent."""
        attempt = self.get_object()
        return Response(grade_attempt(attempt))

    @action(detail=True, methods=["post"], url_path=r"answers/(?P<answer_id>[^/.]+)/mark")
    def mark(self, request, pk=None, answer_id=None):
        """Manually grade an answer. The row id identifies the question; the latest
        revision row for that question is the one marked."""
        attempt = self.get_object()
        row = get_object_or_404(Answer, pk=answer_id, attempt=attempt)
        latest = (
            attempt.answers.filter(question=row.question).order_by("-revision").first()
        )
        serializer = MarkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        latest.marks_awarded = serializer.validated_data["marks_awarded"]
        latest.feedback = serializer.validated_data.get("feedback", "")
        latest.is_graded = True
        latest.save(update_fields=["marks_awarded", "feedback", "is_graded"])
        total = recompute_score_snapshot(attempt)
        return Response(
            {
                "answer_id": str(latest.id),
                "question_id": str(latest.question_id),
                "marks_awarded": str(latest.marks_awarded),
                "feedback": latest.feedback,
                "is_graded": True,
                "attempt_score": str(total),
            }
        )


# ---------------------------------------------------------------------------
# Violation viewset (adjudication / appeals)
# ---------------------------------------------------------------------------

class ViolationViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Violation events: read timeline, adjudicate (staff), appeal (student)."""

    serializer_class = ViolationEventSerializer

    def get_queryset(self):
        user = self.request.user
        qs = ViolationEvent.objects.select_related(
            "attempt", "attempt__exam", "attempt__student"
        ).order_by("-server_ts")
        role = getattr(user, "role", None)
        if role == "admin":
            return qs
        if role == "instructor":
            return qs.filter(attempt__exam__author=user)
        return qs.filter(attempt__student=user)

    def get_permissions(self):
        return [IsAuthenticated(), IsAnyRole()]

    @action(detail=True, methods=["post"], url_path="adjudicate")
    def adjudicate(self, request, pk=None):
        """Instructor of the exam (or admin) records a decision. The event itself
        is never edited — the outcome is appended to ``adjudication``."""
        violation = self.get_object()
        user = request.user
        if not (
            user.role == "admin"
            or (user.role == "instructor" and violation.attempt.exam.author_id == user.id)
        ):
            raise PermissionDenied("Only the exam instructor or an admin can adjudicate.")

        serializer = AdjudicateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        decision = serializer.validated_data["decision"]
        reason = serializer.validated_data["reason"]

        adjudication = violation.adjudication or {}
        adjudication.update(
            {
                "decision": decision,
                "reason": reason,
                "adjudicator": user.email,
                "timestamp": timezone.now().isoformat(),
            }
        )
        violation.adjudication = adjudication
        violation.save(update_fields=["adjudication"])

        if decision == "penalize":
            attempt = violation.attempt
            note = f"penalized [{violation.event_type}]: {reason}"
            attempt.flag_reason = (
                f"{attempt.flag_reason}\n{note}".strip() if attempt.flag_reason else note
            )
            attempt.flagged = True
            attempt.save(update_fields=["flagged", "flag_reason"])

        return Response(ViolationEventSerializer(violation).data)

    @action(detail=True, methods=["post"], url_path="appeal")
    def appeal(self, request, pk=None):
        """The student who owns the attempt appeals an adjudication (once)."""
        violation = self.get_object()
        user = request.user
        if user.role != "student" or violation.attempt.student_id != user.id:
            raise PermissionDenied("Only the student who owns the attempt can appeal.")

        serializer = AppealSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        adjudication = violation.adjudication or {}
        if "appeal" in adjudication:
            return Response(
                {"detail": "Appeal already submitted."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        adjudication["appeal"] = {
            "reason": serializer.validated_data["reason"],
            "timestamp": timezone.now().isoformat(),
        }
        violation.adjudication = adjudication
        violation.save(update_fields=["adjudication"])
        return Response(ViolationEventSerializer(violation).data)


class AuditLogListView(APIView):
    """GET /api/v1/audit-logs/ — admin-only audit trail (optional wiring)."""

    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        logs = AuditLog.objects.select_related("actor").order_by("-created_at")[:200]
        return Response(AuditLogSerializer(logs, many=True).data)
