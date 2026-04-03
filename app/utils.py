from functools import wraps
from flask_login import current_user
from flask import flash, redirect, url_for
from app import db
from app.models import Team, Project, Role

ROLE_ADMIN = 'Admin'
ROLE_BETRIEBSLEITER = 'Betriebsleiter'
ROLE_PROJEKTLEITER = 'Projektleiter'
ROLE_TEAMLEITER = 'Teamleiter'
ROLE_QUALITÄTSMANAGER = 'Qualitätsmanager'
ROLE_QM = ROLE_QUALITÄTSMANAGER
ROLE_SALESCOACH = 'SalesCoach'
ROLE_TRAINER = 'Trainer'
ROLE_ABTEILUNGSLEITER = 'Abteilungsleiter'
ROLE_MITARBEITER = 'Mitarbeiter'

ARCHIV_TEAM_NAME = "ARCHIV"

def role_required(allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                flash('Bitte melden Sie sich an.', 'warning')
                return redirect(url_for('auth.login'))
            if current_user.role_name not in allowed_roles:
                flash('Sie haben keine Berechtigung für diese Seite.', 'danger')
                return redirect(url_for('main.index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def permission_required(permission_name):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                flash('Bitte melden Sie sich an.', 'warning')
                return redirect(url_for('auth.login'))
            if not current_user.has_permission(permission_name):
                flash('Sie haben keine Berechtigung für diese Aktion.', 'danger')
                return redirect(url_for('main.index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def any_permission_required(*permission_names):
    """User must have at least one of the listed permissions."""

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                flash('Bitte melden Sie sich an.', 'warning')
                return redirect(url_for('auth.login'))
            if not any(current_user.has_permission(name) for name in permission_names):
                flash('Sie haben keine Berechtigung für diese Aktion.', 'danger')
                return redirect(url_for('main.index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def get_or_create_archiv_team():
    archiv_team = Team.query.filter_by(name=ARCHIV_TEAM_NAME).first()
    if not archiv_team:
        default_project = Project.query.first()
        if not default_project:
            default_project = Project(name="Default Project")
            db.session.add(default_project)
            db.session.commit()
        archiv_team = Team(name=ARCHIV_TEAM_NAME, project_id=default_project.id)
        db.session.add(archiv_team)
        db.session.commit()
    return archiv_team

def has_permission(user, permission_name):
    if not user or not user.role:
        return False
    return user.role.has_permission(permission_name)


def get_accessible_project_ids():
    """
    Projects this user may see in dashboards, filters, and URLs.

    Returns:
        None — Admin / Betriebsleiter: no restriction (all projects).
        [] — not authenticated or no projects linked (caller should handle).
        [id, ...] — explicit allow-list (primary ``User.project_id`` plus optional
        ``User.projects`` M2M; Abteilungsleiter uses only ``User.projects``).
    """
    if not current_user.is_authenticated:
        return []
    if current_user.role_name in (ROLE_ADMIN, ROLE_BETRIEBSLEITER):
        return None
    if current_user.role_name == ROLE_ABTEILUNGSLEITER:
        return sorted({p.id for p in current_user.projects})
    ids = set()
    if current_user.project_id:
        ids.add(current_user.project_id)
    for p in current_user.projects:
        ids.add(p.id)
    return sorted(ids)


def user_has_mein_team_nav(user):
    """Show 'Mein Team' if view_own_team and user is member of at least one non-ARCHIV team."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if not user.has_permission('view_own_team'):
        return False
    archiv = get_or_create_archiv_team()
    aid = archiv.id
    for tm in user.team_members:
        if tm.team_id and tm.team_id != aid:
            return True
    return False

def get_or_create_role(role_name):
    role = Role.query.filter_by(name=role_name).first()
    if not role:
        role = Role(name=role_name, description=f"Auto-created role: {role_name}")
        db.session.add(role)
        db.session.flush()
        print(f"✅ Auto-created role '{role_name}'")
    return role
