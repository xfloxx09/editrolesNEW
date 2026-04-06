from functools import wraps
from flask_login import current_user
from flask import flash, redirect, url_for
from sqlalchemy.exc import SQLAlchemyError
from app import db
from app.models import Team, Project, Role, TeamMember, User, LeitfadenItem, CoachingThemaItem, CoachingBogenLayout

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


def leitfaden_items_for_project(project_id):
    """
    Active checklist items for new coachings in this project.
    If the project has at least one active project-specific item, use only those.
    Otherwise use the global standard (project_id IS NULL).
    """
    if project_id is None:
        try:
            return (
                LeitfadenItem.query.filter(
                    LeitfadenItem.is_active.is_(True),
                    LeitfadenItem.project_id.is_(None),
                )
                .order_by(LeitfadenItem.position, LeitfadenItem.id)
                .all()
            )
        except SQLAlchemyError:
            db.session.rollback()
            return []
    try:
        scoped = (
            LeitfadenItem.query.filter_by(is_active=True, project_id=project_id)
            .order_by(LeitfadenItem.position, LeitfadenItem.id)
            .all()
        )
        if scoped:
            return scoped
        return (
            LeitfadenItem.query.filter(
                LeitfadenItem.is_active.is_(True),
                LeitfadenItem.project_id.is_(None),
            )
            .order_by(LeitfadenItem.position, LeitfadenItem.id)
            .all()
        )
    except SQLAlchemyError:
        db.session.rollback()
        return []


def leitfaden_items_for_coaching_edit(coaching):
    """
    Items for editing a coaching: current project checklist plus any items already linked
    in saved responses (so legacy rows stay visible if the project checklist changed).
    """
    if not coaching:
        return []
    base = leitfaden_items_for_project(coaching.project_id)
    base_ids = {i.id for i in base}
    extra = []
    try:
        for r in coaching.leitfaden_responses or []:
            if r.item_id not in base_ids and r.item:
                extra.append(r.item)
    except SQLAlchemyError:
        db.session.rollback()
    extra.sort(key=lambda x: (x.position, x.id))
    return base + extra


class DefaultCoachingBogenLayout:
    """Fallback when DB has no layout row yet (before migration)."""
    show_performance_bar = True
    show_coach_notes = True
    show_time_spent = True
    allow_side_by_side = True
    allow_tcap = True


def thema_items_for_project(project_id):
    """Active coaching topic choices; same project vs. global fallback as Leitfaden."""
    if project_id is None:
        try:
            return (
                CoachingThemaItem.query.filter(
                    CoachingThemaItem.is_active.is_(True),
                    CoachingThemaItem.project_id.is_(None),
                )
                .order_by(CoachingThemaItem.position, CoachingThemaItem.id)
                .all()
            )
        except SQLAlchemyError:
            db.session.rollback()
            return []
    try:
        scoped = (
            CoachingThemaItem.query.filter_by(is_active=True, project_id=project_id)
            .order_by(CoachingThemaItem.position, CoachingThemaItem.id)
            .all()
        )
        if scoped:
            return scoped
        return (
            CoachingThemaItem.query.filter(
                CoachingThemaItem.is_active.is_(True),
                CoachingThemaItem.project_id.is_(None),
            )
            .order_by(CoachingThemaItem.position, CoachingThemaItem.id)
            .all()
        )
    except SQLAlchemyError:
        db.session.rollback()
        return []


def bogen_layout_for_project(project_id):
    """Project-specific layout row, else global row, else in-memory defaults."""
    try:
        if project_id is not None:
            row = CoachingBogenLayout.query.filter_by(project_id=project_id).first()
            if row:
                return row
        row = CoachingBogenLayout.query.filter(CoachingBogenLayout.project_id.is_(None)).first()
        if row:
            return row
    except SQLAlchemyError:
        db.session.rollback()
    return DefaultCoachingBogenLayout()


def user_is_archived_only_for_login(user):
    """
    True if this user must not log in: every linked TeamMember row sits on the ARCHIV team
    (Konto deaktiviert). Users without TeamMember rows (z. B. reine Coach-/Admin-Konten) are not blocked.
    """
    if not user:
        return False
    try:
        members = list(iter_relationship(user.team_members))
    except (TypeError, AttributeError):
        members = []
    if not members:
        return False
    archiv_team = Team.query.filter_by(name=ARCHIV_TEAM_NAME).first()
    if not archiv_team:
        return False
    aid = archiv_team.id
    return all(getattr(tm, 'team_id', None) == aid for tm in members)


def team_member_eligible_for_new_coaching(team_member):
    """True if this member may be selected for a new coaching, workshop, or assignment."""
    if not team_member or not team_member.team:
        return False
    if team_member.team.name == ARCHIV_TEAM_NAME:
        return False
    return bool(team_member.team.active_for_coaching)


def team_member_eligible_for_coaching_assignment(team_member):
    """True if this member may receive a coaching *assignment* (Coaching zuweisen), including admin-whitelisted inactive teams."""
    if not team_member or not team_member.team:
        return False
    if team_member.team.name == ARCHIV_TEAM_NAME:
        return False
    t = team_member.team
    if t.active_for_coaching:
        return True
    return bool(getattr(t, 'visible_for_coaching_assignment', False))


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


def _team_usable_for_coach_link(team, project_id, archiv_id, for_assignment=False):
    """Project team usable when linking coaches to coachees (add coaching / assignments)."""
    if not team or team.project_id != project_id:
        return False
    if team.name == ARCHIV_TEAM_NAME:
        return False
    if archiv_id is not None and team.id == archiv_id:
        return False
    if team.active_for_coaching:
        return True
    if for_assignment and getattr(team, 'visible_for_coaching_assignment', False):
        return True
    return False


def _is_live_team_in_project(team, project_id, archiv_id):
    """Real project team (not ARCHIV) with active coaching — workshops, neue Coachings, etc."""
    return _team_usable_for_coach_link(team, project_id, archiv_id, for_assignment=False)


def user_eligible_assignable_coach(user, project_id, team_member_id=None, for_assignment=False):
    """
    Users who may be chosen as coach when creating an AssignedCoaching in project_id.

    Excludes ties that only exist via ARCHIV or inactive teams, except when
    ``for_assignment`` and the coachee team is whitelisted (``visible_for_coaching_assignment``).
    ``coach_own_team_only`` requires a selected coachee on the same live team / led team.

    ``for_assignment``: coachee teams may be inactive if marked visible for assignment in admin.
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
        return _team_usable_for_coach_link(team, project_id, archiv_id, for_assignment=for_assignment)

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
        if user_eligible_assignable_coach(u, project_id, team_member_id, for_assignment=True)
    ]
    eligible.sort(key=lambda u: (u.coach_display_name or '').lower())
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


def projects_in_abteilung(abteilung_id):
    """Ordered projects linked to an Abteilung (for primary project / sync)."""
    if not abteilung_id:
        return []
    return Project.query.filter_by(abteilung_id=abteilung_id).order_by(Project.id).all()


def get_accessible_project_ids():
    """
    Projects this user may see in dashboards, filters, and URLs.

    Returns:
        None — Admin / Betriebsleiter: no restriction (all projects).
        [] — not authenticated or no projects linked (caller should handle).
        [id, ...] — explicit allow-list (primary ``User.project_id`` plus optional
        ``User.projects`` M2M; Abteilungsleiter uses ``User.projects``).
        Users with ``view_abteilung`` and ``User.abteilung_id`` also see all projects
        of that Abteilung.
    """
    if not current_user.is_authenticated:
        return []
    if current_user.role_name in (ROLE_ADMIN, ROLE_BETRIEBSLEITER):
        return None
    ids = set()
    if current_user.has_permission('view_abteilung') and current_user.abteilung_id:
        ids.update(p.id for p in projects_in_abteilung(current_user.abteilung_id))
    if current_user.role_name == ROLE_ABTEILUNGSLEITER:
        ids.update(p.id for p in current_user.projects)
        return sorted(ids)
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
