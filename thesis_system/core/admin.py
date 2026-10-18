from django.contrib import admin
from .models import (
    Student, Adviser, ThesisTopic, TopicReview, ThesisProposal, ProposalReview, ProposalDocument,
    Defense, DefensePanel, EvaluationCriterion, Evaluation, EvaluationScore,
    RevisionRequest, FinalThesis, Notification, AuditLog, RepositoryAccess,
)


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("student_number", "last_name", "first_name", "program", "year_level", "adviser", "is_active")
    search_fields = ("student_number", "first_name", "last_name", "email")
    list_filter = ("program", "year_level", "is_active")


@admin.register(Adviser)
class AdviserAdmin(admin.ModelAdmin):
    list_display = ("faculty_id", "first_name", "last_name", "specialization", "is_active")
    search_fields = ("faculty_id", "first_name", "last_name", "specialization")
    list_filter = ("department", "is_active")


@admin.register(ThesisTopic)
class ThesisTopicAdmin(admin.ModelAdmin):
    list_display = ("title", "student", "research_area", "status", "submitted_at")
    search_fields = ("title", "student__student_number", "student__last_name", "research_area")
    list_filter = ("status", "research_area")


@admin.register(ThesisProposal)
class ThesisProposalAdmin(admin.ModelAdmin):
    list_display = ("topic", "version", "status", "submitted_at")
    list_filter = ("status",)


@admin.register(Defense)
class DefenseAdmin(admin.ModelAdmin):
    list_display = ("proposal", "defense_type", "defense_date", "defense_time", "venue", "status")
    list_filter = ("defense_type", "status", "defense_date")


@admin.register(EvaluationCriterion)
class EvaluationCriterionAdmin(admin.ModelAdmin):
    list_display = ("name", "weight", "is_active")


@admin.register(FinalThesis)
class FinalThesisAdmin(admin.ModelAdmin):
    list_display = ("title", "student", "status", "access_level", "archived_at")
    list_filter = ("status", "access_level")
    search_fields = ("title", "student__last_name", "keywords")


# The remaining models are also available for maintenance through Django admin.
admin.site.register([TopicReview, ProposalReview, ProposalDocument, DefensePanel, Evaluation, EvaluationScore,
                     RevisionRequest, Notification, AuditLog, RepositoryAccess])
