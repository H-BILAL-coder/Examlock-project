"""URL routes for the exams app (mounted under /api/v1/ by the project)."""

from rest_framework.routers import DefaultRouter

from .views import ExamViewSet

router = DefaultRouter()
router.register("exams", ExamViewSet, basename="exam")

urlpatterns = router.urls
