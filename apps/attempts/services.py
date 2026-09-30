"""Grading and enforcement helpers for the attempts engine.

All grading is server-side: the client never sees correct answers and never
computes marks. Objective questions are auto-graded; short answers keep
whatever marks a human grader assigned (or stay ungraded).
"""

import hashlib
import hmac
from decimal import Decimal, InvalidOperation

from django.utils import timezone

OBJECTIVE_QTYPES = ("mcq_single", "mcq_multi", "true_false")


# ---------------------------------------------------------------------------
# Attempt token enforcement
# ---------------------------------------------------------------------------

def hash_attempt_token(raw_token: str) -> str:
    """Hash a raw attempt token for storage (SHA-256 hex digest)."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def validate_attempt_token(attempt, presented) -> bool:
    """Constant-time check of the ``X-Attempt-Token`` header value."""
    if not presented:
        return False
    return hmac.compare_digest(hash_attempt_token(str(presented)), attempt.token_hash or "")


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

def _to_decimal(value, default=Decimal("0")):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _full_marks(question, exam_question):
    if exam_question is not None and exam_question.marks_override is not None:
        return exam_question.marks_override
    return question.marks


def _negative_penalty_fraction(exam):
    """Return the negative-marking penalty fraction, or None if disabled."""
    policy = exam.policy or {}
    negative = policy.get("negative_marking") or {}
    if negative.get("enabled"):
        return _to_decimal(negative.get("penalty_fraction", 0))
    return None


def _latest_answers_by_question(attempt, question_ids):
    """Map str(question_id) -> latest Answer row for this attempt."""
    answers = attempt.answers.filter(question_id__in=question_ids).order_by("question_id", "-revision")
    latest = {}
    for answer in answers:
        key = str(answer.question_id)
        if key not in latest:
            latest[key] = answer
    return latest


def grade_attempt(attempt):
    """Grade every question in the attempt's question order.

    Idempotent: re-running simply rewrites the same marks. Objective questions
    (mcq_single / mcq_multi / true_false) use exact-set matching against the
    correct choice ids. Wrong answers score 0, or a negative penalty when the
    exam policy enables negative marking. Short answers are left untouched so
    manual grades are preserved.

    Returns a breakdown dict with per-question marks and the total.
    """
    from apps.banks.models import Question
    from apps.exams.models import ExamQuestion

    exam = attempt.exam
    question_ids = (attempt.question_order or {}).get("questions", []) or []

    questions = {
        str(pk): q for pk, q in Question.objects.filter(id__in=question_ids).in_bulk().items()
    }
    exam_questions = {
        str(eq.question_id): eq
        for eq in ExamQuestion.objects.filter(exam=exam, question_id__in=question_ids)
    }
    latest = _latest_answers_by_question(attempt, question_ids)
    penalty_fraction = _negative_penalty_fraction(exam)

    per_question = {}
    total = Decimal("0")
    for qid in question_ids:
        question = questions.get(qid)
        if question is None:
            continue
        full_marks = _full_marks(question, exam_questions.get(qid))
        answer = latest.get(qid)
        if answer is None:
            per_question[qid] = {
                "marks_awarded": "0",
                "full_marks": str(full_marks),
                "is_graded": False,
                "answered": False,
            }
            continue

        if question.qtype in OBJECTIVE_QTYPES:
            correct_ids = {str(c.id) for c in question.choices.filter(is_correct=True)}
            selected = {str(c) for c in (answer.selected_choice_ids or [])}
            if selected == correct_ids:
                awarded = full_marks
            elif penalty_fraction is not None:
                awarded = -(full_marks * penalty_fraction)
            else:
                awarded = Decimal("0")
            answer.marks_awarded = awarded
            answer.is_graded = True
            answer.save(update_fields=["marks_awarded", "is_graded"])
        # short_answer: keep any existing manual marks_awarded / is_graded as-is.

        if answer.marks_awarded is not None:
            total += answer.marks_awarded
        per_question[qid] = {
            "marks_awarded": str(answer.marks_awarded) if answer.marks_awarded is not None else None,
            "full_marks": str(full_marks),
            "is_graded": answer.is_graded,
            "answered": True,
        }

    attempt.score_snapshot = total
    attempt.save(update_fields=["score_snapshot"])
    return {
        "attempt_id": str(attempt.id),
        "total": str(total),
        "graded_at": timezone.now().isoformat(),
        "per_question": per_question,
    }


def recompute_score_snapshot(attempt):
    """Recompute ``score_snapshot`` from the latest revision of each answer.

    Used after manual marking; does not touch per-answer grading state.
    """
    question_ids = (attempt.question_order or {}).get("questions", []) or []
    latest = _latest_answers_by_question(attempt, question_ids)
    total = Decimal("0")
    for answer in latest.values():
        if answer.marks_awarded is not None:
            total += answer.marks_awarded
    attempt.score_snapshot = total
    attempt.save(update_fields=["score_snapshot"])
    return total
