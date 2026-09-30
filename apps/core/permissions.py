"""Role-based permission classes mirroring the ExamLock permission matrix.

Every API viewset picks from these; the UI never shows a control the API
would not authorise.
"""
from rest_framework.permissions import BasePermission


class IsAdmin(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == "admin")


class IsInstructor(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == "instructor")


class IsStudent(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == "student")


class IsAdminOrInstructor(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role in ("admin", "instructor")
        )


class IsOwnerOrAdmin(BasePermission):
    """Object-level: the object's owner/author may act, or any admin."""

    def has_object_permission(self, request, view, obj):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.user.role == "admin":
            return True
        owner = getattr(obj, "owner", None) or getattr(obj, "author", None)
        return owner is not None and owner.pk == request.user.pk


# Combined permissions. DRF's `|` operator works on permission *classes*
# (via the BasePermission metaclass), not on instances — so build the
# combined class once here and instantiate it where needed.
IsInstructorOrAdmin = IsInstructor | IsAdmin
IsAnyRole = IsStudent | IsInstructor | IsAdmin
