import uuid

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    email = models.EmailField(unique=True)

    class Meta(AbstractUser.Meta):
        constraints = [models.UniqueConstraint(Lower("email"), name="unique_email_casefold")]

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        old = User.objects.filter(pk=self.pk).first() if self.pk else None
        if self.pk and Voter.objects.filter(user_id=self.pk, election__activated_at__isnull=False).exists():
            if (old.username, old.email) != (self.username, self.email):
                raise ValidationError("Activated voter identity is frozen.")
        super().save(*args, **kwargs)
        if old and old.password != self.password:
            EmailDelivery.objects.filter(user=self, kind="credentials", status="pending").update(
                status="failed", encrypted_payload="", last_error="Superseded by password change")


class Election(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    name = models.CharField(_("Name"), max_length=150, default='Pemilu HMTG "GEA" ITB')
    opens_at = models.DateTimeField(_("Opens at"))
    closes_at = models.DateTimeField(_("Closes at"))
    activated_at = models.DateTimeField(null=True, blank=True, editable=False)
    paused = models.BooleanField(default=False, editable=False)
    finalized_at = models.DateTimeField(null=True, blank=True, editable=False)
    result_snapshot = models.JSONField(null=True, blank=True, editable=False)
    credentials_requested_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = _("Election")
        verbose_name_plural = _("Elections")
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="one_election"),
            models.CheckConstraint(condition=models.Q(closes_at__gt=models.F("opens_at")), name="valid_window"),
        ]
        permissions = [("operate_election", "Operate election and send credentials"),
                       ("review_ballots", "Review identity-linked ballots after closing")]

    def clean(self):
        if self.opens_at and self.closes_at:
            if (self.closes_at - self.opens_at).total_seconds() != 72 * 3600:
                raise ValidationError("The election window must be exactly 72 hours.")
        if self.pk:
            old = Election.objects.filter(pk=self.pk, activated_at__isnull=False).first()
            if old and (old.name, old.opens_at, old.closes_at) != (self.name, self.opens_at, self.closes_at):
                raise ValidationError("Activated election configuration is frozen.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def is_demo(self):
        return self.name.startswith("DEMO")

    @property
    def state(self):
        now = timezone.now()
        if not self.activated_at:
            return "draft"
        if now >= self.closes_at:
            return "closed"
        if now < self.opens_at:
            return "scheduled"
        return "paused" if self.paused else "open"

    def __str__(self):
        return self.name


class Office(models.Model):
    class Slug(models.TextChoices):
        KAHIM = "kahim", "Kahim"
        SENATOR = "senator", "Senator"

    election = models.ForeignKey(Election, on_delete=models.PROTECT, related_name="offices", verbose_name=_("Election"))
    slug = models.CharField(_("Office"), max_length=10, choices=Slug.choices)

    class Meta:
        verbose_name = _("Office")
        verbose_name_plural = _("Offices")
        constraints = [models.UniqueConstraint(fields=["election", "slug"], name="unique_office")]
        ordering = ["slug"]

    def __str__(self):
        return self.get_slug_display()


class Candidate(models.Model):
    office = models.ForeignKey(Office, on_delete=models.PROTECT, related_name="candidates", verbose_name=_("Office"))
    number = models.PositiveSmallIntegerField(_("Candidate number"), choices=[(1, "1"), (2, "2")])
    name = models.CharField(_("Name"), max_length=150)
    profile_id = models.TextField(_("Profile (Indonesian)"))
    profile_en = models.TextField(_("Profile (English)"))

    class Meta:
        verbose_name = _("Candidate")
        verbose_name_plural = _("Candidates")
        constraints = [models.UniqueConstraint(fields=["office", "number"], name="unique_candidate_number"),
                       models.CheckConstraint(condition=models.Q(number__in=[1, 2]), name="two_candidate_numbers")]
        ordering = ["number"]

    @property
    def profile(self):
        return self.profile_en if get_language() == "en" else self.profile_id

    def __str__(self):
        return f"{self.office} / {self.number}. {self.name}"


class Voter(models.Model):
    election = models.ForeignKey(Election, on_delete=models.PROTECT, related_name="voters")
    user = models.OneToOneField(User, on_delete=models.PROTECT, related_name="voter")
    display_name = models.CharField(max_length=150, blank=True)
    credentials_issued_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = _("Voter")
        verbose_name_plural = _("Voters")

    def __str__(self):
        return self.user.username


class Vote(models.Model):
    election = models.ForeignKey(Election, on_delete=models.PROTECT)
    voter = models.ForeignKey(Voter, on_delete=models.PROTECT, related_name="votes")
    office = models.ForeignKey(Office, on_delete=models.PROTECT)
    candidate = models.ForeignKey(Candidate, on_delete=models.PROTECT)
    submitted_at = models.DateTimeField()
    receipt_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    request_id = models.UUIDField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["election", "voter", "office"], name="one_vote_per_office")]
        default_permissions = ()

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Votes are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Votes are immutable.")


class EmailDelivery(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        SENT = "sent", _("Sent")
        FAILED = "failed", _("Failed / review required")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.PROTECT)
    kind = models.CharField(max_length=20, choices=[("credentials", "Credentials"), ("reset", "Password reset")])
    encrypted_payload = models.TextField(editable=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    attempts = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    provider_id = models.CharField(max_length=100, blank=True)
    last_error = models.CharField(max_length=100, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    bounced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Email delivery")
        verbose_name_plural = _("Email deliveries")
        indexes = [models.Index(fields=["status", "next_attempt_at"])]


class AuditEvent(models.Model):
    actor = models.ForeignKey(User, null=True, on_delete=models.PROTECT)
    action = models.CharField(max_length=80)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = _("Audit event")
        verbose_name_plural = _("Audit events")


class ThrottleBucket(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    window = models.BigIntegerField()
    count = models.PositiveIntegerField(default=0)
