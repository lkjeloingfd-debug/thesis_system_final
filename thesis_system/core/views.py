from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import Group
from django.db import IntegrityError, transaction
from django.db.models import Q, Count
from django.http import FileResponse
from pathlib import Path
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import (
    StudentForm, AdviserForm, ThesisTopicForm, ThesisProposalForm,
    ProposalDocumentForm, DefenseForm, DefensePanelForm, EvaluationCriterionForm,
    RevisionRequestForm, StudentRevisionSubmissionForm, FinalThesisForm,
)
from .models import (
    Student, Adviser, ThesisTopic, TopicReview, ThesisProposal, ProposalReview, ProposalDocument,
    Defense, DefensePanel, EvaluationCriterion, Evaluation, EvaluationScore,
    RevisionRequest, FinalThesis, Notification, AuditLog, RepositoryAccess,
)

# These group names are also created by the setup_roles management command.
ROLES = {
    "Administrator", "Thesis Coordinator", "Adviser", "Panel Member",
    "Student", "Repository Manager",
}
MANAGEMENT_ROLES = ("Administrator", "Thesis Coordinator")
FACULTY_ROLES = ("Administrator", "Thesis Coordinator", "Adviser")
EVALUATION_ROLES = ("Administrator", "Thesis Coordinator", "Adviser", "Panel Member")
REPOSITORY_ROLES = ("Administrator", "Thesis Coordinator", "Repository Manager")


def has_role(user, roles):
    """Superusers/staff can administer the demo; other users need a matching Group."""
    if user.is_superuser or user.is_staff:
        return True
    return user.groups.filter(name__in=roles).exists()


def role_required(*roles):
    """Use on views that change records. Login is checked before a role is checked."""
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if not has_role(request.user, roles):
                return render(request, "core/access_denied.html", {"required_roles": roles}, status=403)
            return view_func(request, *args, **kwargs)
        return wrapped
    return decorator


def log_action(request, action, module, record=None, remarks=""):
    """Save a small audit entry after a meaningful change."""
    AuditLog.objects.create(
        user=request.user if request.user.is_authenticated else None,
        action=action, module=module,
        record_id=str(record.pk) if record is not None and getattr(record, "pk", None) else "",
        remarks=remarks,
    )


def send_notice(user, message, target_url=""):
    """Notifications are stored in the database and displayed in the header."""
    if user and getattr(user, "is_active", False):
        Notification.objects.create(recipient=user, message=message, target_url=target_url)


def linked_student(user):
    """Return the student profile connected to a login, or None if it is not connected."""
    try:
        return user.student_profile
    except Student.DoesNotExist:
        return None


def linked_adviser(user):
    """Return the faculty profile connected to a login, if there is one."""
    try:
        return user.adviser_profile
    except Adviser.DoesNotExist:
        return None


def visible_students(user):
    """Queryset of student records this account may normally inspect."""
    students = Student.objects.all()
    if has_role(user, MANAGEMENT_ROLES):
        return students
    profile = linked_student(user)
    if profile and has_role(user, ("Student",)):
        return students.filter(pk=profile.pk)
    adviser = linked_adviser(user)
    if adviser and has_role(user, ("Adviser",)):
        return students.filter(adviser=adviser)
    if adviser and has_role(user, ("Panel Member",)):
        return students.filter(topics__proposals__defenses__panel_assignments__adviser=adviser).distinct()
    return students.none()


def can_view_student(user, student):
    """Whether a signed-in user can read a student's non-public workflow record."""
    return visible_students(user).filter(pk=student.pk).exists()


def can_submit_revision(user, student):
    """Only the linked student or coordinator/admin can submit a student's revisions."""
    if has_role(user, MANAGEMENT_ROLES):
        return True
    profile = linked_student(user)
    return bool(profile and profile.pk == student.pk and has_role(user, ("Student",)))


def can_manage_student(user, student):
    """Coordinator/admin can manage all records; students/advisers are scoped to their own links."""
    if has_role(user, MANAGEMENT_ROLES):
        return True
    profile = linked_student(user)
    if profile and profile.pk == student.pk and has_role(user, ("Student",)):
        return True
    adviser = linked_adviser(user)
    if adviser and student.adviser_id == adviser.pk and has_role(user, ("Adviser",)):
        return True
    return False


def can_review_student(user, student):
    """Advisers may review topics/proposals only for students assigned to them."""
    if has_role(user, MANAGEMENT_ROLES):
        return True
    adviser = linked_adviser(user)
    return bool(adviser and student.adviser_id == adviser.pk and has_role(user, ("Adviser",)))


def can_evaluate_defense(user, defense):
    """A panel member must be assigned to this exact defense before scoring it."""
    if has_role(user, MANAGEMENT_ROLES):
        return True
    return has_role(user, EVALUATION_ROLES) and defense.panel_assignments.filter(adviser__user=user).exists()


def final_validation_errors(thesis):
    """Return reasons why this final-thesis record is not ready for approval/archive."""
    errors = []
    if not thesis.final_pdf:
        errors.append("Final manuscript PDF is missing.")
    if not thesis.approval_form:
        errors.append("Required approval form is missing.")
    completed_final_defense = Defense.objects.filter(proposal=thesis.proposal, defense_type=Defense.Type.FINAL,
                                           status=Defense.Status.COMPLETED)
    if not completed_final_defense.exists():
        errors.append("A completed final defense is required.")
    elif not completed_final_defense.filter(final_decision__in=[Defense.Decision.PASS, Defense.Decision.MINOR, Defense.Decision.MAJOR], final_score__isnull=False).exists():
        errors.append("A consolidated passing final-defense decision and score are required.")
    if RevisionRequest.objects.filter(student=thesis.student).exclude(status=RevisionRequest.Status.APPROVED).exists():
        errors.append("All revision requests for this student must be approved.")
    return errors


def may_read_thesis(user, thesis):
    """Private drafts are visible to their owner/staff; archived items follow access level."""
    if has_role(user, REPOSITORY_ROLES) or can_view_student(user, thesis.student):
        return True
    if thesis.status != FinalThesis.Status.ARCHIVED:
        return False
    if thesis.access_level in [FinalThesis.Access.PUBLIC, FinalThesis.Access.INSTITUTION]:
        return True
    if thesis.access_level == FinalThesis.Access.FACULTY and has_role(user, FACULTY_ROLES + ("Panel Member",)):
        return True
    return False


def may_download_thesis(user, thesis):
    """Downloads require an archived thesis and a role permitted by its access level."""
    if thesis.status != FinalThesis.Status.ARCHIVED or not thesis.final_pdf:
        return False
    if has_role(user, REPOSITORY_ROLES):
        return True
    if thesis.access_level in [FinalThesis.Access.PUBLIC, FinalThesis.Access.INSTITUTION]:
        return True
    return thesis.access_level == FinalThesis.Access.FACULTY and has_role(user, FACULTY_ROLES + ("Panel Member",))


def form_page(request, form, title, cancel_url, **extra):
    return render(request, "core/form_page.html", {
        "form": form, "page_title": title, "cancel_url": cancel_url, **extra,
    })


def list_page(request, title, description, headers, rows, create_url=None, create_label="Add record", empty_message="No records found.", extra_context=None):
    context = {
        "page_title": title, "description": description, "headers": headers,
        "rows": rows, "create_url": create_url, "create_label": create_label,
        "empty_message": empty_message,
    }
    if extra_context:
        context.update(extra_context)
    return render(request, "core/list_page.html", context)


@login_required
def home(request):
    """The dashboard aggregates counts across the connected workflow."""
    context = {
        "total_students": Student.objects.filter(is_active=True).count(),
        "total_advisers": Adviser.objects.filter(is_active=True).count(),
        "topics_pending": ThesisTopic.objects.filter(status__in=["SUBMITTED", "UNDER_REVIEW", "FOR_REVISION", "RECOMMENDED"]).count(),
        "proposals_pending": ThesisProposal.objects.exclude(status__in=["APPROVED", "REJECTED"]).count(),
        "upcoming_defenses": Defense.objects.filter(status=Defense.Status.SCHEDULED, defense_date__gte=timezone.localdate()).count(),
        "revisions_pending": RevisionRequest.objects.exclude(status=RevisionRequest.Status.APPROVED).count(),
        "completed_theses": FinalThesis.objects.filter(status=FinalThesis.Status.APPROVED).count(),
        "archived_theses": FinalThesis.objects.filter(status=FinalThesis.Status.ARCHIVED).count(),
        "recent_notifications": Notification.objects.filter(recipient=request.user, is_read=False)[:5],
    }
    return render(request, "core/home.html", context)


# -----------------------------------------------------------------------------
# Students — Labs 4 to 6, plus the student record used by the rest of the system.
# -----------------------------------------------------------------------------
@login_required
def student_list(request):
    students = visible_students(request.user).select_related("adviser")
    query = request.GET.get("q", "").strip()
    program = request.GET.get("program", "").strip()
    year = request.GET.get("year_level", "").strip()
    status = request.GET.get("status", "active")
    if status == "active":
        students = students.filter(is_active=True)
    elif status == "inactive":
        students = students.filter(is_active=False)
    if query:
        students = students.filter(Q(student_number__icontains=query) | Q(first_name__icontains=query) |
                                   Q(middle_name__icontains=query) | Q(last_name__icontains=query))
    if program:
        students = students.filter(program__icontains=program)
    if year.isdigit():
        students = students.filter(year_level=int(year))
    programs = Student.objects.order_by("program").values_list("program", flat=True).distinct()
    rows = [{"primary": s.full_name, "cells": [s.student_number, s.program, f"Year {s.year_level}", s.adviser or "Not assigned", "Active" if s.is_active else "Inactive", s.thesis_status],
             "url": redirect_url("student_detail", s.pk), "actions": ([{"label": "Edit", "url": redirect_url("student_edit", s.pk)}] if has_role(request.user, MANAGEMENT_ROLES) else [])} for s in students]
    response = list_page(request, "Students", "Search student records, inspect thesis status, and manage adviser assignments.",
                         ["Student No.", "Program", "Year", "Adviser", "Status", "Thesis status"], rows,
                         "student_create" if has_role(request.user, MANAGEMENT_ROLES) else None, "Register student", extra_context={"query": query, "program": program, "year_level": year, "status": status, "programs": programs})
    return response


def redirect_url(name, *args):
    # reverse() is a small helper so table-building code can stay readable.
    from django.urls import reverse
    return reverse(name, args=args)


@role_required(*MANAGEMENT_ROLES)
def student_create(request):
    form = StudentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        student = form.save()
        log_action(request, "Registered student", "Students", student)
        messages.success(request, "Student registered successfully.")
        return redirect("student_detail", pk=student.pk)
    return form_page(request, form, "Register Student", redirect_url("student_list"))


@login_required
def student_detail(request, pk):
    student = get_object_or_404(Student.objects.select_related("adviser"), pk=pk)
    if not can_view_student(request.user, student):
        return render(request, "core/access_denied.html", status=403)
    return render(request, "core/student_detail.html", {"student": student, "can_edit": has_role(request.user, MANAGEMENT_ROLES)})


@role_required(*MANAGEMENT_ROLES)
def student_edit(request, pk):
    student = get_object_or_404(Student, pk=pk)
    form = StudentForm(request.POST or None, instance=student)
    if request.method == "POST" and form.is_valid():
        student = form.save()
        log_action(request, "Updated student", "Students", student)
        messages.success(request, "Student information saved.")
        return redirect("student_detail", pk=student.pk)
    return form_page(request, form, "Edit Student", redirect_url("student_detail", student.pk))


@role_required(*MANAGEMENT_ROLES)
@require_POST
def student_deactivate(request, pk):
    student = get_object_or_404(Student, pk=pk)
    student.is_active = False  # Soft-delete; thesis history remains attached to this student.
    student.save(update_fields=["is_active"])
    log_action(request, "Deactivated student", "Students", student)
    messages.success(request, "Student deactivated; the record was retained.")
    return redirect("student_list")


# -----------------------------------------------------------------------------
# Advisers — registration, workload monitoring, and assignment via StudentForm.
# -----------------------------------------------------------------------------
@login_required
def adviser_list(request):
    advisers = Adviser.objects.annotate(active_students=Count("students", filter=Q(students__is_active=True)))
    if not has_role(request.user, MANAGEMENT_ROLES):
        profile = linked_adviser(request.user)
        student = linked_student(request.user)
        if profile and has_role(request.user, ("Adviser", "Panel Member")):
            advisers = advisers.filter(pk=profile.pk)
        elif student and student.adviser_id:
            advisers = advisers.filter(pk=student.adviser_id)
        else:
            advisers = advisers.none()
    rows = [{"primary": str(a), "cells": [a.faculty_id, a.department or "—", a.specialization, a.active_students,
             a.max_advisees, max(0, a.max_advisees - a.active_students), "Active" if a.is_active else "Inactive"],
             "url": redirect_url("adviser_detail", a.pk), "actions": ([{"label": "Edit", "url": redirect_url("adviser_edit", a.pk)}] if has_role(request.user, MANAGEMENT_ROLES) else [])} for a in advisers]
    return list_page(request, "Adviser Management", "Workload is calculated from active students assigned to each adviser.",
                     ["Faculty ID", "Department", "Specialization", "Advisees", "Maximum", "Available", "Status"], rows,
                     "adviser_create" if has_role(request.user, MANAGEMENT_ROLES) else None, "Register adviser")


@role_required(*MANAGEMENT_ROLES)
def adviser_create(request):
    form = AdviserForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        adviser = form.save()
        log_action(request, "Registered adviser", "Advisers", adviser)
        messages.success(request, "Adviser registered.")
        return redirect("adviser_list")
    return form_page(request, form, "Register Adviser", redirect_url("adviser_list"))


@login_required
def adviser_detail(request, pk):
    adviser = get_object_or_404(Adviser, pk=pk)
    if not has_role(request.user, MANAGEMENT_ROLES):
        profile = linked_adviser(request.user)
        student = linked_student(request.user)
        if not ((profile and profile.pk == adviser.pk) or (student and student.adviser_id == adviser.pk)):
            return render(request, "core/access_denied.html", status=403)
    return render(request, "core/adviser_detail.html", {"adviser": adviser, "students": visible_students(request.user).filter(adviser=adviser), "can_edit": has_role(request.user, MANAGEMENT_ROLES)})


@role_required(*MANAGEMENT_ROLES)
def adviser_edit(request, pk):
    adviser = get_object_or_404(Adviser, pk=pk)
    form = AdviserForm(request.POST or None, instance=adviser)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_action(request, "Updated adviser", "Advisers", adviser)
        messages.success(request, "Adviser information saved.")
        return redirect("adviser_list")
    return form_page(request, form, "Edit Adviser", redirect_url("adviser_list"))


# -----------------------------------------------------------------------------
# Thesis topics — submission, review history, recommendation, and approval.
# -----------------------------------------------------------------------------
@login_required
def topic_list(request):
    topics = ThesisTopic.objects.select_related("student").filter(student__in=visible_students(request.user))
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    area = request.GET.get("area", "").strip()
    program = request.GET.get("program", "").strip()
    year = request.GET.get("year", "").strip()
    if q:
        topics = topics.filter(Q(title__icontains=q) | Q(student__first_name__icontains=q) |
                               Q(student__last_name__icontains=q) | Q(student__student_number__icontains=q))
    if status:
        topics = topics.filter(status=status)
    if area:
        topics = topics.filter(research_area__icontains=area)
    if program:
        topics = topics.filter(student__program__icontains=program)
    if year.isdigit():
        topics = topics.filter(student__year_level=int(year))
    programs = Student.objects.order_by("program").values_list("program", flat=True).distinct()
    rows = [{"primary": t.title, "cells": [t.student.full_name, t.research_area, t.get_status_display(), t.submitted_at.strftime("%b %d, %Y")],
             "url": redirect_url("topic_detail", t.pk)} for t in topics]
    return list_page(request, "Thesis Topics", "Submit topics, inspect similarity candidates, and follow the review status.",
                     ["Student", "Research area", "Status", "Submitted"], rows,
                     "topic_create" if has_role(request.user, ("Administrator", "Thesis Coordinator", "Adviser", "Student")) else None, "Propose topic",
                     extra_context={"query": q, "area": area, "program": program, "year": year, "status": status, "programs": programs, "topic_statuses": ThesisTopic.Status.choices})


@role_required("Administrator", "Thesis Coordinator", "Adviser", "Student")
def topic_create(request):
    form = ThesisTopicForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        topic = form.save()
        log_action(request, "Submitted thesis topic", "Topics", topic)
        # Notify the student's linked adviser, if the adviser account is connected.
        adviser_user = topic.student.adviser.user if topic.student.adviser else None
        send_notice(adviser_user, f"A new thesis topic was submitted: {topic.title}", redirect_url("topic_detail", topic.pk))
        messages.success(request, "Topic submitted for review.")
        return redirect("topic_detail", pk=topic.pk)
    return form_page(request, form, "Propose Thesis Topic", redirect_url("topic_list"))


@role_required("Administrator", "Thesis Coordinator", "Adviser", "Student")
def topic_edit(request, pk):
    topic = get_object_or_404(ThesisTopic, pk=pk)
    if not can_manage_student(request.user, topic.student):
        return render(request, "core/access_denied.html", status=403)
    # Students can update a topic that has not been finally approved, especially after a revision request.
    if topic.status not in [ThesisTopic.Status.SUBMITTED, ThesisTopic.Status.FOR_REVISION]:
        messages.error(request, "Only submitted or returned topics can be edited.")
        return redirect("topic_detail", pk=pk)
    form = ThesisTopicForm(request.POST or None, instance=topic, user=request.user)
    if request.method == "POST" and form.is_valid():
        topic = form.save(commit=False)
        if topic.status == ThesisTopic.Status.FOR_REVISION:
            topic.status = ThesisTopic.Status.SUBMITTED
        topic.save()
        log_action(request, "Updated thesis topic", "Topics", topic)
        messages.success(request, "Topic changes saved and the topic returned to Submitted status.")
        return redirect("topic_detail", pk=pk)
    return form_page(request, form, "Edit Thesis Topic", redirect_url("topic_detail", pk))


@login_required
def topic_detail(request, pk):
    topic = get_object_or_404(ThesisTopic.objects.select_related("student"), pk=pk)
    if not can_view_student(request.user, topic.student):
        return render(request, "core/access_denied.html", status=403)
    similar = ThesisTopic.objects.filter(student__in=visible_students(request.user)).exclude(pk=topic.pk).filter(
        Q(title__icontains=topic.title[:30]) | Q(research_area__iexact=topic.research_area)
    )[:5]  # Basic keyword/area check; this is not a full semantic similarity algorithm.
    return render(request, "core/topic_detail.html", {"topic": topic, "reviews": topic.reviews.select_related("reviewer"),
                                                        "similar_topics": similar, "reviewer_can_act": can_review_student(request.user, topic.student),
                                                        "can_approve": has_role(request.user, MANAGEMENT_ROLES),
                                                        "can_edit": can_manage_student(request.user, topic.student) and topic.status in [ThesisTopic.Status.SUBMITTED, ThesisTopic.Status.FOR_REVISION]})


@role_required(*FACULTY_ROLES)
@require_POST
def topic_review(request, pk):
    topic = get_object_or_404(ThesisTopic, pk=pk)
    if not can_review_student(request.user, topic.student):
        return render(request, "core/access_denied.html", status=403)
    recommendation = request.POST.get("recommendation", "")
    comments = request.POST.get("comments", "").strip()
    valid = dict(TopicReview.Recommendation.choices)
    if topic.status in [ThesisTopic.Status.APPROVED, ThesisTopic.Status.REJECTED]:
        messages.error(request, "A topic that is already approved or rejected cannot be reviewed again from this page.")
        return redirect("topic_detail", pk=pk)
    if recommendation not in valid or not comments:
        messages.error(request, "Choose a recommendation and enter review comments.")
        return redirect("topic_detail", pk=pk)
    TopicReview.objects.create(topic=topic, reviewer=request.user, comments=comments, recommendation=recommendation)
    topic.status = {"APPROVE": ThesisTopic.Status.RECOMMENDED, "REVISE": ThesisTopic.Status.FOR_REVISION,
                    "REJECT": ThesisTopic.Status.REJECTED}[recommendation]
    topic.save(update_fields=["status", "updated_at"])
    log_action(request, "Reviewed thesis topic", "Topics", topic, recommendation)
    send_notice(topic.student.user, f"Your topic '{topic.title}' review result: {topic.get_status_display()}.", redirect_url("topic_detail", topic.pk))
    messages.success(request, f"Review recorded. Current status: {topic.get_status_display()}.")
    return redirect("topic_detail", pk=pk)


@role_required(*MANAGEMENT_ROLES)
@require_POST
def topic_approve(request, pk):
    topic = get_object_or_404(ThesisTopic, pk=pk)
    if topic.status != ThesisTopic.Status.RECOMMENDED:
        messages.error(request, "A topic must be recommended before final approval.")
    else:
        topic.status = ThesisTopic.Status.APPROVED
        topic.save(update_fields=["status", "updated_at"])
        log_action(request, "Approved thesis topic", "Topics", topic)
        messages.success(request, "Topic approved. The student can now start a proposal.")
    return redirect("topic_detail", pk=pk)


# -----------------------------------------------------------------------------
# Proposals — connected only to approved topics, document history, and review.
# -----------------------------------------------------------------------------
@login_required
def proposal_list(request):
    proposals = ThesisProposal.objects.select_related("topic", "topic__student").filter(topic__student__in=visible_students(request.user))
    q = request.GET.get("q", "").strip()
    if q:
        proposals = proposals.filter(Q(topic__title__icontains=q) | Q(topic__student__first_name__icontains=q) | Q(topic__student__last_name__icontains=q) | Q(topic__student__student_number__icontains=q))
    rows = [{"primary": f"{p.topic.title} (v{p.version})", "cells": [p.topic.student.full_name, p.get_status_display(),
             p.submitted_at.strftime("%b %d, %Y") if p.submitted_at else "Not submitted", p.documents.count()],
             "url": redirect_url("proposal_detail", p.pk)} for p in proposals]
    return list_page(request, "Proposals", "Proposal text, documents, comments, and status remain connected to an approved topic.",
                     ["Student", "Status", "Submitted", "Documents"], rows,
                     "proposal_create" if has_role(request.user, ("Administrator", "Thesis Coordinator", "Adviser", "Student")) else None, "Create proposal")


@role_required("Administrator", "Thesis Coordinator", "Adviser", "Student")
def proposal_create(request):
    form = ThesisProposalForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        proposal = form.save()
        log_action(request, "Created proposal", "Proposals", proposal)
        messages.success(request, "Proposal draft created.")
        return redirect("proposal_detail", pk=proposal.pk)
    return form_page(request, form, "Create Thesis Proposal", redirect_url("proposal_list"))


@role_required("Administrator", "Thesis Coordinator", "Adviser", "Student")
def proposal_edit(request, pk):
    proposal = get_object_or_404(ThesisProposal, pk=pk)
    if not can_manage_student(request.user, proposal.topic.student):
        return render(request, "core/access_denied.html", status=403)
    if proposal.status not in [ThesisProposal.Status.DRAFT, ThesisProposal.Status.FOR_REVISION]:
        messages.error(request, "Only draft or returned proposals can be edited.")
        return redirect("proposal_detail", pk=pk)
    form = ThesisProposalForm(request.POST or None, instance=proposal, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_action(request, "Edited proposal", "Proposals", proposal)
        messages.success(request, "Proposal changes saved.")
        return redirect("proposal_detail", pk=pk)
    return form_page(request, form, "Edit Proposal", redirect_url("proposal_detail", pk))


@login_required
def proposal_detail(request, pk):
    proposal = get_object_or_404(ThesisProposal.objects.select_related("topic", "topic__student"), pk=pk)
    if not can_view_student(request.user, proposal.topic.student):
        return render(request, "core/access_denied.html", status=403)
    document_form = ProposalDocumentForm()
    return render(request, "core/proposal_detail.html", {"proposal": proposal, "document_form": document_form,
                                                            "can_review": can_review_student(request.user, proposal.topic.student),
                                                            "can_edit": can_manage_student(request.user, proposal.topic.student) and proposal.status in [ThesisProposal.Status.DRAFT, ThesisProposal.Status.FOR_REVISION]})


@role_required("Administrator", "Thesis Coordinator", "Adviser", "Student")
@require_POST
def proposal_submit(request, pk):
    proposal = get_object_or_404(ThesisProposal, pk=pk)
    if not can_manage_student(request.user, proposal.topic.student):
        return render(request, "core/access_denied.html", status=403)
    if proposal.status not in [ThesisProposal.Status.DRAFT, ThesisProposal.Status.FOR_REVISION, ThesisProposal.Status.RESUBMITTED]:
        messages.error(request, "This proposal cannot be submitted from its current status.")
    elif not all([proposal.introduction, proposal.problem_statement, proposal.objectives, proposal.methodology]):
        messages.error(request, "Complete Introduction, Problem Statement, Objectives, and Methodology before submitting.")
    else:
        proposal.status = ThesisProposal.Status.SUBMITTED if proposal.version == 1 else ThesisProposal.Status.RESUBMITTED
        proposal.submitted_at = timezone.now()
        proposal.save(update_fields=["status", "submitted_at"])
        log_action(request, "Submitted proposal", "Proposals", proposal)
        messages.success(request, "Proposal submitted for review.")
    return redirect("proposal_detail", pk=pk)


@role_required(*FACULTY_ROLES)
@require_POST
def proposal_review(request, pk):
    proposal = get_object_or_404(ThesisProposal, pk=pk)
    if not can_review_student(request.user, proposal.topic.student):
        return render(request, "core/access_denied.html", status=403)
    new_status = request.POST.get("status", "")
    comment = request.POST.get("adviser_comments", "").strip()
    allowed = {"UNDER_REVIEW", "FOR_REVISION", "APPROVED", "REJECTED"}
    if proposal.status in [ThesisProposal.Status.APPROVED, ThesisProposal.Status.REJECTED]:
        messages.error(request, "An already approved or rejected proposal cannot be reviewed again here.")
    elif proposal.status == ThesisProposal.Status.DRAFT:
        messages.error(request, "Submit the proposal before reviewing it.")
    elif new_status not in allowed:
        messages.error(request, "Invalid proposal review status.")
    elif new_status == "APPROVED" and not proposal.documents.exists():
        messages.error(request, "Upload at least one proposal document before approving.")
    else:
        proposal.status = new_status
        proposal.adviser_comments = comment
        proposal.save(update_fields=["status", "adviser_comments"])
        ProposalReview.objects.create(proposal=proposal, reviewer=request.user, status=new_status, comments=comment)
        log_action(request, "Reviewed proposal", "Proposals", proposal, new_status)
        send_notice(proposal.topic.student.user, f"Proposal review update: {proposal.get_status_display()}.", redirect_url("proposal_detail", proposal.pk))
        messages.success(request, "Proposal review saved.")
    return redirect("proposal_detail", pk=pk)


@role_required("Administrator", "Thesis Coordinator", "Adviser", "Student")
@require_POST
def proposal_document_upload(request, pk):
    proposal = get_object_or_404(ThesisProposal, pk=pk)
    if not can_manage_student(request.user, proposal.topic.student):
        return render(request, "core/access_denied.html", status=403)
    form = ProposalDocumentForm(request.POST, request.FILES)
    if form.is_valid():
        document = form.save(commit=False)
        document.proposal = proposal
        document.uploaded_by = request.user
        document.save()
        log_action(request, "Uploaded proposal document", "Proposals", proposal, document.version_label)
        messages.success(request, "Document uploaded and added to the proposal's file history.")
    else:
        messages.error(request, "Could not upload the file. Check that you selected a document.")
    return redirect("proposal_detail", pk=pk)


# -----------------------------------------------------------------------------
# Defense scheduling and panel assignment. Both depend on an approved proposal.
# -----------------------------------------------------------------------------
@login_required
def defense_list(request):
    defenses = Defense.objects.select_related("proposal", "proposal__topic", "proposal__topic__student")
    if has_role(request.user, MANAGEMENT_ROLES):
        pass
    else:
        # Students/advisers see defenses for visible students; panel members see assigned defenses.
        own = Defense.objects.filter(proposal__topic__student__in=visible_students(request.user))
        panel = Defense.objects.filter(panel_assignments__adviser__user=request.user)
        defenses = (own | panel).distinct()
    q = request.GET.get("q", "").strip()
    if q:
        defenses = defenses.filter(Q(proposal__topic__title__icontains=q) | Q(proposal__topic__student__first_name__icontains=q) | Q(proposal__topic__student__last_name__icontains=q) | Q(venue__icontains=q))
    rows = [{"primary": str(d), "cells": [d.proposal.topic.student.full_name, d.get_defense_type_display(),
             d.defense_date.strftime("%b %d, %Y"), d.defense_time.strftime("%I:%M %p"), d.venue, d.get_status_display()],
             "url": redirect_url("defense_detail", d.pk)} for d in defenses]
    return list_page(request, "Defense Calendar", "Scheduled defenses, panel assignments, and evaluation records.",
                     ["Student", "Type", "Date", "Time", "Venue", "Status"], rows,
                     "defense_create" if has_role(request.user, MANAGEMENT_ROLES) else None, "Schedule defense")


@role_required(*MANAGEMENT_ROLES)
def defense_create(request):
    form = DefenseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        defense = form.save(commit=False)
        defense.created_by = request.user
        # Avoid creating a scheduled conflict for the exact same venue/date/time.
        conflict_in_venue = Defense.objects.filter(status=Defense.Status.SCHEDULED, defense_date=defense.defense_date,
                    defense_time=defense.defense_time, venue__iexact=defense.venue).exists()
        conflict_for_student = Defense.objects.filter(status=Defense.Status.SCHEDULED, defense_date=defense.defense_date,
                    defense_time=defense.defense_time, proposal__topic__student=defense.proposal.topic.student).exists()
        if conflict_in_venue or conflict_for_student:
            form.add_error(None, "A defense already uses this venue or the same student already has a defense at that date and time.")
        else:
            defense.save()
            log_action(request, "Scheduled defense", "Defenses", defense)
            student = defense.proposal.topic.student
            send_notice(student.user, f"Your {defense.get_defense_type_display()} is scheduled for {defense.defense_date} at {defense.defense_time}.", redirect_url("defense_detail", defense.pk))
            if student.adviser:
                send_notice(student.adviser.user, f"A defense for {student.full_name} is scheduled for {defense.defense_date}.", redirect_url("defense_detail", defense.pk))
            messages.success(request, "Defense scheduled.")
            return redirect("defense_detail", pk=defense.pk)
    return form_page(request, form, "Schedule Defense", redirect_url("defense_list"))


@login_required
def defense_detail(request, pk):
    defense = get_object_or_404(Defense.objects.select_related("proposal", "proposal__topic", "proposal__topic__student"), pk=pk)
    if not can_view_student(request.user, defense.proposal.topic.student) and not defense.panel_assignments.filter(adviser__user=request.user).exists() and not has_role(request.user, MANAGEMENT_ROLES):
        return render(request, "core/access_denied.html", status=403)
    return render(request, "core/defense_detail.html", {"defense": defense, "panel_form": DefensePanelForm(),
                                                          "panel_roles_allowed": has_role(request.user, MANAGEMENT_ROLES),
                                                          "evaluation_allowed": can_evaluate_defense(request.user, defense),
                                                          "revision_allowed": has_role(request.user, FACULTY_ROLES),
                                                          "can_finalize_decision": has_role(request.user, MANAGEMENT_ROLES),
                                                          "defense_decisions": Defense.Decision.choices})


@role_required(*MANAGEMENT_ROLES)
@require_POST
def defense_panel_add(request, pk):
    defense = get_object_or_404(Defense, pk=pk)
    form = DefensePanelForm(request.POST)
    if form.is_valid():
        assignment = form.save(commit=False)
        assignment.defense = defense
        adviser = assignment.adviser
        # Panel members may not be assigned to two defenses with the same date and time.
        if DefensePanel.objects.filter(adviser=adviser, defense__defense_date=defense.defense_date,
                                       defense__defense_time=defense.defense_time,
                                       defense__status=Defense.Status.SCHEDULED).exclude(defense=defense).exists():
            messages.error(request, "This faculty member is already assigned to another defense at that date and time.")
        elif assignment.role in [DefensePanel.Role.CHAIR, DefensePanel.Role.ADVISER] and defense.panel_assignments.filter(role=assignment.role).exists():
            messages.error(request, f"Only one {assignment.get_role_display().lower()} can be assigned to a defense.")
        elif assignment.role == DefensePanel.Role.ADVISER and defense.proposal.topic.student.adviser_id and defense.proposal.topic.student.adviser_id != adviser.pk:
            messages.error(request, "Assign the student's own adviser to the Adviser panel role.")
        else:
            try:
                assignment.save()
                log_action(request, "Assigned defense panel member", "Defenses", defense, f"{adviser} — {assignment.role}")
                if adviser.user:
                    send_notice(adviser.user, f"You were assigned to a defense on {defense.defense_date}.", redirect_url("defense_detail", defense.pk))
                messages.success(request, "Panel member assigned.")
            except IntegrityError:
                messages.error(request, "This faculty member is already assigned to this defense.")
    else:
        messages.error(request, "Choose an active faculty member and a panel role.")
    return redirect("defense_detail", pk=pk)


@role_required(*MANAGEMENT_ROLES)
@require_POST
def defense_mark_completed(request, pk):
    defense = get_object_or_404(Defense, pk=pk)
    roles = list(defense.panel_assignments.values_list("role", flat=True))
    missing = []
    if DefensePanel.Role.CHAIR not in roles:
        missing.append("chairperson")
    if DefensePanel.Role.ADVISER not in roles:
        missing.append("adviser")
    if roles.count(DefensePanel.Role.MEMBER) < 2:
        missing.append("at least two panel members")
    if missing:
        messages.error(request, "Assign the required defense panel first: " + ", ".join(missing) + ".")
        return redirect("defense_detail", pk=pk)
    defense.status = Defense.Status.COMPLETED
    defense.save(update_fields=["status"])
    log_action(request, "Marked defense completed", "Defenses", defense)
    messages.success(request, "Defense marked as completed. Panel members can now enter their evaluations.")
    return redirect("defense_detail", pk=pk)


@role_required(*MANAGEMENT_ROLES)
@require_POST
def defense_finalize_decision(request, pk):
    """Consolidate panel evaluations into one official score and decision for this defense."""
    defense = get_object_or_404(Defense, pk=pk)
    if defense.status != Defense.Status.COMPLETED:
        messages.error(request, "The defense must be marked completed before finalizing its decision.")
        return redirect("defense_detail", pk=pk)
    criteria = list(EvaluationCriterion.objects.filter(is_active=True))
    if not criteria or sum((c.weight for c in criteria), Decimal("0")) != Decimal("100.00"):
        messages.error(request, "Active evaluation criteria must total exactly 100%.")
        return redirect("defense_detail", pk=pk)
    evaluations = list(defense.evaluations.prefetch_related("scores__criterion"))
    if not evaluations:
        messages.error(request, "At least one panel evaluation is required.")
        return redirect("defense_detail", pk=pk)
    assigned_user_ids = set(defense.panel_assignments.exclude(adviser__user__isnull=True).values_list("adviser__user_id", flat=True))
    submitted_user_ids = {evaluation.evaluator_id for evaluation in evaluations if evaluation.decision}
    if assigned_user_ids and not assigned_user_ids.issubset(submitted_user_ids):
        messages.error(request, "Every assigned panel member with a login must submit an evaluation first.")
        return redirect("defense_detail", pk=pk)
    scores = []
    for evaluation in evaluations:
        by_criterion = {score.criterion_id: score for score in evaluation.scores.all()}
        if any(criterion.pk not in by_criterion for criterion in criteria) or not evaluation.decision:
            messages.error(request, f"Evaluation by {evaluation.evaluator.username} is incomplete.")
            return redirect("defense_detail", pk=pk)
        scores.append(evaluation.weighted_score)
    decision = request.POST.get("final_decision", "")
    if decision not in dict(Defense.Decision.choices):
        messages.error(request, "Select the official defense decision.")
        return redirect("defense_detail", pk=pk)
    # The system averages panel weighted scores; the coordinator records the official decision.
    average = (sum(scores, Decimal("0")) / Decimal(len(scores))).quantize(Decimal("0.01"))
    defense.final_score = average
    defense.final_decision = decision
    defense.save(update_fields=["final_score", "final_decision"])
    log_action(request, "Finalized defense decision", "Evaluation", defense, f"Score: {average}; Decision: {decision}")
    student = defense.proposal.topic.student
    send_notice(student.user, f"Your defense result is {defense.get_final_decision_display()} with a score of {average}%.", redirect_url("defense_detail", defense.pk))
    messages.success(request, f"Official defense result saved. Average weighted score: {average}%.")
    return redirect("defense_detail", pk=pk)


# -----------------------------------------------------------------------------
# Evaluation — criteria weights, panel scoring, automatic weighted total/decision.
# -----------------------------------------------------------------------------
@login_required
def criteria_list(request):
    criteria = EvaluationCriterion.objects.all()
    rows = [{"primary": c.name, "cells": [c.description or "—", f"{c.weight}%", "Active" if c.is_active else "Inactive"],
             "url": redirect_url("criterion_edit", c.pk), "actions": ([{"label": "Edit", "url": redirect_url("criterion_edit", c.pk)}] if has_role(request.user, MANAGEMENT_ROLES) else [])} for c in criteria]
    return list_page(request, "Evaluation Criteria", "The active criterion weights should total 100 percent.",
                     ["Description", "Weight", "Status"], rows,
                     "criterion_create" if has_role(request.user, MANAGEMENT_ROLES) else None, "Add criterion")


@role_required(*MANAGEMENT_ROLES)
def criterion_create(request):
    form = EvaluationCriterionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        criterion = form.save()
        log_action(request, "Created evaluation criterion", "Evaluation", criterion)
        return redirect("criteria_list")
    return form_page(request, form, "Add Evaluation Criterion", redirect_url("criteria_list"))


@role_required(*MANAGEMENT_ROLES)
def criterion_edit(request, pk):
    criterion = get_object_or_404(EvaluationCriterion, pk=pk)
    form = EvaluationCriterionForm(request.POST or None, instance=criterion)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_action(request, "Updated evaluation criterion", "Evaluation", criterion)
        return redirect("criteria_list")
    return form_page(request, form, "Edit Evaluation Criterion", redirect_url("criteria_list"))


@login_required
def evaluation_page(request, pk):
    defense = get_object_or_404(Defense, pk=pk)
    if not can_evaluate_defense(request.user, defense):
        return render(request, "core/access_denied.html", {"required_roles": EVALUATION_ROLES}, status=403)
    if defense.status != Defense.Status.COMPLETED:
        messages.error(request, "Mark the defense as completed before recording panel evaluations.")
        return redirect("defense_detail", pk=pk)
    criteria = EvaluationCriterion.objects.filter(is_active=True)
    evaluation, _ = Evaluation.objects.get_or_create(defense=defense, evaluator=request.user)
    if request.method == "POST":
        weight_total = sum((c.weight for c in criteria), Decimal("0"))
        if not criteria.exists() or weight_total != Decimal("100.00"):
            messages.error(request, f"Active evaluation criteria must total exactly 100%. Current total: {weight_total}%.")
            return redirect("evaluation_page", pk=pk)
        try:
            with transaction.atomic():
                for criterion in criteria:
                    raw_score = request.POST.get(f"score_{criterion.pk}", "").strip()
                    score = Decimal(raw_score)
                    if score < 0 or score > 100:
                        raise ValueError(f"Score for {criterion.name} must be between 0 and 100.")
                    EvaluationScore.objects.update_or_create(evaluation=evaluation, criterion=criterion,
                                                              defaults={"score": score})
                decision = request.POST.get("decision", "")
                if decision not in dict(Evaluation.Decision.choices):
                    raise ValueError("Choose a valid defense decision.")
                evaluation.decision = decision
                evaluation.comments = request.POST.get("comments", "").strip()
                evaluation.save()
        except (InvalidOperation, ValueError) as error:
            messages.error(request, str(error) or "Enter a valid score for every criterion.")
            return redirect("evaluation_page", pk=pk)
        log_action(request, "Submitted evaluation", "Evaluation", evaluation, f"Weighted score: {evaluation.weighted_score}")
        messages.success(request, f"Evaluation saved. Weighted score: {evaluation.weighted_score}%.")
        return redirect("defense_detail", pk=defense.pk)
    saved_scores = {score.criterion_id: score.score for score in evaluation.scores.all()}
    return render(request, "core/evaluation_page.html", {"defense": defense, "criteria": criteria,
                                                           "saved_scores": saved_scores, "evaluation": evaluation, "decisions": Evaluation.Decision.choices})


# -----------------------------------------------------------------------------
# Revisions — requests, student upload/response, and faculty verification.
# -----------------------------------------------------------------------------
@login_required
def revision_list(request):
    revisions = RevisionRequest.objects.select_related("student", "defense").filter(student__in=visible_students(request.user))
    q = request.GET.get("q", "").strip()
    if q:
        revisions = revisions.filter(Q(section__icontains=q) | Q(required_change__icontains=q) | Q(student__first_name__icontains=q) | Q(student__last_name__icontains=q))
    rows = [{"primary": f"{r.section}: {r.required_change[:65]}", "cells": [r.student.full_name,
             r.get_priority_display(), r.deadline.strftime("%b %d, %Y") if r.deadline else "—", r.get_status_display()],
             "url": redirect_url("revision_detail", r.pk)} for r in revisions]
    return list_page(request, "Revision Tracking", "Track required changes through student submission and faculty verification.",
                     ["Student", "Priority", "Deadline", "Status"], rows,
                     "revision_create" if has_role(request.user, FACULTY_ROLES) else None, "Create revision request")


@role_required(*FACULTY_ROLES)
def revision_create(request):
    form = RevisionRequestForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        revision = form.save(commit=False)
        if not can_review_student(request.user, revision.student):
            form.add_error("student", "You can create revisions only for students assigned to you.")
            return form_page(request, form, "Create Revision Request", redirect_url("revision_list"))
        revision.requested_by = request.user
        revision.save()
        log_action(request, "Created revision request", "Revisions", revision)
        send_notice(revision.student.user, f"A thesis revision is required for {revision.section}.", redirect_url("revision_detail", revision.pk))
        messages.success(request, "Revision request created.")
        return redirect("revision_detail", pk=revision.pk)
    return form_page(request, form, "Create Revision Request", redirect_url("revision_list"))


@login_required
def revision_detail(request, pk):
    revision = get_object_or_404(RevisionRequest.objects.select_related("student", "defense"), pk=pk)
    if not can_view_student(request.user, revision.student) and not revision.requested_by_id == request.user.pk and not has_role(request.user, MANAGEMENT_ROLES):
        return render(request, "core/access_denied.html", status=403)
    form = StudentRevisionSubmissionForm(instance=revision)
    return render(request, "core/revision_detail.html", {"revision": revision, "form": form,
                                                           "can_verify": can_review_student(request.user, revision.student),
                                                           "can_submit": can_submit_revision(request.user, revision.student) and revision.status != RevisionRequest.Status.APPROVED})


@role_required("Administrator", "Thesis Coordinator", "Student")
@require_POST
def revision_submit(request, pk):
    revision = get_object_or_404(RevisionRequest, pk=pk)
    if not can_manage_student(request.user, revision.student):
        return render(request, "core/access_denied.html", status=403)
    form = StudentRevisionSubmissionForm(request.POST, request.FILES, instance=revision)
    if form.is_valid():
        revision = form.save(commit=False)
        if not revision.student_response.strip() and not revision.revised_file:
            messages.error(request, "Add a response or upload the revised file before submitting.")
        else:
            revision.status = RevisionRequest.Status.SUBMITTED
            revision.save()
            log_action(request, "Submitted revision", "Revisions", revision)
            messages.success(request, "Revision submitted for verification.")
    else:
        messages.error(request, "Check the revision response and file.")
    return redirect("revision_detail", pk=pk)


@role_required(*FACULTY_ROLES)
@require_POST
def revision_verify(request, pk):
    revision = get_object_or_404(RevisionRequest, pk=pk)
    if not can_review_student(request.user, revision.student):
        return render(request, "core/access_denied.html", status=403)
    decision = request.POST.get("decision")
    if decision not in [RevisionRequest.Status.APPROVED, RevisionRequest.Status.RETURNED]:
        messages.error(request, "Choose approve or return.")
    elif revision.status != RevisionRequest.Status.SUBMITTED:
        messages.error(request, "Only submitted revisions can be verified.")
    else:
        revision.status = decision
        revision.save(update_fields=["status", "updated_at"])
        log_action(request, "Verified revision", "Revisions", revision, decision)
        send_notice(revision.student.user, f"Revision status updated: {revision.get_status_display()}.", redirect_url("revision_detail", revision.pk))
        messages.success(request, f"Revision marked as {revision.get_status_display()}.")
    return redirect("revision_detail", pk=pk)


# -----------------------------------------------------------------------------
# Final thesis and searchable repository — only approved work can be archived.
# -----------------------------------------------------------------------------
@login_required
def repository_list(request):
    theses = FinalThesis.objects.select_related("student", "student__adviser", "proposal", "proposal__topic")
    q = request.GET.get("q", "").strip()
    program = request.GET.get("program", "").strip()
    area = request.GET.get("area", "").strip()
    adviser = request.GET.get("adviser", "").strip()
    year = request.GET.get("year", "").strip()
    if q:
        theses = theses.filter(Q(title__icontains=q) | Q(student__first_name__icontains=q) |
                               Q(student__last_name__icontains=q) | Q(student__student_number__icontains=q) |
                               Q(keywords__icontains=q) | Q(proposal__topic__title__icontains=q) |
                               Q(student__adviser__first_name__icontains=q) | Q(student__adviser__last_name__icontains=q))
    if program:
        theses = theses.filter(student__program__icontains=program)
    if area:
        theses = theses.filter(research_area__icontains=area) | theses.filter(proposal__topic__research_area__icontains=area)
    if adviser:
        theses = theses.filter(Q(student__adviser__first_name__icontains=adviser) | Q(student__adviser__last_name__icontains=adviser))
    if year.isdigit():
        theses = theses.filter(archived_at__year=int(year))
    access_allowed = has_role(request.user, REPOSITORY_ROLES)
    # Public items are visible to any signed-in user; restricted items need appropriate roles.
    if not access_allowed:
        allowed_access = [FinalThesis.Access.PUBLIC, FinalThesis.Access.INSTITUTION]
        if has_role(request.user, FACULTY_ROLES + ("Panel Member",)):
            allowed_access.append(FinalThesis.Access.FACULTY)
        theses = theses.filter(access_level__in=allowed_access, status=FinalThesis.Status.ARCHIVED)
    rows = [{"primary": t.title, "cells": [t.student.full_name, t.student.program, t.proposal.topic.research_area,
             t.student.adviser or "—", t.get_access_level_display(), t.get_status_display(), t.archived_at.strftime("%b %d, %Y") if t.archived_at else "—"],
             "url": redirect_url("final_thesis_detail", t.pk)} for t in theses]
    programs = Student.objects.order_by("program").values_list("program", flat=True).distinct()
    areas = ThesisTopic.objects.order_by("research_area").values_list("research_area", flat=True).distinct()
    years = FinalThesis.objects.exclude(archived_at__isnull=True).dates("archived_at", "year", order="DESC")
    response = list_page(request, "Thesis Repository", "Search archived theses by title, author, adviser, program, year, keywords, or research area.",
                         ["Author", "Program", "Research area", "Adviser", "Access", "Status", "Archived"], rows,
                         "final_thesis_create" if has_role(request.user, ("Administrator", "Thesis Coordinator", "Student", "Adviser", "Repository Manager")) else None,
                         "Create final thesis record", extra_context={"query": q, "program": program, "area": area, "adviser": adviser,
                         "year": year, "programs": programs, "areas": areas, "archive_years": [d.year for d in years]})
    return response


@role_required("Administrator", "Thesis Coordinator", "Student", "Adviser", "Repository Manager")
def final_thesis_create(request):
    form = FinalThesisForm(request.POST or None, request.FILES or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        thesis = form.save(commit=False)
        thesis.created_by = request.user
        thesis.save()
        log_action(request, "Created final thesis record", "Repository", thesis)
        messages.success(request, "Final thesis record created. Submit it for validation when ready.")
        return redirect("final_thesis_detail", pk=thesis.pk)
    return form_page(request, form, "Create Final Thesis Record", redirect_url("repository_list"))


@role_required("Administrator", "Thesis Coordinator", "Student", "Adviser", "Repository Manager")
def final_thesis_edit(request, pk):
    thesis = get_object_or_404(FinalThesis, pk=pk)
    if not (can_manage_student(request.user, thesis.student) or has_role(request.user, ("Repository Manager",))) or thesis.status != FinalThesis.Status.DRAFT:
        return render(request, "core/access_denied.html", status=403)
    form = FinalThesisForm(request.POST or None, request.FILES or None, instance=thesis, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_action(request, "Edited final thesis draft", "Repository", thesis)
        messages.success(request, "Final thesis record updated.")
        return redirect("final_thesis_detail", pk=pk)
    return form_page(request, form, "Edit Final Thesis", redirect_url("final_thesis_detail", pk))


@login_required
def final_thesis_detail(request, pk):
    thesis = get_object_or_404(FinalThesis.objects.select_related("student", "proposal", "proposal__topic"), pk=pk)
    if not may_read_thesis(request.user, thesis):
        return render(request, "core/access_denied.html", status=403)
    can_download = may_download_thesis(request.user, thesis)
    if thesis.status == FinalThesis.Status.ARCHIVED:
        # Record each archived-thesis page view for repository access reporting.
        RepositoryAccess.objects.create(thesis=thesis, user=request.user, action=RepositoryAccess.Action.VIEW)
    return render(request, "core/final_thesis_detail.html", {"thesis": thesis, "can_download": can_download,
        "can_manage": has_role(request.user, REPOSITORY_ROLES),
        "can_edit": (can_manage_student(request.user, thesis.student) or has_role(request.user, ("Repository Manager",))) and thesis.status == FinalThesis.Status.DRAFT,
        "can_record_adviser_approval": has_role(request.user, REPOSITORY_ROLES) or (has_role(request.user, FACULTY_ROLES) and can_review_student(request.user, thesis.student)),
        "can_record_panel_approval": has_role(request.user, REPOSITORY_ROLES) or (has_role(request.user, EVALUATION_ROLES) and Defense.objects.filter(proposal=thesis.proposal, defense_type=Defense.Type.FINAL, panel_assignments__adviser__user=request.user).exists()),
        "can_submit": has_role(request.user, ("Administrator", "Thesis Coordinator", "Student", "Adviser", "Repository Manager"))})


@login_required
def repository_download(request, pk):
    thesis = get_object_or_404(FinalThesis, pk=pk)
    if not may_download_thesis(request.user, thesis):
        return render(request, "core/access_denied.html", status=403)
    # FileField.open() works with Django storage backends better than opening a local path.
    file_handle = thesis.final_pdf.open("rb")
    RepositoryAccess.objects.create(thesis=thesis, user=request.user, action=RepositoryAccess.Action.DOWNLOAD)
    log_action(request, "Downloaded archived thesis", "Repository", thesis)
    safe_filename = Path(thesis.final_pdf.name).name
    return FileResponse(file_handle, as_attachment=True, filename=safe_filename)


@role_required("Administrator", "Thesis Coordinator", "Student", "Adviser", "Panel Member", "Repository Manager")
@require_POST
def final_thesis_action(request, pk):
    thesis = get_object_or_404(FinalThesis, pk=pk)
    action = request.POST.get("action")
    if action == "submit":
        if not (can_manage_student(request.user, thesis.student) or has_role(request.user, ("Repository Manager",))):
            return render(request, "core/access_denied.html", status=403)
        errors = final_validation_errors(thesis)
        if errors:
            messages.error(request, "Cannot submit final thesis yet: " + " ".join(errors))
        else:
            thesis.status = FinalThesis.Status.SUBMITTED
            thesis.submitted_at = timezone.now()
            thesis.save(update_fields=["status", "submitted_at"])
            log_action(request, "Submitted final thesis", "Repository", thesis)
            messages.success(request, "Final thesis submitted for validation.")
    elif action in ("adviser_approve", "panel_approve"):
        # A coordinator/repository manager can record an approval administratively.
        # An adviser must be linked to this student's adviser record; a panel member must
        # be assigned to the final defense associated with this thesis.
        if action == "adviser_approve":
            authorized = has_role(request.user, REPOSITORY_ROLES) or (
                has_role(request.user, FACULTY_ROLES) and can_review_student(request.user, thesis.student)
            )
        else:
            final_defenses = Defense.objects.filter(proposal=thesis.proposal, defense_type=Defense.Type.FINAL)
            authorized = has_role(request.user, REPOSITORY_ROLES) or (
                has_role(request.user, EVALUATION_ROLES) and final_defenses.filter(panel_assignments__adviser__user=request.user).exists()
            )
        if not authorized:
            return render(request, "core/access_denied.html", status=403)
        if thesis.status != FinalThesis.Status.SUBMITTED:
            messages.error(request, "Only submitted theses can receive approval.")
        else:
            if action == "adviser_approve":
                thesis.adviser_approved = True
            else:
                thesis.panel_approved = True
            thesis.save(update_fields=["adviser_approved", "panel_approved"])
            log_action(request, action.replace("_", " "), "Repository", thesis)
            messages.success(request, "Approval recorded.")
    elif action == "approve":
        if not has_role(request.user, REPOSITORY_ROLES):
            return render(request, "core/access_denied.html", status=403)
        validation_errors = final_validation_errors(thesis)
        # Drop file requirements only if already present; validation checks still apply before approval.
        if thesis.status != FinalThesis.Status.SUBMITTED or not thesis.adviser_approved or not thesis.panel_approved:
            messages.error(request, "Final thesis needs submission, adviser approval, and panel approval first.")
        elif validation_errors:
            messages.error(request, "Final thesis cannot be approved yet: " + " ".join(validation_errors))
        else:
            thesis.status = FinalThesis.Status.APPROVED
            thesis.save(update_fields=["status"])
            log_action(request, "Approved final thesis", "Repository", thesis)
            messages.success(request, "Final thesis validated and approved.")
    elif action == "archive":
        if not has_role(request.user, REPOSITORY_ROLES):
            return render(request, "core/access_denied.html", status=403)
        if thesis.status != FinalThesis.Status.APPROVED:
            messages.error(request, "Only approved final theses can be archived.")
        else:
            thesis.status = FinalThesis.Status.ARCHIVED
            thesis.archived_at = timezone.now()
            final_defense = Defense.objects.filter(proposal=thesis.proposal, defense_type=Defense.Type.FINAL, status=Defense.Status.COMPLETED).order_by("-defense_date").first()
            if final_defense:
                thesis.defense_date = final_defense.defense_date
            thesis.save(update_fields=["status", "archived_at", "defense_date"])
            log_action(request, "Archived thesis", "Repository", thesis)
            messages.success(request, "Thesis archived in the repository.")
    else:
        messages.error(request, "Unknown thesis action.")
    return redirect("final_thesis_detail", pk=pk)


# -----------------------------------------------------------------------------
# Supporting pages: reports, user notifications, and audit history.
# -----------------------------------------------------------------------------
@role_required(*MANAGEMENT_ROLES)
def reports(request):
    context = {
        "students_without_adviser": Student.objects.filter(is_active=True, adviser__isnull=True).count(),
        "approved_topics": ThesisTopic.objects.filter(status=ThesisTopic.Status.APPROVED).count(),
        "pending_topics": ThesisTopic.objects.exclude(status__in=[ThesisTopic.Status.APPROVED, ThesisTopic.Status.REJECTED]).count(),
        "pending_proposals": ThesisProposal.objects.exclude(status__in=[ThesisProposal.Status.APPROVED, ThesisProposal.Status.REJECTED]).count(),
        "upcoming_defenses": Defense.objects.filter(status=Defense.Status.SCHEDULED, defense_date__gte=timezone.localdate()).count(),
        "completed_defenses": Defense.objects.filter(status=Defense.Status.COMPLETED).count(),
        "revisions_pending": RevisionRequest.objects.exclude(status=RevisionRequest.Status.APPROVED).count(),
        "archived_theses": FinalThesis.objects.filter(status=FinalThesis.Status.ARCHIVED).count(),
        "adviser_workload": [
            {"adviser": adviser, "advisees": adviser.active_students, "maximum": adviser.max_advisees,
             "available": max(0, adviser.max_advisees - adviser.active_students)}
            for adviser in Adviser.objects.annotate(active_students=Count("students", filter=Q(students__is_active=True)))
        ],
        "most_accessed": FinalThesis.objects.filter(status=FinalThesis.Status.ARCHIVED).annotate(access_count=Count("access_records")).order_by("-access_count", "title")[:5],
        "defense_results": Defense.objects.exclude(final_decision="").values("final_decision").annotate(total=Count("id")).order_by("final_decision"),
        "topics_by_area": ThesisTopic.objects.values("research_area").annotate(total=Count("id")).order_by("research_area"),
    }
    return render(request, "core/reports.html", context)


@login_required
def notifications(request):
    notices = Notification.objects.filter(recipient=request.user)
    return render(request, "core/notifications.html", {"notifications": notices})


@login_required
@require_POST
def notification_read(request, pk):
    notice = get_object_or_404(Notification, pk=pk, recipient=request.user)
    notice.is_read = True
    notice.save(update_fields=["is_read"])
    return redirect("notifications")


@role_required("Administrator", "Thesis Coordinator")
def audit_log(request):
    logs = AuditLog.objects.select_related("user")[:300]
    return render(request, "core/audit_log.html", {"logs": logs})


def login_view(request):
    """A small custom login wrapper lets the project keep a matching page design."""
    if request.user.is_authenticated:
        return redirect("home")
    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.get_user())
        return redirect(request.GET.get("next") or "home")
    return render(request, "core/login.html", {"form": form})


@login_required
def student_portal(request):
    """Show the signed-in account's linked student record when one has been connected."""
    try:
        student = request.user.student_profile
    except Student.DoesNotExist:
        student = None
    if student is None:
        messages.info(request, "This account has no linked student profile yet. An administrator can connect it from Students.")
        return redirect("home")
    return render(request, "core/student_portal.html", {"student": student,
        "topics": student.topics.all(), "revisions": student.revision_requests.all(), "final_theses": student.final_theses.all()})
