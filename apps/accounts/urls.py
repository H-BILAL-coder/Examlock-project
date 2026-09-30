from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import TokenBlacklistView, TokenObtainPairView, TokenRefreshView, UserViewSet, me

router = DefaultRouter()
router.register("users", UserViewSet, basename="user")

urlpatterns = [
    path("auth/login/", TokenObtainPairView.as_view(), name="token-obtain"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("auth/logout/", TokenBlacklistView.as_view(), name="token-blacklist"),
    path("auth/me/", me, name="auth-me"),
    path("", include(router.urls)),
]
