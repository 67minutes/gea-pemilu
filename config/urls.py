from django.contrib.auth import views as auth_views
from django.urls import include, path

from elections import views
from elections.admin import site
from elections.forms import ApprovedPasswordResetForm
from elections.webhooks import resend_events

urlpatterns = [
    path("i18n/", include("django.conf.urls.i18n")),
    path("", views.home, name="home"),
    path("login/", views.VoterLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("vote/<slug:slug>/", views.ballot, name="ballot"),
    path("receipt/<uuid:receipt_id>/", views.receipt, name="receipt"),
    path("results/", views.results, name="results"),
    path("about/", views.about, name="about"),
    path("helpdesk/", views.helpdesk, name="helpdesk"),
    path("password-reset/", auth_views.PasswordResetView.as_view(form_class=ApprovedPasswordResetForm,
         email_template_name="registration/password_reset_email.txt"), name="password_reset"),
    path("password-reset/sent/", auth_views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path("password-reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("password-reset/complete/", auth_views.PasswordResetCompleteView.as_view(), name="password_reset_complete"),
    path("committee/", views.committee, name="committee"),
    path("committee/import/", views.roster_import, name="roster_import"),
    path("committee/action/", views.committee_action, name="committee_action"),
    path("committee/export/<slug:kind>/", views.export_results, name="export_results"),
    path("committee/admin/", site.urls),
    path("health/live/", views.health_live, name="health_live"),
    path("health/ready/", views.health_ready, name="health_ready"),
    path("webhooks/resend/", resend_events, name="resend_events"),
]
