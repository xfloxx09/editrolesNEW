# app/utils.py
from functools import wraps
from flask_login import current_user
from flask import abort, flash, redirect, url_for
from app import db
from app.models import Team
from app.constants import ARCHIV_TEAM_NAME

# Role constants (must match the names in the roles table)
ROLE_ADMIN = 'Admin'
ROLE_BETRIEBSLEITER = 'Betriebsleiter'
ROLE_PROJEKTLEITER = 'Projektleiter'
ROLE_ABTEILUNGSLEITER = 'Abteilungsleiter'
ROLE_TEAMLEITER = 'Teamleiter'
ROLE_QM = 'Qualitätsmanager'
ROLE_SALESCOACH = 'SalesCoach'
ROLE_TRAINER = 'Trainer'

def role_required(role_name_or_list):
    """
    Decorator to require that the current user has at least one of the specified roles.
    Accepts a single role name string or a list of role names.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            user_role_name = current_user.role_name
            if not user_role_name:
                flash("Ihre Rolle konnte nicht ermittelt werden.", "danger")
                abort(403)
            required_roles = []
            if isinstance(role_name_or_list, str):
                required_roles.append(role_name_or_list)
            elif isinstance(role_name_or_list, list):
                required_roles = role_name_or_list
            else:
                return abort(500)
            if user_role_name not in required_roles:
                print(f"DEBUG: user role = {user_role_name}, required = {required_roles}")
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def permission_required(permission_name):
    """Decorator to check if current user has a specific permission."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            print(f"DEBUG: permission_required checking '{permission_name}' for user {current_user.username} (role {current_user.role_name})")
            if not current_user.has_permission(permission_name):
                print(f"DEBUG: permission denied for '{permission_name}'")
                abort(403)
            print(f"DEBUG: permission granted for '{permission_name}'")
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def get_or_create_archiv_team():
    """Get or create the special ARCHIV team."""
    archiv_team = Team.query.filter_by(name=ARCHIV_TEAM_NAME).first()
    if not archiv_team:
        print(f"INFO: Erstelle das spezielle Team: {ARCHIV_TEAM_NAME}")
        archiv_team = Team(name=ARCHIV_TEAM_NAME)
        db.session.add(archiv_team)
        db.session.commit()
    return archiv_team


def user_can_access_project(user, project_id):
    """Check if user can access a given project."""
    if user.role_name in [ROLE_ADMIN, ROLE_BETRIEBSLEITER]:
        return True
    if user.role_name == ROLE_ABTEILUNGSLEITER:
        return project_id in user.get_allowed_project_ids()
    return user.project_id == project_id
