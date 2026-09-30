from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.attempts.models import Attempt, ViolationEvent
from apps.banks.models import QuestionBank
from apps.exams.models import Enrollment, Exam


@login_required
def dashboard(request):
    user = request.user
    now = timezone.now()
    ctx = {"now": now}
    if user.role == "student":
        enrollments = (
            Enrollment.objects.filter(student=user)
            .select_related("exam")
            .order_by("-invited_at")
        )
        attempts = Attempt.objects.filter(student=user).select_related("exam")
        attempts_by_exam = {}
        for a in attempts:
            attempts_by_exam.setdefault(a.exam_id, []).append(a)
        rows = []
        for e in enrollments:
            exam = e.exam
            atts = attempts_by_exam.get(exam.id, [])
            active = next((a for a in atts if a.status == "in_progress"), None)
            used = len([a for a in atts if a.status != "voided"])
            can_start = (
                exam.status == "published"
                and exam.opens_at <= now <= exam.closes_at
                and used < exam.attempts_allowed
                and active is None
            )
            rows.append(
                {
                    "exam": exam,
                    "active_attempt": active,
                    "attempts": sorted(atts, key=lambda a: a.started_at, reverse=True),
                    "used": used,
                    "can_start": can_start,
                }
            )
        ctx["rows"] = rows
    elif user.role == "instructor":
        exams = Exam.objects.filter(author=user).annotate(
            attempt_count=Count("attempts"),
            flagged_count=Count("attempts", filter=Q(attempts__flagged=True)),
        ).order_by("-created_at")
        ctx["exams"] = exams
        ctx["bank_count"] = QuestionBank.objects.filter(owner=user).count()
    else:
        ctx["stats"] = {
            "users": user.__class__.objects.count(),
            "exams": Exam.objects.count(),
            "attempts": Attempt.objects.count(),
            "violations": ViolationEvent.objects.count(),
        }
        ctx["recent_attempts"] = Attempt.objects.select_related("exam", "student").order_by("-started_at")[:10]
    return render(request, "web/dashboard.html", ctx)


@login_required
def exam_detail(request, exam_id):
    exam = get_object_or_404(Exam, pk=exam_id)
    if not (request.user.role == "admin" or exam.author_id == request.user.id):
        raise Http404
    attempts = (
        Attempt.objects.filter(exam=exam)
        .select_related("student")
        .order_by("-started_at")
    )
    violations = ViolationEvent.objects.filter(attempt__exam=exam).order_by("-server_ts")[:50]
    ctx = {
        "exam": exam,
        "questions": exam.exam_questions.select_related("question").order_by("sort_order"),
        "enrollments": exam.enrollments.select_related("student"),
        "attempts": attempts,
        "violations": violations,
    }
    return render(request, "web/exam_detail.html", ctx)


@login_required
def exam_room(request, attempt_id):
    attempt = get_object_or_404(Attempt.objects.select_related("exam"), pk=attempt_id)
    if request.user.role != "student" or attempt.student_id != request.user.id:
        raise Http404
    if attempt.status != "in_progress":
        return redirect("web:attempt-result", attempt_id=attempt.id)
    return render(request, "web/exam_room.html", {"attempt": attempt})


@login_required
def attempt_result(request, attempt_id):
    attempt = get_object_or_404(Attempt.objects.select_related("exam"), pk=attempt_id)
    if not (
        request.user.role == "admin"
        or attempt.student_id == request.user.id
        or (request.user.role == "instructor" and attempt.exam.author_id == request.user.id)
    ):
        raise Http404
    show_answers = attempt.exam.results_published or request.user.role in ("instructor", "admin")
    answers = (
        attempt.answers.select_related("question")
        .order_by("question_id", "-revision")
    )
    # latest revision per question
    latest = {}
    for a in answers:
        latest.setdefault(a.question_id, a)
    violations = attempt.violations.order_by("server_ts") if request.user.role != "student" else []
    return render(
        request,
        "web/attempt_result.html",
        {
            "attempt": attempt,
            "answers": latest.values(),
            "show_answers": show_answers,
            "violations": violations,
        },
    )


@login_required
def violation_review(request, exam_id):
    exam = get_object_or_404(Exam, pk=exam_id)
    if not (request.user.role == "admin" or exam.author_id == request.user.id):
        raise Http404
    qs = ViolationEvent.objects.filter(attempt__exam=exam).select_related("attempt", "attempt__student")
    decision = request.GET.get("decision", "")
    if decision == "pending":
        qs = qs.filter(adjudication__isnull=True)
    elif decision in ("upheld", "dismissed", "penalized"):
        qs = qs.filter(adjudication__decision={"upheld": "uphold", "dismissed": "dismiss", "penalized": "penalize"}[decision])
    violations = qs.order_by("-server_ts")[:200]
    return render(
        request, "web/violation_review.html", {"exam": exam, "violations": violations, "decision": decision}
    )


@login_required
@require_POST
def adjudicate_web(request, violation_id):
    violation = get_object_or_404(ViolationEvent.objects.select_related("attempt__exam"), pk=violation_id)
    exam = violation.attempt.exam
    if not (request.user.role == "admin" or exam.author_id == request.user.id):
        raise Http404
    decision = request.POST.get("decision")
    reason = request.POST.get("reason", "")
    if decision not in ("uphold", "dismiss", "penalize"):
        raise Http404
    violation.adjudication = {
        "decision": decision,
        "reason": reason,
        "adjudicator": request.user.email,
        "timestamp": timezone.now().isoformat(),
    }
    violation.save(update_fields=["adjudication"])
    if decision == "penalize":
        attempt = violation.attempt
        attempt.flagged = True
        attempt.flag_reason = (attempt.flag_reason + f" | penalized: {reason}").strip(" |")
        attempt.save(update_fields=["flagged", "flag_reason"])
    return redirect("web:violation-review", exam_id=exam.id)


def logout_view(request):
    auth_logout(request)
    return redirect("web:login")
