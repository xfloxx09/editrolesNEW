from functools import wraps
from flask_login import current_user
from flask import flash, redirect, url_for
from app import db
from app.models import Team, Project, Role, TeamMember, User

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


def team_member_eligible_for_new_coaching(team_member):
    """True if this member may be selected for a new coaching, workshop, or assignment."""
    if not team_member or not team_member.team:
        return False
    if team_member.team.name == ARCHIV_TEAM_NAME:
        return False
    return bool(team_member.team.active_for_coaching)


def iter_relationship(coll):
    """Iterate SQLAlchemy relation: dynamic (``.all()``) or static list / InstrumentedList."""
    if coll is None:
        return
    try:
        seq = coll.all()
    except AttributeError:
        seq = coll
    for item in seq:
        yield item


def _archiv_team_id():
    t = Team.query.filter_by(name=ARCHIV_TEAM_NAME).first()
    return t.id if t else None


def _is_live_team_in_project(team, project_id, archiv_id):
    """Real project team (not ARCHIV) suitable for coaching context."""
    if not team or team.project_id != project_id:
        return False
    if team.name == ARCHIV_TEAM_NAME:
        return False
    if archiv_id is not None and team.id == archiv_id:
        return False
    if not team.active_for_coaching:
        return False
    return True


def user_eligible_assignable_coach(user, project_id, team_member_id=None):
    """
    Users who may be chosen as coach when creating an AssignedCoaching in project_id.

    Excludes ties that only exist via ARCHIV or inactive (``active_for_coaching``) teams.
    ``coach_own_team_only`` requires a selected coachee on the same live team / led team.
    """
    if not user or not user.role:
        return False
    if not user.has_permission('coach') and not user.has_permission('accept_assigned_coaching'):
        return False
    if user.role_name == ROLE_ADMIN:
        return False

    archiv_id = _archiv_team_id()
    pids = {project_id}

    def live_proj(team):
        return _is_live_team_in_project(team, project_id, archiv_id)

    linked = False
    if user.project_id in pids:
        linked = True
    for p in iter_relationship(user.projects):
        if p.id in pids:
            linked = True
            break

    for team in iter_relationship(user.teams_led):
        if live_proj(team):
            linked = True
            break

    if user.has_permission('multiple_teams'):
        for tm in user.team_members:
            if tm.team and live_proj(tm.team):
                linked = True
                break

    tid = getattr(user, 'team_id_if_leader', None)
    if tid:
        t = db.session.get(Team, tid)
        if live_proj(t):
            linked = True

    leader_of_target = False
    if team_member_id:
        tm_coachee = db.session.get(TeamMember, team_member_id)
        if (
            tm_coachee
            and tm_coachee.team
            and tm_coachee.team.project_id == project_id
            and live_proj(tm_coachee.team)
        ):
            leader_ids = {l.id for l in iter_relationship(tm_coachee.team.leaders)}
            if user.id in leader_ids:
                leader_of_target = True

    if not linked and not leader_of_target:
        return False

    # Nur noch ARCHIV-Zeilen als Mitglied: nicht als Coach anbieten, außer anderer Bezug
    tms = list(user.team_members)
    if tms and archiv_id is not None:
        only_archiv = all((tm.team_id == archiv_id) for tm in tms)
        if only_archiv:
            has_other = (
                user.project_id in pids
                or any(p.id in pids for p in iter_relationship(user.projects))
                or any(live_proj(t) for t in iter_relationship(user.teams_led))
                or leader_of_target
            )
            tid2 = getattr(user, 'team_id_if_leader', None)
            if tid2:
                t2 = db.session.get(Team, tid2)
                if live_proj(t2):
                    has_other = True
            if not has_other:
                return False

    if user.has_permission('coach_own_team_only'):
        if not team_member_id:
            return False
        tm_coachee = db.session.get(TeamMember, team_member_id)
        if not tm_coachee or not tm_coachee.team_id:
            return False
        if not live_proj(tm_coachee.team):
            return False
        allowed_team_ids = set()
        for team in iter_relationship(user.teams_led):
            if live_proj(team):
                allowed_team_ids.add(team.id)
        for tm2 in user.team_members:
            if tm2.team and live_proj(tm2.team):
                allowed_team_ids.add(tm2.team_id)
        if tm_coachee.team_id not in allowed_team_ids:
            return False

    return True


def users_for_assignment_coach_dropdown(project_id, team_member_id=None):
    """Sorted users who may receive a coaching assignment in this project."""
    eligible = [
        u for u in User.query.order_by(User.username).all()
        if user_eligible_assignable_coach(u, project_id, team_member_id)
    ]
    return eligible


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
        archiv_team = Team(
            name=ARCHIV_TEAM_NAME,
            project_id=default_project.id,
            active_for_coaching=False,
        )
        db.session.add(archiv_team)
        db.session.commit()
    elif archiv_team.active_for_coaching:
        archiv_team.active_for_coaching = False
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


def workshop_individual_rating_from_request(member_id):
    """Parse optional per-participant workshop rating 0–10; empty or invalid → None."""
    from flask import request
    raw = (request.form.get(f'individual_rating_{member_id}') or '').strip()
    if not raw:
        return None
    try:
        v = int(raw)
        if 0 <= v <= 10:
            return v
    except (ValueError, TypeError):
        pass
    return None


def get_or_create_role(role_name):
    role = Role.query.filter_by(name=role_name).first()
    if not role:
        role = Role(name=role_name, description=f"Auto-created role: {role_name}")
        db.session.add(role)
        db.session.flush()
        print(f"✅ Auto-created role '{role_name}'")
    return role
