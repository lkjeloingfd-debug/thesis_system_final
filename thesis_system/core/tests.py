from datetime import date, time
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import (
    Student, Adviser, ThesisTopic, ThesisProposal, Defense,
    EvaluationCriterion, Evaluation, EvaluationScore, RevisionRequest, FinalThesis,
)

User = get_user_model()


class ThesisManagementSystemTests(TestCase):
    def setUp(self):
        # Staff is used in tests as the coordinator/admin account, so workflow pages are available.
        self.user = User.objects.create_user(username="testadmin", password="safe-test-password", is_staff=True)
        self.client.force_login(self.user)
        self.adviser = Adviser.objects.create(
            faculty_id="FAC-001", first_name="Maria", last_name="Santos",
            department="IT", specialization="Software Engineering", max_advisees=5,
        )
        self.student = Student.objects.create(
            student_number="2026-00125", first_name="Juan", last_name="Dela Cruz",
            email="juan@example.com", program="BS Information Technology", year_level=4,
            academic_year="2026-2027", adviser=self.adviser,
        )
        self.topic = ThesisTopic.objects.create(
            student=self.student, title="Online Thesis Management System",
            background="A sample background.", problem_statement="A sample problem.",
            objectives="A sample objective.", proposed_technology="Django", research_area="Information Systems",
            status=ThesisTopic.Status.APPROVED,
        )
        self.proposal = ThesisProposal.objects.create(
            topic=self.topic, version=1, introduction="Intro", background="Background",
            problem_statement="Problem", objectives="Objectives", methodology="Methodology",
            status=ThesisProposal.Status.APPROVED,
        )
        self.defense = Defense.objects.create(
            proposal=self.proposal, defense_type=Defense.Type.PROPOSAL,
            defense_date=date(2027, 1, 10), defense_time=time(9, 0), venue="Research Lab",
            created_by=self.user,
        )
        self.revision = RevisionRequest.objects.create(
            defense=self.defense, student=self.student, section="Chapter 3",
            required_change="Improve the methodology description.", requested_by=self.user,
        )
        self.final_thesis = FinalThesis.objects.create(
            student=self.student, proposal=self.proposal, title=self.topic.title,
            created_by=self.user,
        )

    def test_student_list_page_loads_and_search_works(self):
        response = self.client.get(reverse("student_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2026-00125")
        response = self.client.get(reverse("student_list"), {"q": "00125"})
        self.assertContains(response, "Juan")

    def test_student_deactivation_keeps_record(self):
        response = self.client.post(reverse("student_deactivate", args=[self.student.pk]))
        self.assertRedirects(response, reverse("student_list"))
        self.student.refresh_from_db()
        self.assertFalse(self.student.is_active)
        self.assertTrue(Student.objects.filter(pk=self.student.pk).exists())

    def test_workflow_pages_render(self):
        # These checks catch broken template links and context variables early.
        urls = [
            reverse("home"), reverse("student_detail", args=[self.student.pk]),
            reverse("adviser_list"), reverse("adviser_detail", args=[self.adviser.pk]),
            reverse("topic_list"), reverse("topic_detail", args=[self.topic.pk]),
            reverse("proposal_list"), reverse("proposal_detail", args=[self.proposal.pk]),
            reverse("defense_list"), reverse("defense_detail", args=[self.defense.pk]),
            reverse("evaluation_page", args=[self.defense.pk]), reverse("criteria_list"),
            reverse("revision_list"), reverse("revision_detail", args=[self.revision.pk]),
            reverse("repository_list"), reverse("final_thesis_detail", args=[self.final_thesis.pk]),
            reverse("reports"), reverse("notifications"), reverse("audit_log"),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                # Evaluation entry is intentionally blocked until the defense is completed.
                if url == reverse("evaluation_page", args=[self.defense.pk]):
                    self.assertEqual(response.status_code, 302, f"Scheduled defenses should not accept scoring yet: {url}")
                else:
                    self.assertEqual(response.status_code, 200, f"Failed URL: {url}; status={response.status_code}")

    def test_proposal_requires_approved_topic(self):
        self.topic.status = ThesisTopic.Status.SUBMITTED
        self.topic.save()
        from .forms import ThesisProposalForm
        form = ThesisProposalForm(data={"topic": self.topic.pk, "version": 2, "introduction": "x"})
        self.assertFalse(form.is_valid())
        self.assertIn("topic", form.errors)

    def test_default_evaluation_weights_total_100(self):
        total = sum(EvaluationCriterion.objects.filter(is_active=True).values_list("weight", flat=True), Decimal("0"))
        self.assertEqual(total, Decimal("100.00"))

    def test_evaluation_weighted_score(self):
        criterion = EvaluationCriterion.objects.create(name="Test criterion", weight=Decimal("100.00"))
        evaluation = Evaluation.objects.create(defense=self.defense, evaluator=self.user)
        EvaluationScore.objects.create(evaluation=evaluation, criterion=criterion, score=Decimal("88.50"))
        self.assertEqual(evaluation.weighted_score, Decimal("88.50"))


    def test_coordinator_finalizes_average_panel_score_and_official_decision(self):
        # Create a completed final defense linked to the already approved proposal.
        final_defense = Defense.objects.create(
            proposal=self.proposal,
            defense_type=Defense.Type.FINAL,
            defense_date=date(2027, 2, 10),
            defense_time=time(9, 0),
            venue="Research Lab 2",
            status=Defense.Status.COMPLETED,
            created_by=self.user,
        )
        evaluation = Evaluation.objects.create(
            defense=final_defense,
            evaluator=self.user,
            decision=Evaluation.Decision.PASS,
        )
        # Give the evaluation a mark for every active criterion, as the UI requires.
        for criterion in EvaluationCriterion.objects.filter(is_active=True):
            EvaluationScore.objects.create(evaluation=evaluation, criterion=criterion, score=Decimal("90.00"))

        response = self.client.post(
            reverse("defense_finalize_decision", args=[final_defense.pk]),
            {"final_decision": Defense.Decision.MINOR},
        )
        self.assertRedirects(response, reverse("defense_detail", args=[final_defense.pk]))
        final_defense.refresh_from_db()
        self.assertEqual(final_defense.final_score, Decimal("90.00"))
        self.assertEqual(final_defense.final_decision, Defense.Decision.MINOR)

    def test_user_must_log_in(self):
        self.client.logout()
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_topic_review_recommendation_then_final_approval(self):
        new_topic = ThesisTopic.objects.create(
            student=self.student, title="Another thesis idea", background="Background",
            problem_statement="Problem", objectives="Objectives", research_area="Cybersecurity",
            status=ThesisTopic.Status.SUBMITTED,
        )
        response = self.client.post(reverse("topic_review", args=[new_topic.pk]), {
            "recommendation": "APPROVE", "comments": "The topic is suitable after review.",
        })
        self.assertRedirects(response, reverse("topic_detail", args=[new_topic.pk]))
        new_topic.refresh_from_db()
        self.assertEqual(new_topic.status, ThesisTopic.Status.RECOMMENDED)
        response = self.client.post(reverse("topic_approve", args=[new_topic.pk]))
        self.assertRedirects(response, reverse("topic_detail", args=[new_topic.pk]))
        new_topic.refresh_from_db()
        self.assertEqual(new_topic.status, ThesisTopic.Status.APPROVED)

    def test_proposal_review_history_is_kept(self):
        from .models import ProposalReview
        draft_topic = ThesisTopic.objects.create(
            student=self.student, title="Review history topic", background="Background",
            problem_statement="Problem", objectives="Objectives", research_area="Data Analytics",
            status=ThesisTopic.Status.APPROVED,
        )
        proposal = ThesisProposal.objects.create(topic=draft_topic, version=1, status=ThesisProposal.Status.SUBMITTED)
        response = self.client.post(reverse("proposal_review", args=[proposal.pk]), {
            "status": "UNDER_REVIEW", "adviser_comments": "Review started.",
        })
        self.assertRedirects(response, reverse("proposal_detail", args=[proposal.pk]))
        self.assertTrue(ProposalReview.objects.filter(proposal=proposal, status="UNDER_REVIEW").exists())

    def test_final_thesis_submission_waits_for_final_requirements(self):
        response = self.client.post(reverse("final_thesis_action", args=[self.final_thesis.pk]), {"action": "submit"})
        self.assertRedirects(response, reverse("final_thesis_detail", args=[self.final_thesis.pk]))
        self.final_thesis.refresh_from_db()
        self.assertEqual(self.final_thesis.status, FinalThesis.Status.DRAFT)

    def test_student_role_can_only_see_own_student_record(self):
        from django.contrib.auth.models import Group
        student_user = User.objects.create_user(username="studentuser", password="safe-test-password")
        student_user.groups.add(Group.objects.get(name="Student"))
        self.student.user = student_user
        self.student.save(update_fields=["user"])
        other = Student.objects.create(
            student_number="2026-00999", first_name="Ana", last_name="Example",
            program="BS Information Technology", year_level=3, academic_year="2026-2027",
        )
        self.client.force_login(student_user)
        response = self.client.get(reverse("student_list"))
        self.assertContains(response, "2026-00125")
        self.assertNotContains(response, "2026-00999")
        response = self.client.get(reverse("student_detail", args=[other.pk]))
        self.assertEqual(response.status_code, 403)

    def test_archived_thesis_view_is_logged(self):
        from .models import RepositoryAccess
        self.final_thesis.status = FinalThesis.Status.ARCHIVED
        self.final_thesis.access_level = FinalThesis.Access.INSTITUTION
        self.final_thesis.save(update_fields=["status", "access_level"])
        response = self.client.get(reverse("final_thesis_detail", args=[self.final_thesis.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(RepositoryAccess.objects.filter(thesis=self.final_thesis, action=RepositoryAccess.Action.VIEW).exists())

    def test_defense_schedule_rejects_conflict(self):
        count_before = Defense.objects.count()
        response = self.client.post(reverse("defense_create"), {
            "proposal": self.proposal.pk, "defense_type": "FINAL", "defense_date": "2027-01-10",
            "defense_time": "09:00", "venue": "Research Lab", "notes": "Conflict test",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Defense.objects.count(), count_before)
