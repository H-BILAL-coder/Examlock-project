from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from . import views

app_name = "web"

urlpatterns = [
    path("login/", LoginView.as_view(template_name="web/login.html"), name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("", views.dashboard, name="dashboard"),
    path("exams/<uuid:exam_id>/", views.exam_detail, name="exam-detail"),
    path("exams/<uuid:exam_id>/violations/", views.violation_review, name="violation-review"),
    path("violations/<uuid:violation_id>/adjudicate/", views.adjudicate_web, name="violation-adjudicate"),
    path("room/<uuid:attempt_id>/", views.exam_room, name="exam-room"),
    path("attempts/<uuid:attempt_id>/result/", views.attempt_result, name="attempt-result"),
]
