# ExamLock — Online Exam & Quiz Platform with Anti-Cheat

A timed, role-based examination platform built with Django and Django REST
Framework. Instructors author question banks and build timed exams; students
sit them in an integrity-monitored exam room. **The browser detects, the
server decides**: client-side signals (tab switches, fullscreen exits,
copy/paste) are logged as evidence, but every integrity decision — deadlines,
write gating, grading — is enforced server-side.

Final-year project of Hazrat Bilal.

## Quick start (no Docker needed)

```bash
cd examlock
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python manage.py migrate
python manage.py seed_demo        # demo users + sample exam
python manage.py runserver
```

Open http://127.0.0.1:8000/ and log in:

| Role       | Email                     | Password      |
|------------|---------------------------|---------------|
| Admin      | admin@examlock.local      | admin123      |
| Instructor | instructor@examlock.local | instructor123 |
| Student    | student@examlock.local    | student123    |

The seeded exam **"Django Fundamentals Quiz"** is published and the student is
enrolled — log in as the student, hit **Start exam**, and try leaving
fullscreen to see a violation event land in the instructor's review queue.

Run the test suite:

```bash
python manage.py test
```

## Full stack with Docker (Postgres + Redis + Celery)

```bash
cp .env.example .env
# edit .env — set a real SECRET_KEY
docker compose up --build
```

Then in another terminal:

```bash
docker compose exec web python manage.py seed_demo
```

Services: web on :8000, postgres :5432, redis :6379, celery worker, celery beat
(auto-submits expired attempts every minute).

## Project layout

```
examlock/
├── config/                 # settings, urls, celery app, wsgi/asgi
├── apps/
│   ├── accounts/           # custom User model (roles: admin/instructor/student), JWT auth
│   ├── core/               # role-based DRF permission classes
│   ├── banks/              # subjects, question banks, questions, choices
│   ├── exams/              # exams, question pinning/pools, enrollments, publish flow
│   ├── attempts/           # attempt lifecycle, write-gate, violations, grading, analytics,
│   │                       # celery tasks, seed_demo command
│   └── web/                # server-rendered UI: dashboard, exam room, violation review
├── templates/              # Django templates
├── static/js/exam-room.js  # integrity agent: fullscreen, tab-switch, copy/paste,
│                           # devtools heuristic, heartbeat, autosave, countdown
├── requirements.txt  Dockerfile  docker-compose.yml  .env.example
```

## API

Versioned at `/api/v1/`. JWT auth on every endpoint except login/refresh.
Interactive docs at `/api/docs/` (drf-spectacular).

Key endpoints:

```
POST /api/v1/auth/login/                 email + password → access/refresh tokens
GET  /api/v1/auth/me/
POST /api/v1/exams/{id}/attempts/        start attempt → {attempt_id, token, deadline}
GET  /api/v1/attempts/{id}/status        server-now, remaining seconds (time authority)
POST /api/v1/attempts/{id}/answers       autosave (seq + idempotency key + X-Attempt-Token)
POST /api/v1/attempts/{id}/heartbeat     aliveness + device/session lock
POST /api/v1/attempts/{id}/violations    integrity beacon (batch client events)
POST /api/v1/attempts/{id}/submit        two-phase final submit {confirm: true}
POST /api/v1/attempts/{id}/grade         auto-grade objective questions
POST /api/v1/violations/{id}/adjudicate  uphold / dismiss / penalize + reason
GET  /api/v1/exams/{id}/analytics        distribution, per-question stats
GET  /api/v1/exams/{id}/results/export   CSV grade book
```

## Anti-cheat design (summary)

1. **Signed attempt tokens** — issued at start, presented on every write.
2. **Server-stamped time** — deadline materialised at start; `/status` is the
   only clock the client may display.
3. **Write gating in transactions** — every answer write re-reads the attempt
   row (`SELECT … FOR UPDATE`); late, submitted, or token-mismatched writes are
   rejected and logged.
4. **Idempotency + monotonic seq** — replayed autosaves are deduplicated,
   out-of-order writes rejected.
5. **Append-only evidence** — violation events are insert-only; adjudication
   appends a decision record, never edits history.
6. **Automatic expiry** — Celery beat server-submits attempts past deadline
   even if the client never reconnects.

Honest scope (also in the thesis): browser signals are advisory and can be
bypassed by a determined attacker. The hard guarantees — timing, write
gating, evidence — are server-side.

## Environment variables

See `.env.example`. Nothing secret is committed; all credentials come from the
environment.
