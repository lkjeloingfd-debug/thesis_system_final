from django import forms
from pathlib import Path
from .models import (
    Student, Adviser, ThesisTopic, ThesisProposal, ProposalDocument, Defense,
    DefensePanel, EvaluationCriterion, RevisionRequest, FinalThesis,
)


class StudentForm(forms.ModelForm):
    class Meta:
        model = Student
        fields = ["user", "student_number", "first_name", "middle_name", "last_name",
                  "email", "contact_number", "program", "year_level", "academic_year", "adviser", "is_active"]

    def clean_year_level(self):
        year = self.cleaned_data["year_level"]
        if not 1 <= year <= 6:
            raise forms.ValidationError("Year level must be between 1 and 6.")
        return year

    def clean(self):
        data = super().clean()
        adviser = data.get("adviser")
        current = self.instance if self.instance and self.instance.pk else None
        # Don't assign an active student to an inactive adviser or exceed the workload limit.
        if adviser and data.get("is_active", True):
            if not adviser.is_active:
                self.add_error("adviser", "Choose an active adviser.")
            elif adviser.current_advisee_count >= adviser.max_advisees and (not current or current.adviser_id != adviser.pk):
                self.add_error("adviser", "This adviser has reached the maximum advisee capacity.")
        return data


class AdviserForm(forms.ModelForm):
    class Meta:
        model = Adviser
        fields = ["user", "faculty_id", "first_name", "last_name", "department", "program",
                  "email", "contact_number", "specialization", "max_advisees", "is_active"]


class ThesisTopicForm(forms.ModelForm):
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Students can only submit a topic for their own linked profile.
        # Advisers see only students assigned to them; coordinator/admin keep the full list.
        if user and not (user.is_staff or user.is_superuser or user.groups.filter(name__in=["Administrator", "Thesis Coordinator"]).exists()):
            try:
                student = user.student_profile
            except Student.DoesNotExist:
                student = None
            if student and user.groups.filter(name="Student").exists():
                self.fields["student"].queryset = Student.objects.filter(pk=student.pk)
            else:
                try:
                    adviser = user.adviser_profile
                except Adviser.DoesNotExist:
                    adviser = None
                if adviser and user.groups.filter(name="Adviser").exists():
                    self.fields["student"].queryset = Student.objects.filter(adviser=adviser, is_active=True)
                else:
                    self.fields["student"].queryset = Student.objects.none()

    class Meta:
        model = ThesisTopic
        fields = ["student", "title", "background", "problem_statement", "objectives", "proposed_technology", "research_area"]
        widgets = {"background": forms.Textarea(attrs={"rows": 3}), "problem_statement": forms.Textarea(attrs={"rows": 3}), "objectives": forms.Textarea(attrs={"rows": 3})}


class ThesisProposalForm(forms.ModelForm):
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Only approved topics appear in the dropdown, and ordinary accounts are scoped to linked records.
        self.fields["topic"].queryset = self.fields["topic"].queryset.filter(status=ThesisTopic.Status.APPROVED)
        if user and not (user.is_staff or user.is_superuser or user.groups.filter(name__in=["Administrator", "Thesis Coordinator"]).exists()):
            try:
                student = user.student_profile
            except Student.DoesNotExist:
                student = None
            if student and user.groups.filter(name="Student").exists():
                self.fields["topic"].queryset = self.fields["topic"].queryset.filter(student=student)
            else:
                try:
                    adviser = user.adviser_profile
                except Adviser.DoesNotExist:
                    adviser = None
                if adviser and user.groups.filter(name="Adviser").exists():
                    self.fields["topic"].queryset = self.fields["topic"].queryset.filter(student__adviser=adviser)
                else:
                    self.fields["topic"].queryset = self.fields["topic"].queryset.none()

    class Meta:
        model = ThesisProposal
        fields = ["topic", "version", "introduction", "background", "problem_statement", "objectives",
                  "scope_limitations", "significance", "related_literature", "methodology", "references"]
        widgets = {field: forms.Textarea(attrs={"rows": 3}) for field in ["introduction", "background", "problem_statement", "objectives", "scope_limitations", "significance", "related_literature", "methodology", "references"]}

    def clean_topic(self):
        topic = self.cleaned_data["topic"]
        # The requirements describe approval as the gate before proposal creation.
        if topic.status != ThesisTopic.Status.APPROVED:
            raise forms.ValidationError("A proposal can only be created for an approved thesis topic.")
        return topic


class ProposalDocumentForm(forms.ModelForm):
    class Meta:
        model = ProposalDocument
        fields = ["file", "version_label", "notes"]

    def clean_file(self):
        file = self.cleaned_data.get("file")
        if file and Path(file.name).suffix.lower() not in {".pdf", ".doc", ".docx"}:
            raise forms.ValidationError("Upload a PDF, DOC, or DOCX document.")
        if file and file.size > 20 * 1024 * 1024:
            raise forms.ValidationError("The file must be 20 MB or smaller.")
        return file


class DefenseForm(forms.ModelForm):
    class Meta:
        model = Defense
        fields = ["proposal", "defense_type", "defense_date", "defense_time", "venue", "notes"]
        widgets = {"defense_date": forms.DateInput(attrs={"type": "date"}), "defense_time": forms.TimeInput(attrs={"type": "time"}), "notes": forms.Textarea(attrs={"rows": 3})}

    def clean_proposal(self):
        proposal = self.cleaned_data["proposal"]
        if proposal.status != ThesisProposal.Status.APPROVED:
            raise forms.ValidationError("Only an approved proposal can be scheduled for defense.")
        return proposal


class DefensePanelForm(forms.ModelForm):
    class Meta:
        model = DefensePanel
        fields = ["adviser", "role"]

    def clean_adviser(self):
        adviser = self.cleaned_data["adviser"]
        if not adviser.is_active:
            raise forms.ValidationError("Inactive faculty cannot be assigned to a defense panel.")
        return adviser


class EvaluationCriterionForm(forms.ModelForm):
    class Meta:
        model = EvaluationCriterion
        fields = ["name", "description", "weight", "is_active"]


class RevisionRequestForm(forms.ModelForm):
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Advisers can request changes only for students they advise. Coordinators keep full access.
        if user and not (user.is_staff or user.is_superuser or user.groups.filter(name__in=["Administrator", "Thesis Coordinator"]).exists()):
            try:
                adviser = user.adviser_profile
            except Adviser.DoesNotExist:
                adviser = None
            if adviser and user.groups.filter(name="Adviser").exists():
                self.fields["student"].queryset = Student.objects.filter(adviser=adviser)
                self.fields["defense"].queryset = self.fields["defense"].queryset.filter(proposal__topic__student__adviser=adviser)
            else:
                self.fields["student"].queryset = Student.objects.none()
                self.fields["defense"].queryset = self.fields["defense"].queryset.none()

    class Meta:
        model = RevisionRequest
        fields = ["defense", "student", "section", "required_change", "reason", "deadline", "priority"]
        widgets = {"deadline": forms.DateInput(attrs={"type": "date"}), "required_change": forms.Textarea(attrs={"rows": 3}), "reason": forms.Textarea(attrs={"rows": 2})}

    def clean(self):
        data = super().clean()
        defense = data.get("defense")
        student = data.get("student")
        # A revision should belong to the same student who owns the defense's topic.
        if defense and student and defense.proposal.topic.student_id != student.pk:
            self.add_error("student", "This student is not connected to the selected defense.")
        return data


class StudentRevisionSubmissionForm(forms.ModelForm):
    class Meta:
        model = RevisionRequest
        fields = ["student_response", "revised_file"]
        widgets = {"student_response": forms.Textarea(attrs={"rows": 3})}

    def clean_revised_file(self):
        file = self.cleaned_data.get("revised_file")
        if file and Path(file.name).suffix.lower() not in {".pdf", ".doc", ".docx"}:
            raise forms.ValidationError("Upload a PDF, DOC, or DOCX document.")
        if file and file.size > 20 * 1024 * 1024:
            raise forms.ValidationError("The file must be 20 MB or smaller.")
        return file


class FinalThesisForm(forms.ModelForm):
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user and not (user.is_staff or user.is_superuser or user.groups.filter(name__in=["Administrator", "Thesis Coordinator", "Repository Manager"]).exists()):
            try:
                student = user.student_profile
            except Student.DoesNotExist:
                student = None
            if student and user.groups.filter(name="Student").exists():
                self.fields["student"].queryset = Student.objects.filter(pk=student.pk)
                self.fields["proposal"].queryset = self.fields["proposal"].queryset.filter(topic__student=student, status=ThesisProposal.Status.APPROVED)
            else:
                try:
                    adviser = user.adviser_profile
                except Adviser.DoesNotExist:
                    adviser = None
                if adviser and user.groups.filter(name="Adviser").exists():
                    self.fields["student"].queryset = Student.objects.filter(adviser=adviser)
                    self.fields["proposal"].queryset = self.fields["proposal"].queryset.filter(topic__student__adviser=adviser, status=ThesisProposal.Status.APPROVED)
                else:
                    self.fields["student"].queryset = Student.objects.none()
                    self.fields["proposal"].queryset = self.fields["proposal"].queryset.none()

    class Meta:
        model = FinalThesis
        fields = ["student", "proposal", "title", "abstract", "keywords", "research_area", "final_pdf", "approval_form", "access_level"]
        widgets = {"abstract": forms.Textarea(attrs={"rows": 4})}

    def clean_final_pdf(self):
        file = self.cleaned_data.get("final_pdf")
        if file and Path(file.name).suffix.lower() != ".pdf":
            raise forms.ValidationError("The final manuscript must be a PDF file.")
        if file and file.size > 30 * 1024 * 1024:
            raise forms.ValidationError("The final manuscript must be 30 MB or smaller.")
        return file

    def clean_approval_form(self):
        file = self.cleaned_data.get("approval_form")
        if file and Path(file.name).suffix.lower() != ".pdf":
            raise forms.ValidationError("The approval form must be a PDF file.")
        if file and file.size > 15 * 1024 * 1024:
            raise forms.ValidationError("The approval form must be 15 MB or smaller.")
        return file

    def clean(self):
        data = super().clean()
        student = data.get("student")
        proposal = data.get("proposal")
        if student and proposal and proposal.topic.student_id != student.pk:
            self.add_error("proposal", "The proposal belongs to a different student.")
        if proposal and proposal.status != ThesisProposal.Status.APPROVED:
            self.add_error("proposal", "Final thesis must be connected to an approved proposal.")
        return data
