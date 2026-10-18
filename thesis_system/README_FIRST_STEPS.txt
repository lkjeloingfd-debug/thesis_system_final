THESIS MANAGEMENT SYSTEM — FULL INTEGRATED LEARNING VERSION
==========================================================

This project builds on the Django structure from your original ZIP. It is no longer
only a student module: it connects the main thesis workflow from student/adviser
records through topic approval, proposals, defenses, evaluations, revisions, and
repository archiving.

It is a learning/development prototype, not a production-ready system. Read
CODE_GUIDE.md for a map of the files and how the parts connect.

WHAT IS INCLUDED
----------------
- Student registration, profile, searching/filtering, editing, deactivation.
- Adviser records, student assignment through the student form, and workload count.
- Thesis-topic submission, similarity hints, review history, recommendation, approval.
- Proposal sections, required submission fields, uploaded document versions, review history.
- Defense scheduling, venue/student conflict checks, panel assignment, notifications.
- Configurable evaluation criteria, weighted scores, panel decisions, consolidated result.
- Revision requests, revised-file submission, faculty verification.
- Final thesis PDF and approval-form upload, validation, adviser/panel approvals, archiving.
- Repository access levels, tracked views/downloads, and most-accessed report.
- Login/logout, role groups, student portal, notifications, audit trail, dashboard/reports.
- Django admin, migrations, and automated tests.

RUN IT ON WINDOWS (VS CODE TERMINAL / POWERSHELL)
--------------------------------------------------
1. Extract the ZIP file.
2. In VS Code choose File > Open Folder, and select the extracted thesis_system folder.
3. Open Terminal > New Terminal. Make sure the terminal is inside that folder (the same
   folder containing manage.py).
4. Create a local virtual environment if you do not already have one:
       py -m venv venv
       .env\Scripts\Activate.ps1
   If PowerShell blocks activation, you can run the commands using your usual Python
   interpreter; the activation command itself is not an application requirement.
5. Install the project library:
       python -m pip install -r requirements.txt
6. Create/update the database tables:
       python manage.py migrate
7. Create your first account:
       python manage.py createsuperuser
8. Start the local development server:
       python manage.py runserver
9. Open this address in your browser:
       http://127.0.0.1:8000/

The login page is /login/. The administrator page is /admin/.
The first database migration creates the app tables. After migrations, Django also
creates the six role groups and the default evaluation criteria (weights total 100%).

SET UP ROLES AND LINK ACCOUNTS
------------------------------
1. Sign in to /admin/ using the superuser you created.
2. Under Authentication and Authorization > Users, create a user for each person.
3. Add that user to the appropriate Group: Administrator, Thesis Coordinator, Adviser,
   Panel Member, Student, or Repository Manager. Names must match exactly.
4. Create the matching Student or Adviser record in the app's admin pages, and select
   the user's account in the User field. That link lets the system know which profile
   belongs to a signed-in student/adviser.
5. Assign an adviser to an active student in Students. The form checks adviser status
   and available advisee capacity.
6. The superuser/staff account can manage the learning prototype while you set it up.
   For testing normal permissions, use non-staff users and give them only the required
   group(s), then link the relevant Student/Adviser profile.

MAIN WORKFLOW
-------------
Student record -> Adviser assignment -> Topic submission -> Adviser review -> Topic
approval -> Proposal creation/submission -> Proposal review and documents -> Defense
schedule -> Panel assignment -> Defense completed -> Individual panel evaluations ->
Coordinator final decision -> Revision requests and verification -> Final thesis PDF
and approval form -> Adviser and panel approvals -> Repository approval/archive ->
Reports and dashboard.

WHERE TO LOOK IN THE SOURCE CODE
-------------------------------
config/settings.py        Django settings, installed app, SQLite DB, uploads, login.
config/urls.py            Main URL table, Django admin, logout, and development uploads.
core/urls.py              Maps named page URLs to view functions.
core/models.py            Database tables and relationships between workflow records.
core/forms.py             Form fields and validation before user-submitted values are saved.
core/views.py             Workflow logic: permissions, database queries, status changes,
                          validation, notifications, and audit logging.
core/apps.py              Creates role groups and default weighted evaluation criteria.
core/admin.py             Lets staff manage records using Django's built-in admin page.
core/templates/core/      HTML pages shown to the user; base.html is the shared layout.
core/templatetags/         Small custom template helper for looking up saved scores.
core/migrations/           Database change history; Django applies these with migrate.
core/tests.py              Automated tests for workflow, permissions, evaluation, and guards.
CODE_GUIDE.md              A beginner-friendly map of how one request moves through files.

CHECK THE SYSTEM
----------------
Run Django's checks:
    python manage.py check

Run the automated tests:
    python manage.py test core

If you change a model field, make a database migration and apply it:
    python manage.py makemigrations core
    python manage.py migrate

FILE UPLOADS AND EMAIL
----------------------
Uploaded proposal files, revisions, approval forms, and final manuscripts go under
media/ during local development. Notification records are stored in the database.
Email uses Django's console email backend for now, so emails are printed in the terminal
rather than sent to real mailboxes.

IMPORTANT LIMITATIONS / FUTURE WORK
-----------------------------------
- Topic similarity is a simple title/area keyword hint, not AI or semantic detection.
- Program/department names are text fields; a normalized institution-wide directory is
  a sensible later enhancement if your adviser requires it.
- Local SQLite and local media storage are for development. A production deployment
  needs secure environment-based settings, a production database, private file storage,
  a real email service, backups, HTTPS, and a security review.
- A public repository access setting currently still uses the application's signed-in
  repository pages; it does not create a standalone anonymous public website.
- The system has basic reports and tables rather than every chart/format named in the
  project brief. Diagrams, complete manuals, end-to-end user acceptance, and deployment
  still need to be completed as part of your project work.
- The application should be tested with your instructor's exact policies before it is
  submitted as the final school system. Keep developing in small steps and use the tests.

BACKUP THE DEVELOPMENT DATABASE
-------------------------------
Stop the server and copy db.sqlite3 to a safe location. In PowerShell, for example:
    Copy-Item db.sqlite3 db_backup.sqlite3
Also back up the media/ folder because uploaded files are stored separately from the DB.
