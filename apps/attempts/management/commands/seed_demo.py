"""Seed demo data for the attempts engine (idempotent).

Creates demo users, a subject, a question bank with 8 questions, a published
exam, and an enrollment. Safe to re-run: skips everything if the demo users
already exist.

NOTE: sibling apps (banks/exams/accounts) are built to the shared contract;
this command introspects their models and only passes fields that actually
exist, so minor field-name differences won't break the seed.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

DEFAULT_POLICY = {
    "negative_marking": {"enabled": False, "penalty_fraction": 0.25},
    "severity_map": {
        "tab_hidden": "medium",
        "focus_lost": "low",
        "fullscreen_exit": "medium",
        "copy_attempt": "high",
        "paste_attempt": "high",
        "devtools_signal": "high",
        "heartbeat_gap": "medium",
        "device_drift": "high",
        "ip_drift": "medium",
        "rate_limit_hit": "low",
    },
    "points": {"low": 1, "medium": 3, "high": 5},
    "flag_threshold": 10,
}

CREDENTIALS = [
    ("admin@examlock.local", "admin123", "admin", "ExamLock Admin"),
    ("instructor@examlock.local", "instructor123", "instructor", "Dana Instructor"),
    ("student@examlock.local", "student123", "student", "Ali Student"),
]

QUESTIONS = [
    {
        "qtype": "mcq_single",
        "difficulty": "easy",
        "marks": Decimal("1.00"),
        "stem": "Which command creates a new Django project?",
        "choices": [
            ("django-admin startproject mysite", True),
            ("django-admin startapp myapp", False),
            ("python manage.py runserver", False),
            ("pip install django", False),
        ],
    },
    {
        "qtype": "mcq_single",
        "difficulty": "easy",
        "marks": Decimal("1.00"),
        "stem": "In Django, which file typically defines the URL routes for an app?",
        "choices": [
            ("urls.py", True),
            ("routes.py", False),
            ("views.py", False),
            ("settings.py", False),
        ],
    },
    {
        "qtype": "mcq_single",
        "difficulty": "medium",
        "marks": Decimal("2.00"),
        "stem": "Which class do you subclass to define a database table with Django's ORM?",
        "choices": [
            ("django.db.models.Model", True),
            ("django.db.models.Schema", False),
            ("django.db.models.Table", False),
            ("django.db.models.Entity", False),
        ],
    },
    {
        "qtype": "mcq_single",
        "difficulty": "medium",
        "marks": Decimal("2.00"),
        "stem": "Which QuerySet method returns a single object and raises DoesNotExist when nothing matches?",
        "choices": [
            ("get()", True),
            ("filter()", False),
            ("first()", False),
            ("one()", False),
        ],
    },
    {
        "qtype": "mcq_multi",
        "difficulty": "medium",
        "marks": Decimal("2.00"),
        "stem": "Which of the following are valid Django model field types? (select all that apply)",
        "choices": [
            ("CharField", True),
            ("IntegerField", True),
            ("StringField", False),
            ("TextField", True),
        ],
    },
    {
        "qtype": "mcq_multi",
        "difficulty": "hard",
        "marks": Decimal("3.00"),
        "stem": "Which HTTP methods can a Django class-based view handle via handler methods? (select all that apply)",
        "choices": [
            ("GET via get()", True),
            ("POST via post()", True),
            ("SEND via send()", False),
            ("PUT via put()", True),
        ],
    },
    {
        "qtype": "true_false",
        "difficulty": "easy",
        "marks": Decimal("1.00"),
        "stem": "Django's {% csrf_token %} template tag helps protect forms against Cross-Site Request Forgery.",
        "choices": [
            ("True", True),
            ("False", False),
        ],
    },
    {
        "qtype": "short_answer",
        "difficulty": "hard",
        "marks": Decimal("3.00"),
        "stem": "Name the Django management command used to apply database migrations.",
        "choices": [],
    },
]


def _concrete_fields(model):
    return {f.name for f in model._meta.get_fields() if getattr(f, "concrete", False)}


def _filtered_create(model, **kwargs):
    """Create a row passing only kwargs that match real model fields."""
    names = _concrete_fields(model)
    data = {k: v for k, v in kwargs.items() if k in names}
    return model.objects.create(**data)


class Command(BaseCommand):
    help = "Seed demo data for the ExamLock attempts engine (idempotent)."

    def handle(self, *args, **options):
        from apps.banks.models import Choice, Question, QuestionBank
        from apps.exams.models import Enrollment, Exam, ExamQuestion

        try:
            from apps.banks.models import Subject
        except ImportError:
            Subject = None

        User = get_user_model()
        emails = [email for email, _, _, _ in CREDENTIALS]
        if User.objects.filter(email__in=emails).exists():
            self.stdout.write(self.style.WARNING("Demo users already exist; skipping seed."))
            return

        users = {}
        user_fields = _concrete_fields(User)
        for email, password, role, full_name in CREDENTIALS:
            data = {"email": email, "full_name": full_name, "role": role, "is_active": True}
            if role == "admin":
                if "is_staff" in user_fields:
                    data["is_staff"] = True
                if "is_superuser" in user_fields:
                    data["is_superuser"] = True
            user = User(**{k: v for k, v in data.items() if k in user_fields})
            user.set_password(password)
            user.save()
            users[role] = user
        instructor = users["instructor"]
        student = users["student"]

        subject = None
        if Subject is not None:
            subject = Subject.objects.filter(name="Computer Science").first()
            if subject is None:
                subject = _filtered_create(Subject, name="Computer Science", title="Computer Science")

        bank = QuestionBank.objects.filter(name="Django Basics").first()
        if bank is None:
            bank = _filtered_create(
                QuestionBank,
                name="Django Basics",
                title="Django Basics",
                owner=instructor,
                subject=subject,
            )

        questions = []
        for spec in QUESTIONS:
            question = Question.objects.filter(bank=bank, stem=spec["stem"]).first()
            if question is None:
                question = _filtered_create(
                    Question,
                    bank=bank,
                    qtype=spec["qtype"],
                    stem=spec["stem"],
                    topic="django",
                    difficulty=spec["difficulty"],
                    marks=spec["marks"],
                    version=1,
                )
                for order, (text, is_correct) in enumerate(spec["choices"]):
                    _filtered_create(
                        Choice,
                        question=question,
                        text=text,
                        is_correct=is_correct,
                        sort_order=order,
                    )
            questions.append(question)

        now = timezone.now()
        exam = Exam.objects.filter(title="Django Fundamentals Quiz").first()
        if exam is None:
            exam = _filtered_create(
                Exam,
                author=instructor,
                title="Django Fundamentals Quiz",
                description="A demo quiz covering Django fundamentals.",
                duration_minutes=30,
                opens_at=now - timedelta(hours=1),
                closes_at=now + timedelta(days=7),
                attempts_allowed=1,
                shuffle_questions=True,
                shuffle_choices=True,
                policy=DEFAULT_POLICY,
                status="published",
                results_published=False,
            )
            for order, question in enumerate(questions):
                _filtered_create(
                    ExamQuestion,
                    exam=exam,
                    question=question,
                    question_version=getattr(question, "version", 1),
                    pool_tag=None,
                    pool_draw=None,
                    marks_override=None,
                    sort_order=order,
                )

        Enrollment.objects.get_or_create(exam=exam, student=student)

        self.stdout.write(self.style.SUCCESS("Demo data seeded."))
        self.stdout.write("")
        self.stdout.write("Credentials:")
        for email, password, role, _ in CREDENTIALS:
            self.stdout.write(f"  {role:11s} {email} / {password}")
        self.stdout.write("")
        self.stdout.write(f"Exam: '{exam.title}' ({exam.id}) — published, 30 min, 1 attempt.")
