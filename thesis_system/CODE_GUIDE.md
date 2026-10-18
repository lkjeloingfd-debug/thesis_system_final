# Code Guide — how the Thesis Management System connects

This guide is meant to sit next to the source code while you learn. Most Python files
include comments near the important logic. You do not need to understand every module
at once; trace one request from its URL to its template and database first.

## 1. The request path

For example, the student list uses this route:

1. `core/urls.py` maps `students/` to `views.student_list`.
2. `core/views.py` checks the signed-in user's access, reads search filters, and asks
   the `Student` model for matching rows.
3. The view turns those records into `rows` and renders `core/list_page.html`.
4. `core/templates/core/base.html` supplies the shared header, navigation, styles,
   messages, and page layout.
5. When a user submits a form, the request goes back to the view using POST.
6. A form in `core/forms.py` validates the values. The view calls `form.save()` or
   updates a model, Django writes it to SQLite, and the view redirects to the saved record.

## 2. Files and their responsibilities

### `config/`

- `settings.py`: Django configuration: app registration, middleware, database, Manila
  timezone, uploaded-file location, and login redirects. `DEBUG=True` is only for local
  development and must not be left on for a real deployment.
- `urls.py`: top-level URL routing, Django admin, logout, and local serving of uploaded
  files while `DEBUG` is enabled.
- `asgi.py` and `wsgi.py`: entry points used by deployment servers.

### `core/models.py` — the database structure

A model class describes a database table. A `ForeignKey` connects one record to another.
For example, `ThesisTopic.student` means every topic is linked to the student who proposed it.
`ThesisProposal.topic` means the proposal belongs to an approved topic. A defense belongs to
a proposal, and a final thesis is linked to both a student and proposal.

Important relationship chain:

`Student -> ThesisTopic -> ThesisProposal -> Defense -> Evaluation -> RevisionRequest -> FinalThesis`

Other records support this chain:

- `Adviser`: faculty identity and advisee capacity; `Student.adviser` records assignment.
- `TopicReview` / `ProposalReview`: keep review history rather than overwriting old comments.
- `ProposalDocument`: stores submitted proposal-file versions.
- `DefensePanel`: connects advisers/faculty to a defense with a panel role.
- `EvaluationCriterion` / `Evaluation` / `EvaluationScore`: define weights, who scored, and
  each score. `Evaluation.weighted_score` calculates the weighted total for one panel member.
- `Defense.final_score` / `Defense.final_decision`: the consolidated official result recorded
  by the coordinator after valid panel evaluations exist.
- `RevisionRequest`: what must change, by when, file submission, and verification status.
- `FinalThesis`: final manuscript, approval form, approvals, status, and access level.
- `RepositoryAccess`: records archive page views/downloads.
- `Notification`: in-app notices. `AuditLog`: records important changes and who made them.

### `core/forms.py` — input and validation

A `ModelForm` creates HTML form fields based on a model. `clean_<field>()` validates one
field; `clean()` validates relationships between multiple fields. Examples include:

- Adviser must be active and under the advisee limit before assigning a student.
- Proposal can only link to an approved topic.
- Defense can only be scheduled for an approved proposal.
- Proposal/final/revision uploads are checked for extension and size.
- Final thesis student and proposal must refer to the same student.

Forms handle normal input errors; important workflow gates are also checked in views so a
user cannot bypass them simply by sending a crafted request.

### `core/views.py` — workflow logic

Views are Python functions that receive an HTTP request and return a page or redirect.
The file is divided into sections that follow the project modules. Find a function using
Ctrl+F in VS Code (for example, `def topic_approve`).

- `has_role()` / `role_required()`: checks group membership before a protected action.
- `visible_students()`: limits student records visible to the signed-in role/profile.
- `log_action()`: records important writes into the audit log.
- `send_notice()`: stores an in-app notification for a linked account.
- `final_validation_errors()`: lists requirements that block final-thesis submission.
- Module views implement search/list pages, forms, workflow status transitions, and detail pages.

Most create/edit actions follow this pattern:

`request -> form validation -> save/update model -> audit/notification -> redirect`

Most workflow buttons use POST forms with a CSRF token. This prevents a normal browser page
from accidentally making a state change just by opening a link.

### `core/urls.py` — named URL routes

Every route has a name such as `student_list` or `topic_detail`. Templates use `{% url %}`
with these names. Prefer linking to route names rather than typing the URL into HTML directly.
The detail view usually receives a database primary key as `pk`.

### `core/templates/core/` — pages

- `base.html`: shared header, navigation, messages, CSS, and logout form.
- `home.html`: dashboard counts and shortcuts.
- `list_page.html`: shared table layout used by several modules.
- `form_page.html`: shared create/edit form layout.
- `student_detail.html`, `topic_detail.html`, `proposal_detail.html`, `defense_detail.html`,
  `evaluation_page.html`, `revision_detail.html`, and `final_thesis_detail.html`: module detail pages.
- `reports.html`, `notifications.html`, `audit_log.html`, `student_portal.html`: supporting pages.
- `login.html` / `access_denied.html`: sign-in and permission feedback.

Django's template syntax is not plain Python. For instance, `{{ student.full_name }}` prints a
value, `{% if student.is_active %}` conditionally shows HTML, and `{% url 'student_list' %}`
looks up a named URL.

### `core/apps.py`, `admin.py`, and migrations

- `apps.py` runs a `post_migrate` setup hook that creates the role groups and starter criteria.
- `admin.py` exposes important models at `/admin/` for setup and record maintenance.
- `migrations/` are versioned changes to the database schema. Do not delete them casually.
  After changing `models.py`, use `makemigrations`, then `migrate`.

### `core/tests.py` — automated checks

The tests create their own temporary database with sample users and thesis records. They
check that pages render, role scoping is enforced, topic review changes status, proposal review
history is saved, overlapping defense slots are rejected, scores calculate correctly, and the
final defense result can be consolidated. Run them with `python manage.py test core`.

## 3. The thesis flow as status changes

1. Register a student and connect an adviser.
2. Student submits a topic; adviser reviews it and management approves a recommendation.
3. Create a proposal from the approved topic; submit sections and a document; adviser records review.
4. Schedule the approved proposal for defense; assign chairperson, adviser, and panel members.
5. Mark the defense completed. Assigned panel users submit scores against criteria totalling 100%.
6. Coordinator finalizes the official decision; the app averages the panel weighted scores.
7. Create revision requests. The student submits the revised file/response; faculty verifies it.
8. Upload the final thesis PDF and approval form; complete defense/revision checks and record adviser/panel approval.
9. Repository manager approves and archives. Archive access/downloads can then be logged and reported.

The prototype has a few intentional simplifications listed in `README_FIRST_STEPS.txt`:
keyword/area matching is only a rough thesis-topic similarity hint; uploaded files use local
media storage; emails are printed to the server console; and reports are table/count based.
Before presenting the project, demonstrate the full flow with test data, confirm requirements
with your instructor, and do not put real student data into a local demo build.
