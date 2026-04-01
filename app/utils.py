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

def get_or_create_role(role_name):
    role = Role.query.filter_by(name=role_name).first()
    if not role:
        role = Role(name=role_name, description=f"Auto-created role: {role_name}")
        db.session.add(role)
        db.session.flush()
        print(f"✅ Auto-created role '{role_name}'")
    return role
