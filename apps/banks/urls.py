"""URL routes for the banks app (mounted under /api/v1/ by the project)."""

from rest_framework.routers import DefaultRouter

from .views import QuestionBankViewSet, QuestionViewSet, SubjectViewSet

router = DefaultRouter()
router.register("question-banks", QuestionBankViewSet, basename="questionbank")
router.register("questions", QuestionViewSet, basename="question")
router.register("subjects", SubjectViewSet, basename="subject")

urlpatterns = router.urls
