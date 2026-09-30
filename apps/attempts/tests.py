"""ExamLock test suite — grading engine, deadline enforcement, permissions,
idempotency, and the violation pipeline."""
import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.attempts.models import Answer, Attempt, ViolationEvent
from apps.attempts.services import grade_attempt
from apps.banks.models import Choice, Question, QuestionBank, Subject
from apps.exams.models import Enrollment, Exam, ExamQuestion

User = get_user_model()

BASE_POLICY = {
    "watched_events": ["tab_hidden", "focus_lost", "fullscreen_exit"],
    "severity_map": {"tab_hidden": "medium", "focus_lost": "low", "fullscreen_exit": "medium"},
    "points": {"low": 1, "medium": 3, "high": 10},
    "warn_threshold": 5,
    "flag_threshold": 15,
    "negative_marking": {"enabled": False, "penalty_fraction": 0.25},
}


def make_user(email, role, password="pass1234"):
    return User.objects.create_user(email=email, password=password, full_name=email.split("@")[0], role=role)


def make_question(bank, qtype, marks, choices, topic="django", difficulty="medium"):
    q = Question.objects.create(
        bank=bank, qtype=qtype, stem=f"Stem for {qtype} {uuid.uuid4().hex[:6]}",
        topic=topic, difficulty=difficulty, marks=Decimal(str(marks)),
    )
    for i, (text, correct) in enumerate(choices):
        Choice.objects.create(question=q, text=text, is_correct=correct, sort_order=i)
    return q


class ExamLockTestBase(TestCase):
    def setUp(self):
        self.admin = make_user("admin@t.local", "admin")
        self.instructor = make_user("instructor@t.local", "instructor")
        self.student = make_user("student@t.local", "student")
        self.other_student = make_user("other@t.local", "student")

        self.subject = Subject.objects.create(name="Computer Science")
        self.bank = QuestionBank.objects.create(owner=self.instructor, name="Bank 1")

        self.q_single = make_question(self.bank, "mcq_single", 2, [("A", True), ("B", False), ("C", False)])
        self.q_multi = make_question(self.bank, "mcq_multi", 4, [("A", True), ("B", True), ("C", False)])
        self.q_tf = make_question(self.bank, "true_false", 1, [("True", True), ("False", False)])
        self.q_short = make_question(self.bank, "short_answer", 5, [])

        self.exam = Exam.objects.create(
            author=self.instructor, title="Quiz 1", duration_minutes=30,
            opens_at=timezone.now() - timedelta(hours=1),
            closes_at=timezone.now() + timedelta(days=1),
            attempts_allowed=1, policy=dict(BASE_POLICY), status="published",
        )
        for i, q in enumerate([self.q_single, self.q_multi, self.q_tf, self.q_short]):
            ExamQuestion.objects.create(exam=self.exam, question=q, question_version=q.version, sort_order=i)
        Enrollment.objects.create(exam=self.exam, student=self.student)

        self.client = APIClient()

    def auth(self, user):
        self.client.force_authenticate(user=user)

    def start_attempt(self, student=None, exam=None):
        student = student or self.student
        exam = exam or self.exam
        self.auth(student)
        res = self.client.post(f"/api/v1/exams/{exam.id}/attempts/", {}, format="json")
        assert res.status_code == 201, res.content
        data = res.json()
        return data["attempt_id"], data["token"]

    def answer_headers(self, token):
        return {"HTTP_X_ATTEMPT_TOKEN": token}

    def post_answers(self, attempt_id, token, items, seq=1, key=None):
        return self.client.post(
            f"/api/v1/attempts/{attempt_id}/answers/",
            {"seq": seq, "idempotency_key": key or uuid.uuid4().hex, "items": items},
            format="json",
            **self.answer_headers(token),
        )

    def correct_ids(self, question):
        return [str(c.id) for c in question.choices.filter(is_correct=True)]


class GradingTests(ExamLockTestBase):
    def _attempt_with_answers(self, answer_map):
        attempt_id, token = self.start_attempt()
        items = []
        for q, payload in answer_map.items():
            items.append({"question_id": str(q.id), **payload})
        res = self.post_answers(attempt_id, token, items)
        assert res.status_code == 200, res.content
        return Attempt.objects.get(pk=attempt_id)

    def test_mcq_single_correct(self):
        attempt = self._attempt_with_answers({self.q_single: {"choice_ids": self.correct_ids(self.q_single)}})
        result = grade_attempt(attempt)
        self.assertEqual(Decimal(result["total"]), Decimal("2"))

    def test_mcq_single_wrong_no_negative_marking(self):
        wrong = [str(c.id) for c in self.q_single.choices.filter(is_correct=False)][:1]
        attempt = self._attempt_with_answers({self.q_single: {"choice_ids": wrong}})
        result = grade_attempt(attempt)
        self.assertEqual(Decimal(result["total"]), Decimal("0"))

    def test_mcq_single_wrong_with_negative_marking(self):
        self.exam.policy["negative_marking"]["enabled"] = True
        self.exam.save()
        wrong = [str(c.id) for c in self.q_single.choices.filter(is_correct=False)][:1]
        attempt = self._attempt_with_answers({self.q_single: {"choice_ids": wrong}})
        result = grade_attempt(attempt)
        # -2 marks * 0.25 penalty
        self.assertEqual(Decimal(result["total"]), Decimal("-0.50"))

    def test_mcq_multi_exact_match(self):
        attempt = self._attempt_with_answers({self.q_multi: {"choice_ids": self.correct_ids(self.q_multi)}})
        result = grade_attempt(attempt)
        self.assertEqual(Decimal(result["total"]), Decimal("4"))

    def test_mcq_multi_partial_is_wrong(self):
        partial = self.correct_ids(self.q_multi)[:1]
        attempt = self._attempt_with_answers({self.q_multi: {"choice_ids": partial}})
        result = grade_attempt(attempt)
        self.assertEqual(Decimal(result["total"]), Decimal("0"))

    def test_true_false(self):
        attempt = self._attempt_with_answers({self.q_tf: {"choice_ids": self.correct_ids(self.q_tf)}})
        result = grade_attempt(attempt)
        self.assertEqual(Decimal(result["total"]), Decimal("1"))

    def test_short_answer_left_ungraded(self):
        attempt = self._attempt_with_answers({self.q_short: {"text": "Django is a web framework"}})
        result = grade_attempt(attempt)
        ans = Answer.objects.get(attempt=attempt, question=self.q_short)
        self.assertFalse(ans.is_graded)
        self.assertIsNone(ans.marks_awarded)
        self.assertEqual(Decimal(result["total"]), Decimal("0"))

    def test_manual_marking_recomputes_snapshot(self):
        attempt = self._attempt_with_answers({
            self.q_single: {"choice_ids": self.correct_ids(self.q_single)},
            self.q_short: {"text": "good answer"},
        })
        grade_attempt(attempt)
        ans = Answer.objects.get(attempt=attempt, question=self.q_short)
        self.auth(self.instructor)
        res = self.client.post(
            f"/api/v1/attempts/{attempt.id}/answers/{ans.id}/mark/",
            {"marks_awarded": "4.5"}, format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        attempt.refresh_from_db()
        self.assertEqual(attempt.score_snapshot, Decimal("6.50"))

    def test_student_cannot_mark(self):
        attempt = self._attempt_with_answers({self.q_short: {"text": "x"}})
        ans = Answer.objects.get(attempt=attempt, question=self.q_short)
        self.auth(self.student)
        res = self.client.post(
            f"/api/v1/attempts/{attempt.id}/answers/{ans.id}/mark/",
            {"marks_awarded": "5"}, format="json",
        )
        self.assertEqual(res.status_code, 403)


class DeadlineEnforcementTests(ExamLockTestBase):
    def test_answers_rejected_after_deadline(self):
        attempt_id, token = self.start_attempt()
        attempt = Attempt.objects.get(pk=attempt_id)
        attempt.deadline = timezone.now() - timedelta(seconds=1)
        attempt.save()
        res = self.post_answers(
            attempt_id, token,
            [{"question_id": str(self.q_single.id), "choice_ids": self.correct_ids(self.q_single)}],
        )
        self.assertEqual(res.status_code, 409)
        self.assertEqual(Answer.objects.filter(attempt_id=attempt_id).count(), 0)

    def test_submit_after_deadline_marks_expired(self):
        attempt_id, token = self.start_attempt()
        attempt = Attempt.objects.get(pk=attempt_id)
        attempt.deadline = timezone.now() - timedelta(seconds=1)
        attempt.save()
        self.auth(self.student)
        res = self.client.post(
            f"/api/v1/attempts/{attempt_id}/submit/", {"confirm": True}, format="json",
            **self.answer_headers(token),
        )
        self.assertEqual(res.status_code, 200)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "expired")

    def test_status_reports_server_time(self):
        attempt_id, token = self.start_attempt()
        self.auth(self.student)
        res = self.client.get(f"/api/v1/attempts/{attempt_id}/status/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("remaining_seconds", data)
        self.assertIn("server_now", data)
        self.assertGreater(data["remaining_seconds"], 0)
        self.assertLessEqual(data["remaining_seconds"], 30 * 60)


class AttemptSecurityTests(ExamLockTestBase):
    def test_wrong_attempt_token_rejected(self):
        attempt_id, _token = self.start_attempt()
        res = self.post_answers(
            attempt_id, "bogus-token",
            [{"question_id": str(self.q_single.id), "choice_ids": []}],
        )
        self.assertEqual(res.status_code, 403)

    def test_double_start_rejected(self):
        self.start_attempt()
        self.auth(self.student)
        res = self.client.post(f"/api/v1/exams/{self.exam.id}/attempts/", {}, format="json")
        self.assertEqual(res.status_code, 409)

    def test_other_student_cannot_read_attempt(self):
        attempt_id, _token = self.start_attempt()
        self.auth(self.other_student)
        res = self.client.get(f"/api/v1/attempts/{attempt_id}/")
        self.assertEqual(res.status_code, 404)

    def test_idempotent_answers_no_duplicates(self):
        attempt_id, token = self.start_attempt()
        key = uuid.uuid4().hex
        items = [{"question_id": str(self.q_single.id), "choice_ids": self.correct_ids(self.q_single)}]
        r1 = self.post_answers(attempt_id, token, items, seq=1, key=key)
        r2 = self.post_answers(attempt_id, token, items, seq=1, key=key)
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json().get("deduplicated"))
        self.assertEqual(Answer.objects.filter(attempt_id=attempt_id, question=self.q_single).count(), 1)

    def test_stale_seq_rejected(self):
        attempt_id, token = self.start_attempt()
        items = [{"question_id": str(self.q_single.id), "choice_ids": []}]
        self.post_answers(attempt_id, token, items, seq=2)
        res = self.post_answers(attempt_id, token, items, seq=1, key=uuid.uuid4().hex)
        self.assertEqual(res.status_code, 409)

    def test_answer_revisions_append(self):
        attempt_id, token = self.start_attempt()
        items = [{"question_id": str(self.q_single.id), "choice_ids": []}]
        self.post_answers(attempt_id, token, items, seq=1)
        self.post_answers(attempt_id, token, items, seq=2, key=uuid.uuid4().hex)
        self.assertEqual(Answer.objects.filter(attempt_id=attempt_id, question=self.q_single).count(), 2)


class ViolationPipelineTests(ExamLockTestBase):
    def test_beacon_records_severity_from_policy(self):
        attempt_id, token = self.start_attempt()
        self.auth(self.student)
        res = self.client.post(
            f"/api/v1/attempts/{attempt_id}/violations/",
            {"events": [{"event_type": "tab_hidden", "client_ts": timezone.now().isoformat(), "payload": {}}]},
            format="json", **self.answer_headers(token),
        )
        self.assertEqual(res.status_code, 200)
        ev = ViolationEvent.objects.get(attempt_id=attempt_id)
        self.assertEqual(ev.severity, "medium")  # from BASE_POLICY severity_map
        self.assertIsNotNone(ev.server_ts)

    def test_flag_threshold_flags_attempt(self):
        attempt_id, token = self.start_attempt()
        self.auth(self.student)
        events = [
            {"event_type": "tab_hidden", "client_ts": timezone.now().isoformat(), "payload": {}}
            for _ in range(6)  # 6 x 3 points = 18 >= 15 flag_threshold
        ]
        res = self.client.post(
            f"/api/v1/attempts/{attempt_id}/violations/", {"events": events},
            format="json", **self.answer_headers(token),
        )
        self.assertEqual(res.status_code, 200)
        attempt = Attempt.objects.get(pk=attempt_id)
        self.assertTrue(attempt.flagged)

    def test_violations_are_append_only(self):
        attempt_id, token = self.start_attempt()
        self.auth(self.student)
        self.client.post(
            f"/api/v1/attempts/{attempt_id}/violations/",
            {"events": [{"event_type": "focus_lost", "client_ts": timezone.now().isoformat(), "payload": {}}]},
            format="json", **self.answer_headers(token),
        )
        ev = ViolationEvent.objects.get(attempt_id=attempt_id)
        # no update/delete path is exposed; adjudication appends a decision record
        self.auth(self.instructor)
        res = self.client.post(
            f"/api/v1/violations/{ev.id}/adjudicate/",
            {"decision": "dismiss", "reason": "brief, accidental"}, format="json",
        )
        self.assertEqual(res.status_code, 200)
        ev.refresh_from_db()
        self.assertEqual(ev.adjudication["decision"], "dismiss")
        self.assertEqual(ev.event_type, "focus_lost")  # original event untouched

    def test_student_appeal_once(self):
        attempt_id, token = self.start_attempt()
        self.auth(self.student)
        self.client.post(
            f"/api/v1/attempts/{attempt_id}/violations/",
            {"events": [{"event_type": "focus_lost", "client_ts": timezone.now().isoformat(), "payload": {}}]},
            format="json", **self.answer_headers(token),
        )
        ev = ViolationEvent.objects.get(attempt_id=attempt_id)
        self.auth(self.instructor)
        self.client.post(f"/api/v1/violations/{ev.id}/adjudicate/",
                         {"decision": "uphold", "reason": "clear"}, format="json")
        self.auth(self.student)
        r1 = self.client.post(f"/api/v1/violations/{ev.id}/appeal/", {"reason": "my cat"}, format="json")
        r2 = self.client.post(f"/api/v1/violations/{ev.id}/appeal/", {"reason": "again"}, format="json")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 400)


class PermissionTests(ExamLockTestBase):
    def test_unauthenticated_blocked(self):
        res = self.client.get("/api/v1/question-banks/")
        self.assertEqual(res.status_code, 401)

    def test_student_cannot_create_bank(self):
        self.auth(self.student)
        res = self.client.post("/api/v1/question-banks/", {"name": "x"}, format="json")
        self.assertEqual(res.status_code, 403)

    def test_student_cannot_create_exam(self):
        self.auth(self.student)
        res = self.client.post("/api/v1/exams/", {"title": "x"}, format="json")
        self.assertEqual(res.status_code, 403)

    def test_instructor_cannot_start_attempt(self):
        self.auth(self.instructor)
        res = self.client.post(f"/api/v1/exams/{self.exam.id}/attempts/", {}, format="json")
        self.assertEqual(res.status_code, 403)

    def test_instructor_cannot_edit_others_bank(self):
        other = make_user("other-inst@t.local", "instructor")
        bank = QuestionBank.objects.create(owner=other, name="theirs")
        self.auth(self.instructor)
        res = self.client.patch(f"/api/v1/question-banks/{bank.id}/", {"name": "mine"}, format="json")
        self.assertEqual(res.status_code, 404)

    def test_student_cannot_list_users(self):
        self.auth(self.student)
        res = self.client.get("/api/v1/users/")
        self.assertEqual(res.status_code, 403)

    def test_admin_can_list_users(self):
        self.auth(self.admin)
        res = self.client.get("/api/v1/users/")
        self.assertEqual(res.status_code, 200)


class PublishFlowTests(ExamLockTestBase):
    def test_publish_validates_exam(self):
        self.auth(self.instructor)
        exam = Exam.objects.create(
            author=self.instructor, title="Empty", duration_minutes=30,
            opens_at=timezone.now() - timedelta(hours=1),
            closes_at=timezone.now() + timedelta(days=1),
            status="draft",
        )
        res = self.client.post(f"/api/v1/exams/{exam.id}/publish/", {}, format="json")
        self.assertEqual(res.status_code, 400)  # no questions, no enrollments

    def test_delete_question_blocked_when_in_published_exam(self):
        self.auth(self.instructor)
        res = self.client.delete(f"/api/v1/questions/{self.q_single.id}/")
        self.assertEqual(res.status_code, 400)

    def test_question_edit_bumps_version(self):
        self.auth(self.instructor)
        res = self.client.patch(
            f"/api/v1/questions/{self.q_tf.id}/", {"stem": "Is the sky blue?"}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.q_tf.refresh_from_db()
        self.assertEqual(self.q_tf.version, 2)
