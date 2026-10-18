from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # Register startup-independent setup that runs after Django applies migrations.
        from django.db.models.signals import post_migrate
        from django.dispatch import receiver

        @receiver(post_migrate, dispatch_uid="core.create_initial_roles_and_criteria")
        def create_initial_roles_and_criteria(sender, **kwargs):
            # Groups provide the basic role-based access controls used by views.py.
            if sender.name != "core":
                return
            from django.contrib.auth.models import Group
            from .models import EvaluationCriterion
            for role in ["Administrator", "Thesis Coordinator", "Adviser", "Panel Member", "Student", "Repository Manager"]:
                Group.objects.get_or_create(name=role)
            # These default weights total 100%, matching the project requirement document.
            defaults = [
                ("Problem Definition", "Clarity and importance of the problem", "15.00"),
                ("Literature Review", "Quality of related literature", "15.00"),
                ("Methodology", "Appropriateness of the method", "20.00"),
                ("System/Research Output", "Quality of the implemented output", "25.00"),
                ("Presentation", "Organization and communication", "15.00"),
                ("Question and Answer", "Responses to panel questions", "10.00"),
            ]
            for name, description, weight in defaults:
                EvaluationCriterion.objects.get_or_create(name=name, defaults={"description": description, "weight": weight})
