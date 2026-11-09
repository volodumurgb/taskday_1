from django.contrib.auth.views import PasswordResetCompleteView, PasswordResetConfirmView
from django.urls import include, path
from inventory.views import login_view, logout_view, password_reset_view, register_view

urlpatterns = [
    path("api/auth/register/", register_view),
    path("api/auth/login/", login_view),
    path("api/auth/logout/", logout_view),
    path("api/auth/password-reset/", password_reset_view),
    path("api/auth/password-reset/complete/", PasswordResetCompleteView.as_view(), name="password_reset_complete"),
    path("api/auth/password-reset/<uidb64>/<token>/", PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("api/", include("inventory.urls")),
]

