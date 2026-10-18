from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models


class Student(models.Model):
    """A student record. The optional User link enables student self-service."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="student_profile")
    student_number = models.CharField(max_length=30, unique=True)
    first_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(blank=True)
    contact_number = models.CharField(max_length=30, blank=True)
    program = models.CharField(max_length=150)
    year_level = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(6)])
    academic_year = models.CharField(max_length=20, help_text="Example: 2026-2027")
    adviser = models.ForeignKey("Adviser", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="students")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return f"{self.last_name}, {self.first_name} ({self.student_number})"

    @property
    def full_name(self):
        return " ".join(part for part in [self.first_name, self.middle_name, self.last_name] if part).strip()

    @property
    def thesis_status(self):
        # A simple display status derived from the latest record in the workflow.
        topic = self.topics.order_by("-submitted_at").first()
        if not topic:
            return "Not yet assigned"
        if topic.status != ThesisTopic.Status.APPROVED:
            return f"Topic: {topic.get_status_display()}"
        proposal = topic.proposals.order_by("-version").first()
        if not proposal:
            return "Topic approved"
        if proposal.status != ThesisProposal.Status.APPROVED:
            return f"Proposal: {proposal.get_status_display()}"
        defense = proposal.defenses.order_by("-defense_date").first()
        if not defense:
            return "Proposal approved"
        # The official coordinator-approved decision is more reliable than one panelist's
        # individual decision. Individual evaluations remain visible on the defense page.
        if defense.final_decision:
            return defense.get_final_decision_display()
        if defense.status == Defense.Status.COMPLETED:
            return "Awaiting official defense decision"
        return f"Defense: {defense.get_status_display()}"


class Adviser(models.Model):
    """Faculty record used for advisers and other defense panel roles."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="adviser_profile")
    faculty_id = models.CharField(max_length=30, unique=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    department = models.CharField(max_length=150, blank=True)
    program = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)
    contact_number = models.CharField(max_length=30, blank=True)
    specialization = models.CharField(max_length=250, help_text="Example: Cybersecurity, Networking, Data Analytics")
    max_advisees = models.PositiveSmallIntegerField(default=10, validators=[MinValueValidator(1)])
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.faculty_id})"

    @property
    def current_advisee_count(self):
        return self.students.filter(is_active=True).count()

    @property
    def available_slots(self):
        return max(0, self.max_advisees - self.current_advisee_count)


class ThesisTopic(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = "SUBMITTED", "Submitted"
        UNDER_REVIEW = "UNDER_REVIEW", "Under review"
        FOR_REVISION = "FOR_REVISION", "For revision"
        RECOMMENDED = "RECOMMENDED", "Recommended"
        REJECTED = "REJECTED", "Rejected"
        APPROVED = "APPROVED", "Approved"

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="topics")
    title = models.CharField(max_length=250)
    background = models.TextField()
    problem_statement = models.TextField()
    objectives = models.TextField()
    proposed_technology = models.CharField(max_length=250, blank=True)
    research_area = models.CharField(max_length=150)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUBMITTED)
    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self):
        return self.title


class TopicReview(models.Model):
    class Recommendation(models.TextChoices):
        APPROVE = "APPROVE", "Recommend approval"
        REVISE = "REVISE", "Revise and resubmit"
        REJECT = "REJECT", "Reject"

    topic = models.ForeignKey(ThesisTopic, on_delete=models.CASCADE, related_name="reviews")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    comments = models.TextField()
    recommendation = models.CharField(max_length=10, choices=Recommendation.choices)
    reviewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-reviewed_at"]


class ThesisProposal(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SUBMITTED = "SUBMITTED", "Submitted"
        UNDER_REVIEW = "UNDER_REVIEW", "Under review"
        FOR_REVISION = "FOR_REVISION", "For revision"
        RESUBMITTED = "RESUBMITTED", "Resubmitted"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    topic = models.ForeignKey(ThesisTopic, on_delete=models.CASCADE, related_name="proposals")
    version = models.PositiveSmallIntegerField(default=1)
    introduction = models.TextField(blank=True)
    background = models.TextField(blank=True)
    problem_statement = models.TextField(blank=True)
    objectives = models.TextField(blank=True)
    scope_limitations = models.TextField(blank=True)
    significance = models.TextField(blank=True)
    related_literature = models.TextField(blank=True)
    methodology = models.TextField(blank=True)
    references = models.TextField(blank=True)
    adviser_comments = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["topic", "version"], name="unique_proposal_version_per_topic")]

    def __str__(self):
        return f"{self.topic.title} — Proposal v{self.version}"


class ProposalDocument(models.Model):
    proposal = models.ForeignKey(ThesisProposal, on_delete=models.CASCADE, related_name="documents")
    file = models.FileField(upload_to="proposal_documents/%Y/%m/")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    version_label = models.CharField(max_length=80, default="Version 1")
    notes = models.CharField(max_length=250, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.version_label


class ProposalReview(models.Model):
    """Keeps each proposal review as history instead of overwriting older comments."""
    class Status(models.TextChoices):
        UNDER_REVIEW = "UNDER_REVIEW", "Under review"
        FOR_REVISION = "FOR_REVISION", "For revision"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    proposal = models.ForeignKey(ThesisProposal, on_delete=models.CASCADE, related_name="reviews")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, choices=Status.choices)
    comments = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-reviewed_at"]

    def __str__(self):
        return f"{self.proposal} — {self.get_status_display()}"


class Defense(models.Model):
    class Type(models.TextChoices):
        PROPOSAL = "PROPOSAL", "Proposal defense"
        MIDTERM = "MIDTERM", "Midterm defense"
        FINAL = "FINAL", "Final defense"
        REDEFENSE = "REDEFENSE", "Re-defense"

    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED", "Scheduled"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    class Decision(models.TextChoices):
        PASS = "PASS", "Pass"
        MINOR = "MINOR", "Pass with minor revisions"
        MAJOR = "MAJOR", "Pass with major revisions"
        REDEFENSE = "REDEFENSE", "Re-defense required"
        FAIL = "FAIL", "Fail"

    proposal = models.ForeignKey(ThesisProposal, on_delete=models.PROTECT, related_name="defenses")
    defense_type = models.CharField(max_length=15, choices=Type.choices)
    defense_date = models.DateField()
    defense_time = models.TimeField()
    venue = models.CharField(max_length=200)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SCHEDULED)
    notes = models.TextField(blank=True)
    # Individual Evaluation rows belong to panel members; these fields store the official consolidated result.
    final_decision = models.CharField(max_length=12, choices=Decision.choices, blank=True)
    final_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
                                      validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))])
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_defenses")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["defense_date", "defense_time"]

    def __str__(self):
        return f"{self.get_defense_type_display()} — {self.proposal.topic.title} ({self.defense_date})"


class DefensePanel(models.Model):
    class Role(models.TextChoices):
        CHAIR = "CHAIR", "Chairperson"
        ADVISER = "ADVISER", "Adviser"
        MEMBER = "MEMBER", "Panel member"

    defense = models.ForeignKey(Defense, on_delete=models.CASCADE, related_name="panel_assignments")
    adviser = models.ForeignKey(Adviser, on_delete=models.PROTECT, related_name="panel_assignments")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["defense", "adviser"], name="unique_panel_member_per_defense")]

    def __str__(self):
        return f"{self.adviser} — {self.get_role_display()}"


class EvaluationCriterion(models.Model):
    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True)
    weight = models.DecimalField(max_digits=5, decimal_places=2,
                                 validators=[MinValueValidator(Decimal("0.01")), MaxValueValidator(Decimal("100.00"))])
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.weight}%)"


class Evaluation(models.Model):
    class Decision(models.TextChoices):
        PASS = "PASS", "Pass"
        MINOR = "MINOR", "Pass with minor revisions"
        MAJOR = "MAJOR", "Pass with major revisions"
        REDEFENSE = "REDEFENSE", "Re-defense required"
        FAIL = "FAIL", "Fail"

    defense = models.ForeignKey(Defense, on_delete=models.CASCADE, related_name="evaluations")
    evaluator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    decision = models.CharField(max_length=12, choices=Decision.choices, blank=True)
    comments = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["defense", "evaluator"], name="unique_evaluator_per_defense")]

    @property
    def weighted_score(self):
        # Each individual score is a percentage mark; the criterion weight determines its contribution.
        total = Decimal("0.00")
        for item in self.scores.select_related("criterion"):
            total += item.score * item.criterion.weight / Decimal("100")
        return total.quantize(Decimal("0.01"))

    def __str__(self):
        return f"Evaluation for {self.defense} by {self.evaluator}"


class EvaluationScore(models.Model):
    evaluation = models.ForeignKey(Evaluation, on_delete=models.CASCADE, related_name="scores")
    criterion = models.ForeignKey(EvaluationCriterion, on_delete=models.PROTECT)
    score = models.DecimalField(max_digits=5, decimal_places=2,
                                validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))])

    class Meta:
        constraints = [models.UniqueConstraint(fields=["evaluation", "criterion"], name="unique_score_per_criterion")]


class RevisionRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SUBMITTED = "SUBMITTED", "Submitted for review"
        APPROVED = "APPROVED", "Approved"
        RETURNED = "RETURNED", "Returned for more changes"

    class Priority(models.TextChoices):
        LOW = "LOW", "Low"
        NORMAL = "NORMAL", "Normal"
        HIGH = "HIGH", "High"
        URGENT = "URGENT", "Urgent"

    defense = models.ForeignKey(Defense, on_delete=models.CASCADE, related_name="revision_requests")
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="revision_requests")
    section = models.CharField(max_length=150)
    required_change = models.TextField()
    reason = models.TextField(blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="requested_revisions")
    deadline = models.DateField(null=True, blank=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    student_response = models.TextField(blank=True)
    revised_file = models.FileField(upload_to="revisions/%Y/%m/", blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["status", "deadline", "-created_at"]

    def __str__(self):
        return f"{self.section} — {self.student} ({self.get_status_display()})"


class FinalThesis(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SUBMITTED = "SUBMITTED", "Submitted"
        APPROVED = "APPROVED", "Approved"
        ARCHIVED = "ARCHIVED", "Archived"

    class Access(models.TextChoices):
        PUBLIC = "PUBLIC", "Public"
        INSTITUTION = "INSTITUTION", "Institutional users"
        FACULTY = "FACULTY", "Faculty only"
        RESTRICTED = "RESTRICTED", "Restricted"

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="final_theses")
    proposal = models.ForeignKey(ThesisProposal, on_delete=models.PROTECT, related_name="final_theses")
    title = models.CharField(max_length=250)
    abstract = models.TextField(blank=True)
    keywords = models.CharField(max_length=500, blank=True)
    research_area = models.CharField(max_length=150, blank=True)
    final_pdf = models.FileField(upload_to="final_theses/%Y/%m/", blank=True, null=True)
    # The requirements list an approval form separately from the final manuscript.
    approval_form = models.FileField(upload_to="approval_forms/%Y/%m/", blank=True, null=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    access_level = models.CharField(max_length=12, choices=Access.choices, default=Access.INSTITUTION)
    adviser_approved = models.BooleanField(default=False)
    panel_approved = models.BooleanField(default=False)
    defense_date = models.DateField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        ordering = ["-archived_at", "title"]

    def __str__(self):
        return self.title


class Notification(models.Model):
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="thesis_notifications")
    message = models.CharField(max_length=300)
    target_url = models.CharField(max_length=300, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class AuditLog(models.Model):
    """Append-only activity record to help track who changed important records."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=100)
    module = models.CharField(max_length=100)
    record_id = models.CharField(max_length=80, blank=True)
    remarks = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} ({self.module})"


class RepositoryAccess(models.Model):
    """Tracks visits/downloads of archived documents when access auditing is required."""
    class Action(models.TextChoices):
        VIEW = "VIEW", "Viewed"
        DOWNLOAD = "DOWNLOAD", "Downloaded"

    thesis = models.ForeignKey(FinalThesis, on_delete=models.CASCADE, related_name="access_records")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=10, choices=Action.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_action_display()} — {self.thesis.title}"
