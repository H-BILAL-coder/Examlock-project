"""URL wiring for the attempts engine.

The parent project should include this module under ``/api/v1/``::

    path("api/v1/", include("apps.attempts.urls"))
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AttemptViewSet,
    AuditLogListView,
    ExamAnalyticsView,
    ExamResultsView,
    ExportResultsView,
    PublishResultsView,
    StartAttemptView,
    ViolationViewSet,
)

app_name = "attempts"

router = DefaultRouter()
router.register("attempts", AttemptViewSet, basename="attempt")
router.register("violations", ViolationViewSet, basename="violation")

urlpatterns = [
    path("exams/<uuid:exam_id>/attempts/", StartAttemptView.as_view(), name="attempt-start"),
    path("exams/<uuid:exam_id>/results/", ExamResultsView.as_view(), name="exam-results"),
    path("exams/<uuid:exam_id>/results/publish/", PublishResultsView.as_view(), name="exam-results-publish"),
    path("exams/<uuid:exam_id>/results/export/", ExportResultsView.as_view(), name="exam-results-export"),
    path("exams/<uuid:exam_id>/analytics/", ExamAnalyticsView.as_view(), name="exam-analytics"),
    path("audit-logs/", AuditLogListView.as_view(), name="audit-logs"),
    path("", include(router.urls)),
]
