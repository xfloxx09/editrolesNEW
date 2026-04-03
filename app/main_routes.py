# app/main_routes.py
from flask import Blueprint, render_template, redirect, url_for, flash, request, session, current_app, jsonify
from flask_login import login_required, current_user
from sqlalchemy import desc, or_, false, exists
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload, selectinload, aliased
from app import db
from app.models import User, Team, TeamMember, Coaching, Workshop, workshop_participants, Project, Role, AssignedCoaching, LeitfadenItem, CoachingLeitfadenResponse, CoachingReview
from app.forms import CoachingForm, WorkshopForm, PasswordChangeForm, CoachingReviewForm, AssignedCoachingForm
from app.utils import (
    role_required,
    permission_required,
    any_permission_required,
    ROLE_ADMIN,
    ROLE_BETRIEBSLEITER,
    ROLE_PROJEKTLEITER,
    ROLE_TEAMLEITER,
    ROLE_ABTEILUNGSLEITER,
    ROLE_QM,
    ROLE_SALESCOACH,
    ROLE_TRAINER,
    get_or_create_archiv_team,
    ARCHIV_TEAM_NAME,
    get_accessible_project_ids,
    team_member_eligible_for_new_coaching,
    user_eligible_assignable_coach,
    users_for_assignment_coach_dropdown,
    workshop_individual_rating_from_request,
)
from datetime import datetime, timezone, timedelta, date
import calendar

bp = Blueprint('main', __name__)
LEITFADEN_CHOICES = {'Ja', 'Nein', 'k.A.'}


def _safe_internal_path(path_val):
    """Only allow same-app relative paths (no open redirects)."""
    if not path_val or not isinstance(path_val, str):
        return None
    s = path_val.strip()
    if not s.startswith('/') or s.startswith('//'):
        return None
    if any(c in s for c in '\n\r\t'):
        return None
    return s


def _redirect_after_coaching_review(form, my_coachings_query_args):
    target = _safe_internal_path((form.next.data or '').strip()) if getattr(form, 'next', None) else None
    if target:
        return redirect(target)
    return redirect(url_for('main.my_coachings', **my_coachings_query_args))


def get_active_leitfaden_items_safe():
    try:
        return LeitfadenItem.query.filter_by(is_active=True).order_by(LeitfadenItem.position, LeitfadenItem.id).all()
    except SQLAlchemyError:
        db.session.rollback()
        return []

# Helper to get the active project for the current user
def get_visible_project_id():
    if current_user.is_authenticated:
        if current_user.role_name in [ROLE_ADMIN, ROLE_BETRIEBSLEITER]:
            project_id = session.get('active_project')
            if project_id:
                return project_id
            first = Project.query.first()
            return first.id if first else None
        elif current_user.role_name == ROLE_ABTEILUNGSLEITER:
            project_id = session.get('active_project')
            allowed = [p.id for p in current_user.projects]
            if project_id and project_id in allowed:
                return project_id
            first = current_user.projects.first()
            return first.id if first else None
        allowed = get_accessible_project_ids()
        if not allowed:
            return current_user.project_id
        if len(allowed) == 1:
            return allowed[0]
        project_id = session.get('active_project')
        if project_id and project_id in allowed:
            return project_id
        if current_user.project_id and current_user.project_id in allowed:
            return current_user.project_id
        return allowed[0]
    return None


def _apply_query_project_to_session():
    """If ?project=<id> is present and allowed, persist to session (same rules as set_project)."""
    pid = request.args.get('project', type=int)
    if pid is None:
        return
    project = Project.query.get(pid)
    if not project:
        return
    if current_user.role_name in [ROLE_ADMIN, ROLE_BETRIEBSLEITER]:
        session['active_project'] = pid
        session.modified = True
        return
    if current_user.role_name == ROLE_ABTEILUNGSLEITER and project in current_user.projects:
        session['active_project'] = pid
        session.modified = True
        return
    allowed = get_accessible_project_ids()
    if allowed and pid in allowed:
        session['active_project'] = pid
        session.modified = True


def _projects_for_coaching_workshop_picker():
    """Projects the user may target when adding a coaching or workshop."""
    accessible = get_accessible_project_ids()
    if accessible is None:
        return Project.query.order_by(Project.name).all()
    if not accessible:
        return []
    return Project.query.filter(Project.id.in_(accessible)).order_by(Project.name).all()


def _resolve_coaching_workshop_project_id():
    """
    Active project for add-coaching / add-workshop.
    Uses ?project= on GET, project_id on POST; must fall within get_accessible_project_ids() for non-admin.
    """
    accessible = get_accessible_project_ids()
    chosen = request.args.get('project', type=int)
    if request.method == 'POST':
        chosen = request.form.get('project_id', type=int) or chosen
    if accessible is None:
        if chosen and Project.query.get(chosen):
            return chosen
        return get_visible_project_id()
    if not accessible:
        return None
    if chosen and chosen in accessible:
        return chosen
    return get_visible_project_id()


def _sync_assigned_coaching_status_from_progress(assignment):
    """Mark assignment completed when expected_coaching_count is reached; reopen if count drops below (e.g. delete)."""
    if not assignment:
        return
    exp = assignment.expected_coaching_count or 0
    if exp <= 0:
        return
    st = assignment.status
    if st in ('cancelled', 'rejected', 'expired'):
        return
    done = Coaching.query.filter_by(assigned_coaching_id=assignment.id).count()
    if done >= exp:
        if st in ('pending', 'accepted', 'in_progress'):
            assignment.status = 'completed'
    elif st == 'completed':
        assignment.status = 'in_progress' if done > 0 else 'accepted'


def _user_can_assign_coachings():
    return (
        current_user.has_permission('assign_coachings')
        or current_user.has_permission('view_pl_qm_dashboard')
    )


def _member_performance_for_assigned_page(project_id):
    members = TeamMember.query.join(Team, TeamMember.team_id == Team.id).filter(
        Team.project_id == project_id,
        Team.name != ARCHIV_TEAM_NAME,
        Team.active_for_coaching.is_(True),
    ).all()
    raw = []
    for m in members:
        stats = db.session.query(
            db.func.count(Coaching.id),
            db.func.avg(Coaching.performance_mark),
            db.func.sum(Coaching.time_spent),
            db.func.max(Coaching.coaching_date),
        ).filter(
            Coaching.team_member_id == m.id,
            Coaching.project_id == project_id,
        ).first()
        cnt = int(stats[0] or 0)
        avg_m = stats[1]
        total_t = int(stats[2] or 0)
        last_d = stats[3]
        avg_score = round(float(avg_m or 0) * 10, 1) if cnt > 0 else 0.0
        raw.append({
            'member': m,
            'coaching_count': cnt,
            'avg_score': avg_score,
            'total_time': total_t,
            'last_coaching_date': last_d,
        })
    if not raw:
        return []
    max_c = max(r['coaching_count'] for r in raw) or 1
    max_t = max(r['total_time'] for r in raw) or 1
    out = []
    for r in raw:
        m = r['member']
        perf_part = float(r['avg_score'])
        cnt_part = (r['coaching_count'] / max_c) * 100.0
        time_part = (r['total_time'] / max_t) * 100.0 if max_t else 0.0
        combined = 0.4 * perf_part + 0.3 * cnt_part + 0.3 * time_part
        out.append({
            'id': m.id,
            'name': m.name,
            'team_name': m.team.name if m.team else '',
            'combined_score': combined,
            'avg_score': r['avg_score'],
            'coaching_count': r['coaching_count'],
            'total_time': r['total_time'],
            'last_coaching_date': r['last_coaching_date'],
        })
    return out


# Helper for date ranges
def calculate_date_range(period_arg):
    today = datetime.now(timezone.utc).date()
    if period_arg == 'today':
        start = datetime.combine(today, datetime.min.time())
        end = datetime.combine(today, datetime.max.time())
    elif period_arg == 'yesterday':
        yesterday = today - timedelta(days=1)
        start = datetime.combine(yesterday, datetime.min.time())
        end = datetime.combine(yesterday, datetime.max.time())
    elif period_arg == 'this_week':
        start_of_week = today - timedelta(days=today.weekday())
        start = datetime.combine(start_of_week, datetime.min.time())
        end = datetime.combine(today, datetime.max.time())
    elif period_arg == 'last_week':
        start_of_last_week = today - timedelta(days=today.weekday() + 7)
        end_of_last_week = start_of_last_week + timedelta(days=6)
        start = datetime.combine(start_of_last_week, datetime.min.time())
        end = datetime.combine(end_of_last_week, datetime.max.time())
    elif period_arg == 'this_month':
        start = datetime.combine(today.replace(day=1), datetime.min.time())
        end = datetime.combine(today, datetime.max.time())
    elif period_arg == 'last_month':
        first_of_this_month = today.replace(day=1)
        last_of_last_month = first_of_this_month - timedelta(days=1)
        first_of_last_month = last_of_last_month.replace(day=1)
        start = datetime.combine(first_of_last_month, datetime.min.time())
        end = datetime.combine(last_of_last_month, datetime.max.time())
    elif period_arg == '7days':
        start_day = today - timedelta(days=6)
        start = datetime.combine(start_day, datetime.min.time())
        end = datetime.combine(today, datetime.max.time())
    elif period_arg == '30days':
        start_day = today - timedelta(days=29)
        start = datetime.combine(start_day, datetime.min.time())
        end = datetime.combine(today, datetime.max.time())
    elif period_arg == 'current_quarter':
        q = (today.month - 1) // 3
        first_month = q * 3 + 1
        start = datetime.combine(date(today.year, first_month, 1), datetime.min.time())
        end = datetime.combine(today, datetime.max.time())
    elif period_arg == 'current_year':
        start = datetime.combine(date(today.year, 1, 1), datetime.min.time())
        end = datetime.combine(today, datetime.max.time())
    elif period_arg and len(period_arg) == 7 and period_arg[4] == '-':
        try:
            y = int(period_arg[0:4])
            mo = int(period_arg[5:7])
            last_d = calendar.monthrange(y, mo)[1]
            start = datetime.combine(date(y, mo, 1), datetime.min.time())
            end = datetime.combine(date(y, mo, last_d), datetime.max.time())
        except ValueError:
            start = None
            end = None
    else:
        start = None
        end = None
    return start, end

def get_month_name_german(month_num):
    return ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni',
            'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'][month_num-1]


def get_allowed_project_ids_for_reviews():
    """Projects a user may see when using view_all_reviews."""
    ids = get_accessible_project_ids()
    if ids is None:
        ap = session.get('active_project')
        if ap:
            return [ap]
        return [p.id for p in Project.query.order_by(Project.name).all()]
    return ids


def apply_coaching_date_filters(query, period_arg, year, month, day):
    """Preset period and/or explicit Jahr/Monat/Tag (UTC day boundaries). Query must be on Coaching."""
    if year is not None:
        try:
            if month is not None and day is not None:
                d0 = date(year, month, day)
                start = datetime.combine(d0, datetime.min.time()).replace(tzinfo=timezone.utc)
                end = datetime.combine(d0, datetime.max.time()).replace(tzinfo=timezone.utc)
            elif month is not None:
                last_d = calendar.monthrange(year, month)[1]
                start = datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc)
                end = datetime(year, month, last_d, 23, 59, 59, 999999, tzinfo=timezone.utc)
            else:
                start = datetime(year, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
                end = datetime(year, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)
            query = query.filter(Coaching.coaching_date >= start, Coaching.coaching_date <= end)
        except ValueError:
            pass
    else:
        start, end = calculate_date_range(period_arg)
        if start:
            query = query.filter(Coaching.coaching_date >= start)
        if end:
            query = query.filter(Coaching.coaching_date <= end)
    return query


def _get_teams_for_team_view():
    """Teams for /team-view: PL/QM = aktive Projektteams mit mind. einem Mitglied (ohne ARCHIV); view_own_team = eigene Teams ohne ARCHIV/inaktiv."""
    archiv = get_or_create_archiv_team()
    archiv_id = archiv.id
    has_members = exists().where(TeamMember.team_id == Team.id)
    if current_user.has_permission('view_pl_qm_dashboard'):
        project_id = get_visible_project_id()
        if not project_id:
            return []
        return Team.query.filter(
            Team.project_id == project_id,
            Team.id != archiv_id,
            Team.name != ARCHIV_TEAM_NAME,
            Team.active_for_coaching.is_(True),
            has_members,
        ).order_by(Team.name).all()
    if not current_user.has_permission('view_own_team'):
        return []
    seen = set()
    teams = []
    for tm in current_user.team_members:
        if not tm.team_id or tm.team_id == archiv_id or tm.team_id in seen:
            continue
        team = Team.query.get(tm.team_id)
        if not team or team.name == ARCHIV_TEAM_NAME or not team.active_for_coaching:
            continue
        teams.append(team)
        seen.add(team.id)
    teams.sort(key=lambda x: x.name)
    return teams


def _user_sees_all_teams_coaching_dashboard():
    if current_user.role_name in (ROLE_ADMIN, ROLE_BETRIEBSLEITER):
        return True
    return current_user.has_permission('view_coaching_dashboard_all_teams')


def _dashboard_my_team_ids():
    """Team IDs where the user has a TeamMember row (Mein Team basis), excluding ARCHIV."""
    archiv = get_or_create_archiv_team()
    archiv_id = archiv.id
    seen = set()
    out = []
    for tm in current_user.team_members:
        if not tm.team_id or tm.team_id == archiv_id or tm.team_id in seen:
            continue
        team = tm.team
        if team and team.name != ARCHIV_TEAM_NAME:
            out.append(tm.team_id)
            seen.add(tm.team_id)
    return out


def _coaching_dashboard_query_joined(base_query):
    """Join path required whenever filters reference TeamMember, Team, or coach User."""
    return base_query.join(
        TeamMember, Coaching.team_member_id == TeamMember.id
    ).join(
        Team, TeamMember.team_id == Team.id
    ).outerjoin(
        User, Coaching.coach_id == User.id
    )


def _build_team_members_performance(team):
    project_id = team.project_id
    team_members_performance = []
    for member in TeamMember.query.filter_by(team_id=team.id).order_by(TeamMember.name).all():
        m_stats = db.session.query(
            db.func.count(Coaching.id),
            db.func.avg(Coaching.performance_mark),
            db.func.sum(Coaching.time_spent)
        ).filter(Coaching.team_member_id == member.id, Coaching.project_id == project_id).first()
        total_c = m_stats[0] or 0
        avg_perf = round((m_stats[1] or 0) * 10, 1) if total_c > 0 else 0
        total_t = m_stats[2] or 0
        hours = total_t // 60
        mins = total_t % 60
        formatted_time = f"{hours}h {mins}m" if hours > 0 else f"{mins}m"

        if total_c > 0:
            member_coachings = Coaching.query.filter_by(team_member_id=member.id, project_id=project_id).all()
            total_checks = 0
            positive_checks = 0
            for c in member_coachings:
                for _, val in c.leitfaden_fields_list:
                    if val and val != 'k.A.':
                        total_checks += 1
                        if str(val).lower() in ['ja', 'yes', '1', 'true']:
                            positive_checks += 1
            avg_leitfaden = round((positive_checks / total_checks * 100), 1) if total_checks > 0 else 0
        else:
            avg_leitfaden = 0

        team_members_performance.append({
            'id': member.id,
            'name': member.name,
            'total_coachings': total_c,
            'avg_score': avg_perf,
            'total_time': total_t,
            'formatted_total_coaching_time': formatted_time,
            'avg_leitfaden_adherence': avg_leitfaden
        })
    return team_members_performance


def _team_leaders_for_team_card(team):
    """Auf der Karte als Teamleiter: im Team als Mitglied zugeordnet ``TeamMember.user_id`` und Berechtigung view_own_team."""
    users = (
        User.query.options(
            joinedload(User.role).joinedload(Role.permissions),
            selectinload(User.team_members),
        )
        .join(TeamMember, TeamMember.user_id == User.id)
        .filter(TeamMember.team_id == team.id, TeamMember.user_id.isnot(None))
        .distinct()
        .all()
    )
    eligible = [u for u in users if u.has_permission('view_own_team')]
    return sorted(eligible, key=lambda u: (u.coach_display_name or u.username or '').lower())


def filter_reviews_by_coaching_date(query, period_arg, year, month, day):
    """CoachingReview query already joined to Coaching; filter on coaching_date."""
    if year is not None:
        try:
            if month is not None and day is not None:
                d0 = date(year, month, day)
                start = datetime.combine(d0, datetime.min.time()).replace(tzinfo=timezone.utc)
                end = datetime.combine(d0, datetime.max.time()).replace(tzinfo=timezone.utc)
            elif month is not None:
                last_d = calendar.monthrange(year, month)[1]
                start = datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc)
                end = datetime(year, month, last_d, 23, 59, 59, 999999, tzinfo=timezone.utc)
            else:
                start = datetime(year, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
                end = datetime(year, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)
            query = query.filter(Coaching.coaching_date >= start, Coaching.coaching_date <= end)
        except ValueError:
            pass
    else:
        start, end = calculate_date_range(period_arg)
        if start:
            query = query.filter(Coaching.coaching_date >= start)
        if end:
            query = query.filter(Coaching.coaching_date <= end)
    return query


def my_coachings_filter_query_args():
    """Preserve filters when redirecting after POST."""
    d = {}
    for key in ('period', 'year', 'month', 'day'):
        v = request.args.get(key)
        if v is not None and v != '':
            d[key] = v
    return d


def build_filter_args(period_arg, year, month, day, extra=None):
    args = {'period': period_arg}
    if year is not None:
        args['year'] = year
    if month is not None:
        args['month'] = month
    if day is not None:
        args['day'] = day
    if extra:
        args.update(extra)
    return args


def url_for_paginated(endpoint, page, filter_args):
    kw = dict(filter_args)
    kw['page'] = page
    return url_for(endpoint, **kw)


@bp.route('/')
@login_required
def index():
    u = current_user
    index_tile_count = sum([
        1 if u.has_permission('view_coaching_dashboard') else 0,
        1 if u.has_permission('view_workshop_dashboard') else 0,
        1 if (
            u.has_permission('view_assigned_coachings')
            or u.has_permission('assign_coachings')
            or u.has_permission('view_pl_qm_dashboard')
        ) else 0,
        1 if (u.has_permission('view_own_coachings') or u.has_permission('leave_coaching_review')) else 0,
        1 if u.has_permission('view_review') else 0,
        1 if u.has_permission('view_all_reviews') else 0,
    ])
    return render_template(
        'main/index_choice.html',
        config=current_app.config,
        index_tile_count=index_tile_count,
    )


@bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    form = PasswordChangeForm()
    if form.validate_on_submit():
        if current_user.check_password(form.old_password.data):
            current_user.set_password(form.new_password.data)
            db.session.commit()
            flash('Passwort erfolgreich geändert.', 'success')
            return redirect(url_for('main.profile'))
        else:
            flash('Aktuelles Passwort ist falsch.', 'danger')
    return render_template('main/profile.html', form=form, config=current_app.config)


# --- Coaching Dashboard (your main dashboard) ---
@bp.route('/coaching-dashboard')
@login_required
@permission_required('view_coaching_dashboard')
def coaching_dashboard():
    page = request.args.get('page', 1, type=int)
    period_arg = request.args.get('period', '30days')
    team_arg = request.args.get('team', 'all')
    search_arg = request.args.get('search', default='', type=str).strip()
    project_raw = (request.args.get('project') or '').strip()
    project_filter_int = None
    project_scope_all = False
    if project_raw.lower() == 'all':
        project_scope_all = True
    elif project_raw.isdigit():
        project_filter_int = int(project_raw)

    accessible = get_accessible_project_ids()
    sees_all_teams = _user_sees_all_teams_coaching_dashboard()
    my_dash_team_ids = _dashboard_my_team_ids() if not sees_all_teams else []

    # Scope filters: Projekt + Zeitraum + Team-Dropdown. KPI-Karten zählen inkl. ARCHIV; Grafiken & Coaching-Liste ohne ARCHIV-Coachees.
    scope_filters = []
    if accessible is None:
        if project_filter_int is not None:
            scope_filters.append(Coaching.project_id == project_filter_int)
    elif not accessible:
        scope_filters.append(Coaching.project_id == -1)
    else:
        if project_filter_int is not None and project_filter_int not in accessible:
            project_filter_int = None
        if project_scope_all:
            scope_filters.append(Coaching.project_id.in_(accessible))
        elif project_filter_int is not None:
            scope_filters.append(Coaching.project_id == project_filter_int)
        elif len(accessible) == 1:
            scope_filters.append(Coaching.project_id == accessible[0])
        else:
            vid = get_visible_project_id()
            if vid and vid in accessible:
                scope_filters.append(Coaching.project_id == vid)
            else:
                scope_filters.append(Coaching.project_id == accessible[0])

    if accessible is None:
        dashboard_project_id = project_filter_int
    elif not accessible:
        dashboard_project_id = -1
    else:
        if project_scope_all:
            dashboard_project_id = None
        elif project_filter_int is not None:
            dashboard_project_id = project_filter_int
        elif len(accessible) == 1:
            dashboard_project_id = accessible[0]
        else:
            vid = get_visible_project_id()
            dashboard_project_id = vid if (vid and vid in accessible) else accessible[0]

    start_date, end_date = calculate_date_range(period_arg)
    if start_date:
        scope_filters.append(Coaching.coaching_date >= start_date)
    if end_date:
        scope_filters.append(Coaching.coaching_date <= end_date)

    if team_arg != 'all' and team_arg.isdigit():
        tid = int(team_arg)
        team_row = Team.query.filter_by(id=tid).first()
        if (
            team_row
            and team_row.name != ARCHIV_TEAM_NAME
            and team_row.active_for_coaching
            and dashboard_project_id != -1
            and (accessible is None or team_row.project_id in accessible)
            and (dashboard_project_id is None or team_row.project_id == dashboard_project_id)
        ):
            scope_filters.append(Team.id == tid)

    archiv_team = get_or_create_archiv_team()
    graph_filters = scope_filters + [TeamMember.team_id != archiv_team.id]

    list_filters = list(scope_filters)
    list_filters.append(TeamMember.team_id != archiv_team.id)

    if search_arg:
        pattern = f"%{search_arg}%"
        list_filters.append(
            or_(
                TeamMember.name.ilike(pattern),
                User.username.ilike(pattern),
                Coaching.coaching_subject.ilike(pattern),
                Coaching.coach_notes.ilike(pattern),
            )
        )

    if not sees_all_teams:
        if my_dash_team_ids:
            list_filters.append(TeamMember.team_id.in_(my_dash_team_ids))
        else:
            list_filters.append(false())

    list_query = _coaching_dashboard_query_joined(
        Coaching.query.options(
            joinedload(Coaching.employee_review),
            selectinload(Coaching.coach).selectinload(User.team_members),
        )
    ).filter(*list_filters)

    coachings_paginated = list_query.order_by(desc(Coaching.coaching_date)).paginate(page=page, per_page=15, error_out=False)

    can_leave_review = current_user.has_permission('leave_coaching_review')
    review_form_dashboard = None
    review_redirect_next = ''
    if can_leave_review:
        qv = request.query_string.decode()
        review_redirect_next = request.path + (('?' + qv) if qv else '')
        review_form_dashboard = CoachingReviewForm()
        review_form_dashboard.next.data = review_redirect_next

    total_coachings = list_query.count()

    teams_for_charts = (
        db.session.query(Team.id, Team.name)
        .join(TeamMember, Team.id == TeamMember.team_id)
        .join(Coaching, TeamMember.id == Coaching.team_member_id)
        .outerjoin(User, Coaching.coach_id == User.id)
        .filter(*graph_filters)
        .distinct()
        .all()
    )
    chart_labels = [t.name for t in teams_for_charts]
    chart_avg_performance = []
    chart_total_time = []
    chart_coachings_count = []
    for team in teams_for_charts:
        team_filters = [TeamMember.team_id == team.id] + graph_filters
        stats = (
            db.session.query(
                db.func.avg(Coaching.performance_mark),
                db.func.sum(Coaching.time_spent),
                db.func.count(Coaching.id),
            )
            .select_from(Coaching)
            .join(TeamMember, Coaching.team_member_id == TeamMember.id)
            .join(Team, TeamMember.team_id == Team.id)
            .outerjoin(User, Coaching.coach_id == User.id)
            .filter(*team_filters)
            .first()
        )
        chart_avg_performance.append(round((stats[0] or 0) * 10, 1))
        chart_total_time.append(stats[1] or 0)
        chart_coachings_count.append(stats[2] or 0)

    subject_counts = (
        db.session.query(Coaching.coaching_subject, db.func.count(Coaching.id))
        .select_from(Coaching)
        .join(TeamMember, Coaching.team_member_id == TeamMember.id)
        .join(Team, TeamMember.team_id == Team.id)
        .outerjoin(User, Coaching.coach_id == User.id)
        .filter(*graph_filters)
        .group_by(Coaching.coaching_subject)
        .all()
    )
    subject_chart_labels = [s[0] or 'Unbekannt' for s in subject_counts]
    subject_chart_values = [s[1] for s in subject_counts]

    global_stats = (
        db.session.query(db.func.count(Coaching.id), db.func.sum(Coaching.time_spent))
        .select_from(Coaching)
        .join(TeamMember, Coaching.team_member_id == TeamMember.id)
        .join(Team, TeamMember.team_id == Team.id)
        .outerjoin(User, Coaching.coach_id == User.id)
        .filter(*scope_filters)
        .first()
    )
    global_total_coachings_count = global_stats[0] or 0
    total_minutes = global_stats[1] or 0
    hours = total_minutes // 60
    minutes = total_minutes % 60
    global_time_coached_display = f"{hours} Std. {minutes} Min. ({total_minutes} Min. gesamt)"
    
    # Team-Dropdown: „sichtbare“ Projektteams (nicht ARCHIV, aktiv, mindestens ein TeamMember — leere Teams ausblenden).
    team_dropdown_q = Team.query.filter(
        Team.name != ARCHIV_TEAM_NAME,
        Team.active_for_coaching.is_(True),
        exists().where(TeamMember.team_id == Team.id),
    )
    if dashboard_project_id is not None and dashboard_project_id != -1:
        all_teams_for_filter = team_dropdown_q.filter(Team.project_id == dashboard_project_id).order_by(Team.name).all()
    elif dashboard_project_id == -1:
        all_teams_for_filter = []
    elif accessible is not None and project_scope_all:
        all_teams_for_filter = team_dropdown_q.filter(Team.project_id.in_(accessible)).order_by(Team.name).all()
    else:
        all_teams_for_filter = team_dropdown_q.order_by(Team.name).all()

    # Month options
    now = datetime.now(timezone.utc)
    current_year = now.year
    previous_year = current_year - 1
    month_options = []
    for m in range(12, 0, -1):
        month_options.append({'value': f"{previous_year}-{m:02d}", 'text': f"{get_month_name_german(m)} {previous_year}"})
    for m in range(now.month, 0, -1):
        month_options.append({'value': f"{current_year}-{m:02d}", 'text': f"{get_month_name_german(m)} {current_year}"})
    
    # Project filter dropdown: all (admin) or only accessible
    if accessible is None:
        all_projects = Project.query.order_by(Project.name).all()
    elif len(accessible) > 1:
        all_projects = Project.query.filter(Project.id.in_(accessible)).order_by(Project.name).all()
    else:
        all_projects = []

    show_global_all_projects_option = accessible is None or (accessible is not None and len(accessible) > 1)
    coaching_dashboard_project_all_is_blank = accessible is None

    if accessible is None:
        current_project_filter = project_filter_int
    elif not accessible:
        current_project_filter = None
    else:
        if project_scope_all:
            current_project_filter = 'all'
        elif project_filter_int is not None:
            current_project_filter = project_filter_int
        elif len(accessible) == 1:
            current_project_filter = accessible[0]
        else:
            current_project_filter = dashboard_project_id

    coaching_dashboard_url_project = None
    if all_projects:
        if accessible is None:
            coaching_dashboard_url_project = project_filter_int
        elif not accessible:
            coaching_dashboard_url_project = None
        else:
            if project_scope_all:
                coaching_dashboard_url_project = 'all'
            elif project_filter_int is not None:
                coaching_dashboard_url_project = project_filter_int
            else:
                coaching_dashboard_url_project = dashboard_project_id

    return render_template('main/index.html',
                           title='Coaching Dashboard',
                           coachings_paginated=coachings_paginated,
                           total_coachings=total_coachings,
                           chart_labels=chart_labels,
                           chart_avg_performance_mark_percentage=chart_avg_performance,
                           chart_total_time_spent=chart_total_time,
                           chart_coachings_done=chart_coachings_count,
                           subject_chart_labels=subject_chart_labels,
                           subject_chart_values=subject_chart_values,
                           global_total_coachings_count=global_total_coachings_count,
                           global_time_coached_display=global_time_coached_display,
                           all_teams_for_filter=all_teams_for_filter,
                           all_projects=all_projects,
                           current_period_filter=period_arg,
                           current_team_id_filter=team_arg,
                           current_project_filter=current_project_filter,
                           show_global_all_projects_option=show_global_all_projects_option,
                           coaching_dashboard_project_all_is_blank=coaching_dashboard_project_all_is_blank,
                           coaching_dashboard_url_project=coaching_dashboard_url_project,
                           current_search_term=search_arg,
                           month_options=month_options,
                           can_leave_review=can_leave_review,
                           review_form_dashboard=review_form_dashboard,
                           review_redirect_next=review_redirect_next,
                           config=current_app.config)


@bp.route('/my-coachings')
@login_required
@any_permission_required('view_own_coachings', 'leave_coaching_review')
def my_coachings():
    page = request.args.get('page', 1, type=int)
    period_arg = request.args.get('period', 'all')
    year = request.args.get('year', type=int)
    month = request.args.get('month', type=int)
    day = request.args.get('day', type=int)

    query = Coaching.query.options(
        joinedload(Coaching.employee_review),
        selectinload(Coaching.coach).selectinload(User.team_members),
    ).join(TeamMember, Coaching.team_member_id == TeamMember.id).filter(
        TeamMember.user_id == current_user.id
    )
    query = apply_coaching_date_filters(query, period_arg, year, month, day)
    coachings = query.order_by(desc(Coaching.coaching_date)).paginate(page=page, per_page=15, error_out=False)

    now = datetime.now(timezone.utc)
    year_options = list(range(now.year, now.year - 6, -1))
    month_options_list = [{'value': m, 'text': get_month_name_german(m)} for m in range(1, 13)]
    day_options = list(range(1, 32))

    review_form = CoachingReviewForm()
    filter_args = build_filter_args(period_arg, year, month, day)
    can_leave_review = current_user.has_permission('leave_coaching_review')
    has_team_member_link = (
        db.session.query(TeamMember.id).filter(TeamMember.user_id == current_user.id).first()
        is not None
    )
    return render_template(
        'main/my_coachings.html',
        title='Meine Coachings',
        coachings=coachings,
        current_period=period_arg,
        filter_year=year,
        filter_month=month,
        filter_day=day,
        year_options=year_options,
        month_options_list=month_options_list,
        day_options=day_options,
        filter_args=filter_args,
        page_url=lambda p: url_for_paginated('main.my_coachings', p, filter_args),
        review_form=review_form,
        can_leave_review=can_leave_review,
        has_team_member_link=has_team_member_link,
        config=current_app.config
    )


@bp.route('/my-coachings/review', methods=['POST'])
@login_required
@permission_required('leave_coaching_review')
def submit_coaching_review():
    form = CoachingReviewForm()
    cid_raw = (request.form.get('review_coaching_pk') or '').strip()
    if not cid_raw:
        flash('Coaching konnte nicht zugeordnet werden. Bitte „Bewertung abgeben“ erneut anklicken.', 'danger')
        t = _safe_internal_path((request.form.get('next') or '').strip())
        if t:
            return redirect(t)
        return redirect(url_for('main.my_coachings', **my_coachings_filter_query_args()))

    if not form.validate_on_submit():
        for _field, errors in form.errors.items():
            for err in errors:
                flash(err, 'danger')
        t = _safe_internal_path((request.form.get('next') or '').strip())
        if t:
            return redirect(t)
        return redirect(url_for('main.my_coachings', **my_coachings_filter_query_args()))

    try:
        cid = int(cid_raw)
    except (TypeError, ValueError):
        flash('Ungültige Coaching-ID.', 'danger')
        return _redirect_after_coaching_review(form, my_coachings_filter_query_args())

    coaching = Coaching.query.get_or_404(cid)
    member = coaching.team_member
    if not member or member.user_id != current_user.id:
        flash('Keine Berechtigung für dieses Coaching.', 'danger')
        return _redirect_after_coaching_review(form, my_coachings_filter_query_args())

    existing = CoachingReview.query.filter_by(coaching_id=coaching.id).first()
    if existing:
        flash('Ihre Bewertung wurde bereits abgegeben und kann nicht mehr geändert werden.', 'warning')
        return _redirect_after_coaching_review(form, my_coachings_filter_query_args())

    db.session.add(CoachingReview(
        coaching_id=coaching.id,
        reviewer_user_id=current_user.id,
        rating=form.rating.data,
        comment=(form.comment.data or '').strip() or None,
        visible_to_coach=bool(form.visible_to_coach.data),
        visible_to_manager=bool(form.visible_to_manager.data),
    ))
    db.session.commit()
    flash('Vielen Dank! Ihre Bewertung wurde gespeichert.', 'success')
    return _redirect_after_coaching_review(form, my_coachings_filter_query_args())


@bp.route('/reviews/for-me')
@login_required
@permission_required('view_review')
def coach_received_reviews():
    page = request.args.get('page', 1, type=int)
    period_arg = request.args.get('period', 'all')
    year = request.args.get('year', type=int)
    month = request.args.get('month', type=int)
    day = request.args.get('day', type=int)

    query = CoachingReview.query.join(Coaching, CoachingReview.coaching_id == Coaching.id).filter(
        Coaching.coach_id == current_user.id
    ).filter(CoachingReview.visible_to_coach.is_(True))
    query = filter_reviews_by_coaching_date(query, period_arg, year, month, day)
    reviews = query.order_by(desc(CoachingReview.created_at)).paginate(page=page, per_page=20, error_out=False)

    now = datetime.now(timezone.utc)
    year_options = list(range(now.year, now.year - 6, -1))
    month_options_list = [{'value': m, 'text': get_month_name_german(m)} for m in range(1, 13)]
    day_options = list(range(1, 32))

    filter_args = build_filter_args(period_arg, year, month, day)
    return render_template(
        'main/coach_received_reviews.html',
        title='Bewertungen über mich',
        reviews=reviews,
        current_period=period_arg,
        filter_year=year,
        filter_month=month,
        filter_day=day,
        year_options=year_options,
        month_options_list=month_options_list,
        day_options=day_options,
        filter_args=filter_args,
        page_url=lambda p: url_for_paginated('main.coach_received_reviews', p, filter_args),
        config=current_app.config
    )


@bp.route('/reviews/all')
@login_required
@permission_required('view_all_reviews')
def all_coaching_reviews():
    project_ids = get_allowed_project_ids_for_reviews()
    if not project_ids:
        flash('Kein Projekt für die Bewertungsübersicht verfügbar.', 'warning')
        return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    period_arg = request.args.get('period', 'all')
    year = request.args.get('year', type=int)
    month = request.args.get('month', type=int)
    day = request.args.get('day', type=int)
    project_filter = request.args.get('project', type=int)
    if project_filter and project_filter not in project_ids:
        project_filter = None

    team_filter = request.args.get('team', type=int)
    coach_filter = request.args.get('coach', type=int)

    if team_filter:
        t = Team.query.filter_by(id=team_filter).first()
        if not t or t.project_id not in project_ids:
            team_filter = None
        elif project_filter and t.project_id != project_filter:
            team_filter = None

    if coach_filter:
        cq_exists = Coaching.query.filter(
            Coaching.coach_id == coach_filter,
            Coaching.project_id.in_(project_ids),
        )
        if project_filter:
            cq_exists = cq_exists.filter(Coaching.project_id == project_filter)
        if not cq_exists.first():
            coach_filter = None

    q = CoachingReview.query.join(Coaching, CoachingReview.coaching_id == Coaching.id).filter(
        Coaching.project_id.in_(project_ids)
    ).filter(CoachingReview.visible_to_manager.is_(True))
    if project_filter:
        q = q.filter(Coaching.project_id == project_filter)
    if team_filter:
        q = q.join(TeamMember, Coaching.team_member_id == TeamMember.id).filter(
            TeamMember.team_id == team_filter
        )
    if coach_filter:
        q = q.filter(Coaching.coach_id == coach_filter)
    q = filter_reviews_by_coaching_date(q, period_arg, year, month, day)
    reviews = q.order_by(desc(CoachingReview.created_at)).paginate(page=page, per_page=25, error_out=False)

    now = datetime.now(timezone.utc)
    year_options = list(range(now.year, now.year - 6, -1))
    month_options_list = [{'value': m, 'text': get_month_name_german(m)} for m in range(1, 13)]
    day_options = list(range(1, 32))
    all_projects = Project.query.filter(Project.id.in_(project_ids)).order_by(Project.name).all()

    team_project_scope = [project_filter] if project_filter else project_ids
    filter_teams = (
        Team.query.filter(Team.project_id.in_(team_project_scope), Team.name != ARCHIV_TEAM_NAME)
        .order_by(Team.name)
        .all()
    )

    coach_q = (
        db.session.query(User)
        .options(selectinload(User.team_members))
        .join(Coaching, Coaching.coach_id == User.id)
        .filter(Coaching.project_id.in_(project_ids), Coaching.coach_id.isnot(None))
    )
    if project_filter:
        coach_q = coach_q.filter(Coaching.project_id == project_filter)
    filter_coaches = coach_q.distinct().order_by(User.username).all()

    extra_filters = {}
    if project_filter:
        extra_filters['project'] = project_filter
    if team_filter:
        extra_filters['team'] = team_filter
    if coach_filter:
        extra_filters['coach'] = coach_filter
    filter_args = build_filter_args(period_arg, year, month, day, extra=extra_filters)
    return render_template(
        'main/all_coaching_reviews.html',
        title='Alle Bewertungen',
        reviews=reviews,
        current_period=period_arg,
        filter_year=year,
        filter_month=month,
        filter_day=day,
        filter_project=project_filter,
        filter_team=team_filter,
        filter_coach=coach_filter,
        filter_teams=filter_teams,
        filter_coaches=filter_coaches,
        year_options=year_options,
        month_options_list=month_options_list,
        day_options=day_options,
        filter_projects=all_projects,
        filter_args=filter_args,
        page_url=lambda p: url_for_paginated('main.all_coaching_reviews', p, filter_args),
        config=current_app.config
    )


# --- Add Coaching (with the permission restriction only) ---
@bp.route('/add-coaching', methods=['GET', 'POST'])
@login_required
@permission_required('add_coaching')
def add_coaching():
    coaching_projects = _projects_for_coaching_workshop_picker()
    project_id = _resolve_coaching_workshop_project_id()
    if not project_id:
        flash('Kein Projekt ausgewählt oder zugeordnet.', 'danger')
        return redirect(url_for('main.index'))
    accessible = get_accessible_project_ids()
    if accessible is not None and project_id not in accessible:
        flash('Ungültiges oder nicht freigegebenes Projekt.', 'danger')
        return redirect(url_for('main.add_coaching'))
    if accessible is None and not Project.query.get(project_id):
        flash('Ungültiges Projekt.', 'danger')
        return redirect(url_for('main.add_coaching'))

    show_coaching_project_picker = len(coaching_projects) > 1

    current_user_role = current_user.role_name
    current_user_team_ids = (
        sorted({tm.team_id for tm in current_user.team_members if tm.team_id})
        if current_user_role == ROLE_TEAMLEITER else []
    )
    form = CoachingForm(current_user_role=current_user_role, current_user_team_ids=current_user_team_ids)
    form.update_team_member_choices(exclude_archiv=True, project_id=project_id)
    leitfaden_items = get_active_leitfaden_items_safe()

    if form.validate_on_submit():
        team_member = TeamMember.query.get(form.team_member_id.data)
        if not team_member:
            flash('Teammitglied nicht gefunden.', 'danger')
            return redirect(url_for('main.add_coaching', project=project_id))
        if not team_member.team or team_member.team.project_id != project_id:
            flash('Teammitglied passt nicht zum gewählten Projekt.', 'danger')
            return redirect(url_for('main.add_coaching', project=project_id))
        if not team_member_eligible_for_new_coaching(team_member):
            flash('Dieses Team ist für neue Coachings deaktiviert. Wählen Sie ein anderes Teammitglied.', 'danger')
            return redirect(url_for('main.add_coaching', project=project_id))

        coaching = Coaching(
            team_member_id=form.team_member_id.data,
            coach_id=current_user.id,
            coaching_style=form.coaching_style.data,
            tcap_id=form.tcap_id.data if form.coaching_style.data == 'TCAP' else None,
            coaching_subject=form.coaching_subject.data,
            leitfaden_begruessung=form.leitfaden_begruessung.data,
            leitfaden_legitimation=form.leitfaden_legitimation.data,
            leitfaden_pka=form.leitfaden_pka.data,
            leitfaden_kek=form.leitfaden_kek.data,
            leitfaden_angebot=form.leitfaden_angebot.data,
            leitfaden_zusammenfassung=form.leitfaden_zusammenfassung.data,
            leitfaden_kzb=form.leitfaden_kzb.data,
            performance_mark=form.performance_mark.data,
            time_spent=form.time_spent.data,
            coach_notes=form.coach_notes.data,
            project_id=project_id,
            team_id=team_member.team_id
        )
        linked_assignment = None
        if form.assigned_coaching_id.data and form.assigned_coaching_id.data != 0:
            coaching.assigned_coaching_id = form.assigned_coaching_id.data
            linked_assignment = AssignedCoaching.query.get(form.assigned_coaching_id.data)
            if linked_assignment:
                linked_assignment.status = 'in_progress'

        db.session.add(coaching)
        db.session.flush()

        for item in leitfaden_items:
            selected_value = request.form.get(f'leitfaden_item_{item.id}', 'k.A.')
            value = selected_value if selected_value in LEITFADEN_CHOICES else 'k.A.'
            db.session.add(CoachingLeitfadenResponse(
                coaching_id=coaching.id,
                item_id=item.id,
                value=value
            ))
        if linked_assignment:
            _sync_assigned_coaching_status_from_progress(linked_assignment)
        db.session.commit()
        flash('Coaching erfolgreich gespeichert!', 'success')
        return redirect(url_for('main.coaching_dashboard'))

    assigned_id = request.args.get('assigned_id', type=int)
    if assigned_id:
        assignment = AssignedCoaching.query.get(assigned_id)
        if assignment and assignment.coach_id == current_user.id and assignment.status == 'pending':
            tm_a = TeamMember.query.get(assignment.team_member_id)
            if not team_member_eligible_for_new_coaching(tm_a):
                flash('Diese Aufgabe kann nicht angenommen werden: Das Team ist für neue Coachings deaktiviert.', 'danger')
            else:
                form.assigned_coaching_id.data = assigned_id
                form.team_member_id.data = assignment.team_member_id
                if assignment.desired_performance_note:
                    form.performance_mark.data = assignment.desired_performance_note
                assignment.status = 'accepted'
                db.session.commit()
                flash('Coaching-Aufgabe angenommen.', 'success')
        else:
            flash('Ungültige oder nicht verfügbare Aufgabe.', 'danger')

    return render_template(
        'main/add_coaching.html',
        form=form,
        leitfaden_items=leitfaden_items,
        selected_leitfaden_values={},
        coaching_projects=coaching_projects,
        selected_coaching_project_id=project_id,
        show_coaching_project_picker=show_coaching_project_picker,
        config=current_app.config
    )


# --- Edit Coaching ---
@bp.route('/edit-coaching/<int:coaching_id>', methods=['GET', 'POST'])
@login_required
@permission_required('edit_coaching')
def edit_coaching(coaching_id):
    coaching = Coaching.query.get_or_404(coaching_id)
    if current_user.role_name not in [ROLE_ADMIN, ROLE_BETRIEBSLEITER] and coaching.coach_id != current_user.id:
        flash('Sie haben keine Berechtigung, dieses Coaching zu bearbeiten.', 'danger')
        return redirect(url_for('main.coaching_dashboard'))

    cut = (
        sorted({tm.team_id for tm in current_user.team_members if tm.team_id})
        if current_user.role_name == ROLE_TEAMLEITER else []
    )
    form = CoachingForm(obj=coaching, current_user_role=current_user.role_name, current_user_team_ids=cut)
    form.update_team_member_choices(
        exclude_archiv=True,
        project_id=coaching.project_id,
        include_member_ids=[coaching.team_member_id],
    )
    leitfaden_items = get_active_leitfaden_items_safe()
    selected_leitfaden_values = {}
    if leitfaden_items:
        try:
            selected_leitfaden_values = {response.item_id: response.value for response in coaching.leitfaden_responses}
        except SQLAlchemyError:
            db.session.rollback()
            selected_leitfaden_values = {}

    if form.validate_on_submit():
        tm_new = TeamMember.query.get(form.team_member_id.data)
        if not tm_new or not team_member_eligible_for_new_coaching(tm_new):
            flash('Ungültiges Teammitglied oder Team für neue Coachings deaktiviert.', 'danger')
            return redirect(url_for('main.edit_coaching', coaching_id=coaching_id))
        prev_assigned_id = coaching.assigned_coaching_id
        form.populate_obj(coaching)
        if form.coaching_style.data != 'TCAP':
            coaching.tcap_id = None
        if leitfaden_items:
            CoachingLeitfadenResponse.query.filter_by(coaching_id=coaching.id).delete()
            for item in leitfaden_items:
                selected_value = request.form.get(f'leitfaden_item_{item.id}', 'k.A.')
                value = selected_value if selected_value in LEITFADEN_CHOICES else 'k.A.'
                db.session.add(CoachingLeitfadenResponse(
                    coaching_id=coaching.id,
                    item_id=item.id,
                    value=value
                ))
        db.session.flush()
        for aid in {a for a in (prev_assigned_id, coaching.assigned_coaching_id) if a}:
            _sync_assigned_coaching_status_from_progress(AssignedCoaching.query.get(aid))
        db.session.commit()
        flash('Coaching erfolgreich aktualisiert.', 'success')
        return redirect(url_for('main.coaching_dashboard'))

    return render_template(
        'main/add_coaching.html',
        form=form,
        is_edit_mode=True,
        coaching=coaching,
        leitfaden_items=leitfaden_items,
        selected_leitfaden_values=selected_leitfaden_values,
        config=current_app.config
    )


# --- Delete Coaching ---
@bp.route('/delete-coaching/<int:coaching_id>', methods=['POST'])
@login_required
@permission_required('edit_coaching')
def delete_coaching(coaching_id):
    coaching = Coaching.query.get_or_404(coaching_id)
    if current_user.role_name not in [ROLE_ADMIN, ROLE_BETRIEBSLEITER] and coaching.coach_id != current_user.id:
        flash('Keine Berechtigung.', 'danger')
        return redirect(url_for('main.coaching_dashboard'))
    assigned_ref = coaching.assigned_coaching_id
    db.session.delete(coaching)
    db.session.flush()
    if assigned_ref:
        _sync_assigned_coaching_status_from_progress(AssignedCoaching.query.get(assigned_ref))
    db.session.commit()
    flash('Coaching gelöscht.', 'success')
    return redirect(url_for('main.coaching_dashboard'))


# --- Workshop routes (keep as you had) ---
@bp.route('/add-workshop', methods=['GET', 'POST'])
@login_required
@permission_required('add_workshop')
def add_workshop():
    workshop_projects = _projects_for_coaching_workshop_picker()
    project_id = _resolve_coaching_workshop_project_id()
    if not project_id:
        flash('Kein Projekt ausgewählt.', 'danger')
        return redirect(url_for('main.index'))
    accessible = get_accessible_project_ids()
    if accessible is not None and project_id not in accessible:
        flash('Ungültiges oder nicht freigegebenes Projekt.', 'danger')
        return redirect(url_for('main.add_workshop'))
    if accessible is None and not Project.query.get(project_id):
        flash('Ungültiges Projekt.', 'danger')
        return redirect(url_for('main.add_workshop'))

    show_workshop_project_picker = len(workshop_projects) > 1

    current_user_team_ids = (
        sorted({tm.team_id for tm in current_user.team_members if tm.team_id})
        if current_user.role_name == ROLE_TEAMLEITER else []
    )
    form = WorkshopForm(current_user_role=current_user.role_name, current_user_team_ids=current_user_team_ids)
    form.update_participant_choices(project_id=project_id)
    if form.validate_on_submit():
        for member_id in form.team_member_ids.data:
            wm = TeamMember.query.get(member_id)
            if not wm or not wm.team or wm.team.project_id != project_id:
                flash('Mindestens ein Teilnehmer gehört nicht zum gewählten Projekt.', 'danger')
                return redirect(url_for('main.add_workshop', project=project_id))
            if not team_member_eligible_for_new_coaching(wm):
                flash('Mindestens ein Teilnehmer gehört zu einem Team, das für neue Workshops deaktiviert ist.', 'danger')
                return redirect(url_for('main.add_workshop'))
        workshop = Workshop(
            title=form.title.data,
            coach_id=current_user.id,
            overall_rating=form.overall_rating.data,
            time_spent=form.time_spent.data,
            notes=form.notes.data,
            project_id=project_id
        )
        db.session.add(workshop)
        db.session.flush()
        for member_id in form.team_member_ids.data:
            individual_rating = workshop_individual_rating_from_request(member_id)
            stmt = workshop_participants.insert().values(
                workshop_id=workshop.id,
                team_member_id=member_id,
                individual_rating=individual_rating,
                original_team_id=None
            )
            db.session.execute(stmt)
        db.session.commit()
        flash('Workshop erfolgreich gespeichert.', 'success')
        return redirect(url_for('main.workshop_dashboard'))
    return render_template(
        'main/add_workshop.html',
        form=form,
        workshop_projects=workshop_projects,
        selected_workshop_project_id=project_id,
        show_workshop_project_picker=show_workshop_project_picker,
        config=current_app.config,
    )


@bp.route('/workshop-dashboard')
@login_required
@permission_required('view_workshop_dashboard')
def workshop_dashboard():
    page = request.args.get('page', 1, type=int)
    period_arg = request.args.get('period', 'all')
    search_arg = request.args.get('search', default="", type=str).strip()
    project_filter = get_visible_project_id()

    # Build reusable filter conditions
    ws_filters = []
    if project_filter:
        ws_filters.append(Workshop.project_id == project_filter)
    start_date, end_date = calculate_date_range(period_arg)
    if start_date:
        ws_filters.append(Workshop.workshop_date >= start_date)
    if end_date:
        ws_filters.append(Workshop.workshop_date <= end_date)

    workshops_query = Workshop.query
    if search_arg:
        pattern = f"%{search_arg}%"
        ws_filters.append(
            or_(
                Workshop.title.ilike(pattern),
                Workshop.notes.ilike(pattern),
                User.username.ilike(pattern)
            )
        )
        workshops_query = workshops_query.join(User, Workshop.coach_id == User.id)

    workshops_query = workshops_query.filter(*ws_filters)
    workshops_paginated = workshops_query.order_by(desc(Workshop.workshop_date)).paginate(page=page, per_page=15, error_out=False)

    # Compute stats for the template
    total_workshops = workshops_query.count()
    total_time = db.session.query(
        db.func.coalesce(db.func.sum(Workshop.time_spent), 0)
    ).filter(*ws_filters).scalar()
    avg_rating_val = db.session.query(
        db.func.avg(Workshop.overall_rating)
    ).filter(*ws_filters).scalar()
    avg_rating = round(avg_rating_val, 1) if avg_rating_val else 0

    # Month options for filter dropdown
    now = datetime.now(timezone.utc)
    current_year = now.year
    previous_year = current_year - 1
    month_options = []
    for m in range(12, 0, -1):
        month_options.append({'value': f"{previous_year}-{m:02d}", 'text': f"{get_month_name_german(m)} {previous_year}"})
    for m in range(now.month, 0, -1):
        month_options.append({'value': f"{current_year}-{m:02d}", 'text': f"{get_month_name_german(m)} {current_year}"})

    return render_template('main/workshop_dashboard.html',
                           title='Workshop Dashboard',
                           workshops_paginated=workshops_paginated,
                           total_workshops=total_workshops,
                           total_time=total_time,
                           avg_rating=avg_rating,
                           current_search=search_arg,
                           current_period_filter=period_arg,
                           month_options=month_options,
                           config=current_app.config,
                           db=db,
                           workshop_participants=workshop_participants)


# --- Team View (team leaders + members with view_own_team; PL/QM via view_pl_qm_dashboard) ---
@bp.route('/team-view')
@login_required
@any_permission_required('view_own_team', 'view_pl_qm_dashboard')
def team_view():
    all_teams_list = _get_teams_for_team_view()
    if not all_teams_list:
        flash('Kein Team für diese Ansicht verfügbar. Prüfen Sie die Berechtigung und die Zuordnung (Teamleiter-Teams oder Teammitglied).', 'info')
        return redirect(url_for('main.index'))

    requested_id = request.args.get('team_id', type=int)
    team = None
    if requested_id:
        team = next((t for t in all_teams_list if t.id == requested_id), None)
        if not team:
            flash('Kein Zugriff auf das angeforderte Team.', 'warning')
    if not team:
        team = all_teams_list[0]

    team_members_performance = _build_team_members_performance(team)
    member_ids = [m.id for m in TeamMember.query.filter_by(team_id=team.id).all()]
    team_coachings = []
    if member_ids:
        team_coachings = Coaching.query.options(
            joinedload(Coaching.coach),
            joinedload(Coaching.team_member_coached),
        ).filter(
            Coaching.team_member_id.in_(member_ids),
            Coaching.project_id == team.project_id,
        ).order_by(desc(Coaching.coaching_date)).limit(10).all()

    members = TeamMember.query.filter_by(team_id=team.id).order_by(TeamMember.name).all()
    team_leaders_display = _team_leaders_for_team_card(team)
    return render_template(
        'main/team_view.html',
        title='Mein Team',
        team=team,
        members=members,
        team_leaders_display=team_leaders_display,
        team_members_performance=team_members_performance,
        team_coachings=team_coachings,
        all_teams_list=all_teams_list,
        config=current_app.config,
    )


# --- PL/QM Dashboard ---
@bp.route('/pl-qm-dashboard')
@login_required
@permission_required('view_pl_qm_dashboard')
def pl_qm_dashboard():
    _apply_query_project_to_session()
    project_id = get_visible_project_id()
    if not project_id:
        flash('Kein Projekt ausgewählt.', 'danger')
        return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    selected_team_id_filter = request.args.get('team_id_filter', default='', type=str)

    project = Project.query.get(project_id)
    all_teams = (
        Team.query.filter_by(project_id=project_id)
        .filter(
            Team.name != ARCHIV_TEAM_NAME,
            Team.active_for_coaching.is_(True),
            exists().where(TeamMember.team_id == Team.id),
        )
        .order_by(Team.name)
        .all()
    )
    allowed_pl_qm_team_ids = {t.id for t in all_teams}

    # Compute per-team stats
    teams_stats = []
    for team in all_teams:
        stats = db.session.query(
            db.func.count(Coaching.id),
            db.func.avg(Coaching.performance_mark),
            db.func.sum(Coaching.time_spent)
        ).join(TeamMember, Coaching.team_member_id == TeamMember.id).filter(
            TeamMember.team_id == team.id,
            Coaching.project_id == project_id
        ).first()
        num_coachings = stats[0] or 0
        avg_score = round((stats[1] or 0) * 10, 1)
        total_time = stats[2] or 0
        teams_stats.append({
            'id': team.id,
            'name': team.name,
            'num_coachings': num_coachings,
            'avg_score': avg_score,
            'total_time': total_time
        })

    # Overall stats
    overall = db.session.query(
        db.func.count(Coaching.id),
        db.func.sum(Coaching.time_spent),
        db.func.avg(Coaching.performance_mark)
    ).filter(Coaching.project_id == project_id).first()
    total_coachings_overall = overall[0] or 0
    total_time_overall = overall[1] or 0
    avg_score_overall = round((overall[2] or 0) * 10, 1)

    # Chart data
    chart_labels = [t['name'] for t in teams_stats if t['num_coachings'] > 0]
    chart_avg_performance_values = [t['avg_score'] for t in teams_stats if t['num_coachings'] > 0]

    subject_counts = db.session.query(
        Coaching.coaching_subject, db.func.count(Coaching.id)
    ).filter(Coaching.project_id == project_id).group_by(Coaching.coaching_subject).all()
    subject_labels = [s[0] or 'Unbekannt' for s in subject_counts]
    subject_values = [s[1] for s in subject_counts]

    # Top 3 and Flop 3 teams
    teams_with_coachings = [t for t in teams_stats if t['num_coachings'] > 0]
    sorted_by_score = sorted(teams_with_coachings, key=lambda x: x['avg_score'], reverse=True)
    top_3_teams = sorted_by_score[:3]
    flop_3_teams = sorted_by_score[-3:][::-1] if len(sorted_by_score) > 3 else []

    # Member cards for selected team
    selected_team_object_for_cards = None
    members_data_for_cards = []
    if selected_team_id_filter and selected_team_id_filter.isdigit():
        selected_team_object_for_cards = Team.query.get(int(selected_team_id_filter))
        if not selected_team_object_for_cards or selected_team_object_for_cards.id not in allowed_pl_qm_team_ids:
            selected_team_object_for_cards = None
        if selected_team_object_for_cards:
            team_members = TeamMember.query.filter_by(team_id=selected_team_object_for_cards.id).order_by(TeamMember.name).all()
            for member in team_members:
                m_stats = db.session.query(
                    db.func.count(Coaching.id),
                    db.func.avg(Coaching.performance_mark),
                    db.func.sum(Coaching.time_spent)
                ).filter(Coaching.team_member_id == member.id, Coaching.project_id == project_id).first()
                total_c = m_stats[0] or 0
                avg_perf = round((m_stats[1] or 0) * 10, 1) if total_c > 0 else 0
                total_t = m_stats[2] or 0
                hours = total_t // 60
                mins = total_t % 60
                formatted_time = f"{hours}h {mins}m" if hours > 0 else f"{mins}m"

                if total_c > 0:
                    member_coachings = Coaching.query.filter_by(team_member_id=member.id, project_id=project_id).all()
                    total_checks = 0
                    positive_checks = 0
                    for c in member_coachings:
                        for _, val in c.leitfaden_fields_list:
                            if val and val != 'k.A.':
                                total_checks += 1
                                if val.lower() in ['ja', 'yes', '1', 'true']:
                                    positive_checks += 1
                    avg_leitfaden = round((positive_checks / total_checks * 100), 1) if total_checks > 0 else 0
                else:
                    avg_leitfaden = 0

                members_data_for_cards.append({
                    'id': member.id,
                    'name': member.name,
                    'total_coachings': total_c,
                    'avg_score': avg_perf,
                    'total_time': total_t,
                    'formatted_total_coaching_time': formatted_time,
                    'avg_leitfaden_adherence': avg_leitfaden
                })

    coachings_paginated = Coaching.query.filter_by(project_id=project_id).order_by(
        desc(Coaching.coaching_date)
    ).paginate(page=page, per_page=15, error_out=False)

    return render_template('main/projektleiter_dashboard.html',
                           title='Teams',
                           project_bar_endpoint='main.pl_qm_dashboard',
                           project_bar_extra_hidden={},
                           project=project,
                           total_coachings_overall=total_coachings_overall,
                           total_time_overall=total_time_overall,
                           avg_score_overall=avg_score_overall,
                           teams_stats=teams_stats,
                           chart_labels=chart_labels,
                           chart_avg_performance_values=chart_avg_performance_values,
                           subject_labels=subject_labels,
                           subject_values=subject_values,
                           all_teams_for_filter=all_teams,
                           selected_team_id_filter=selected_team_id_filter,
                           selected_team_object_for_cards=selected_team_object_for_cards,
                           members_data_for_cards=members_data_for_cards,
                           coachings_paginated=coachings_paginated,
                           top_3_teams=top_3_teams,
                           flop_3_teams=flop_3_teams,
                           config=current_app.config)


@bp.route('/api/available_assignments')
@login_required
@permission_required('add_coaching')
def available_assignments():
    """Offene/aktive zugewiesene Aufgaben für Coach + gewähltes Teammitglied (Coaching-Formular)."""
    member_id = request.args.get('member_id', type=int)
    if not member_id:
        return jsonify({'assignments': []})

    ensure_raw = (request.args.get('ensure_assignment_ids') or '').strip()
    ensure_ids = []
    for part in ensure_raw.split(','):
        part = part.strip()
        if part.isdigit():
            ensure_ids.append(int(part))

    base = AssignedCoaching.query.filter(
        AssignedCoaching.team_member_id == member_id,
        AssignedCoaching.coach_id == current_user.id,
        AssignedCoaching.status.in_(['pending', 'accepted', 'in_progress']),
    ).order_by(AssignedCoaching.deadline)

    seen = set()
    out = []
    for a in base.all():
        seen.add(a.id)
        out.append({
            'id': a.id,
            'deadline': a.deadline.strftime('%d.%m.%y') if a.deadline else '',
            'progress': a.progress,
        })

    for eid in ensure_ids:
        if eid in seen:
            continue
        a = AssignedCoaching.query.get(eid)
        if (
            a
            and a.team_member_id == member_id
            and a.coach_id == current_user.id
        ):
            seen.add(a.id)
            out.append({
                'id': a.id,
                'deadline': a.deadline.strftime('%d.%m.%y') if a.deadline else '',
                'progress': a.progress,
            })

    return jsonify({'assignments': out})


@bp.route('/api/member-coaching-trend')
@login_required
@any_permission_required('view_pl_qm_dashboard', 'view_own_team')
def get_member_coaching_trend():
    team_member_id = request.args.get('team_member_id', type=int)
    count = request.args.get('count', default='10', type=str)
    if not team_member_id:
        return jsonify({'labels': [], 'scores': [], 'dates': []})

    tm_row = TeamMember.query.get(team_member_id)
    if not tm_row:
        return jsonify({'labels': [], 'scores': [], 'dates': []})
    allowed_team_ids = {t.id for t in _get_teams_for_team_view()}
    if tm_row.team_id not in allowed_team_ids:
        return jsonify({'labels': [], 'scores': [], 'dates': []})

    query = Coaching.query.filter_by(team_member_id=team_member_id).order_by(desc(Coaching.coaching_date))
    if count != 'all':
        try:
            query = query.limit(int(count))
        except (ValueError, TypeError):
            query = query.limit(10)
    coachings = query.all()
    coachings.reverse()  # oldest first for chart

    labels = [f"Coaching #{i+1}" for i in range(len(coachings))]
    scores = [(c.performance_mark or 0) * 10 for c in coachings]
    dates = [c.coaching_date.strftime('%d.%m.%Y') if c.coaching_date else '' for c in coachings]

    return jsonify({'labels': labels, 'scores': scores, 'dates': dates})


# --- Project selection ---
@bp.route('/set-project/<int:project_id>')
@login_required
def set_project(project_id):
    project = Project.query.get_or_404(project_id)
    if current_user.role_name in [ROLE_ADMIN, ROLE_BETRIEBSLEITER]:
        session['active_project'] = project_id
    elif current_user.role_name == ROLE_ABTEILUNGSLEITER and project in current_user.projects:
        session['active_project'] = project_id
    else:
        allowed = get_accessible_project_ids()
        if allowed and project_id in allowed:
            session['active_project'] = project_id
        else:
            flash('Sie haben keine Berechtigung für dieses Projekt.', 'danger')
            return redirect(url_for('main.index'))
    flash(f'Projekt gewechselt zu {project.name}.', 'success')
    return redirect(request.referrer or url_for('main.index'))


# --- Assigned Coachings (Coach-Ansicht + PL/Zuweiser-Ansicht) ---
@bp.route('/assigned-coachings')
@login_required
@any_permission_required('view_assigned_coachings', 'view_pl_qm_dashboard', 'assign_coachings')
def assigned_coachings():
    _apply_query_project_to_session()
    project_id = get_visible_project_id()
    if not project_id:
        flash('Kein Projekt ausgewählt.', 'danger')
        return redirect(url_for('main.index'))

    can_assign = _user_can_assign_coachings()
    can_coach_list = current_user.has_permission('view_assigned_coachings')
    if not can_assign and not can_coach_list:
        flash('Keine Berechtigung.', 'danger')
        return redirect(url_for('main.index'))

    view_type = 'pl' if can_assign else 'coach'

    tab_active = request.args.get('status', 'current')
    if tab_active not in ('current', 'completed'):
        tab_active = 'current'

    page = request.args.get('page', 1, type=int)
    team_filter = request.args.get('team', type=int)
    coach_filter = request.args.get('coach', type=int)
    member_filter = request.args.get('member', type=int)
    search_term = (request.args.get('search') or '').strip()
    sort_by = request.args.get('sort_by', 'deadline')
    sort_dir = request.args.get('sort_dir', 'asc')
    if sort_dir not in ('asc', 'desc'):
        sort_dir = 'asc'

    q = AssignedCoaching.query.options(
        joinedload(AssignedCoaching.team_member).joinedload(TeamMember.team),
        joinedload(AssignedCoaching.coach),
    ).join(TeamMember, AssignedCoaching.team_member_id == TeamMember.id).join(
        Team, TeamMember.team_id == Team.id
    ).filter(Team.project_id == project_id)

    if view_type == 'pl':
        q = q.filter(AssignedCoaching.project_leader_id == current_user.id)
    else:
        q = q.filter(AssignedCoaching.coach_id == current_user.id)

    if tab_active == 'completed':
        q = q.filter(AssignedCoaching.status.in_(['completed', 'expired', 'rejected', 'cancelled']))
    else:
        q = q.filter(AssignedCoaching.status.in_(['pending', 'accepted', 'in_progress']))

    if team_filter:
        q = q.filter(TeamMember.team_id == team_filter)
    if coach_filter:
        q = q.filter(AssignedCoaching.coach_id == coach_filter)
    if member_filter:
        q = q.filter(AssignedCoaching.team_member_id == member_filter)
    if search_term:
        st = f'%{search_term}%'
        q = q.filter(or_(
            AssignedCoaching.coach.has(User.username.ilike(st)),
            TeamMember.name.ilike(st),
        ))

    CoachAlias = aliased(User)
    if sort_by == 'coach_name':
        q = q.join(CoachAlias, AssignedCoaching.coach_id == CoachAlias.id)
        order_expr = CoachAlias.username
    elif sort_by == 'member_name':
        order_expr = TeamMember.name
    else:
        order_expr = AssignedCoaching.deadline

    if sort_dir == 'desc':
        q = q.order_by(desc(order_expr))
    else:
        q = q.order_by(order_expr)

    assignments = q.paginate(page=page, per_page=15, error_out=False)

    all_teams = Team.query.filter_by(project_id=project_id).filter(
        Team.name != ARCHIV_TEAM_NAME
    ).order_by(Team.name).all()

    coach_id_rows = db.session.query(AssignedCoaching.coach_id).join(
        TeamMember, AssignedCoaching.team_member_id == TeamMember.id
    ).join(Team, TeamMember.team_id == Team.id).filter(
        Team.project_id == project_id
    ).distinct().all()
    coach_id_list = [r[0] for r in coach_id_rows if r[0]]
    all_coaches = (
        list(User.query.filter(User.id.in_(coach_id_list)).all())
        if coach_id_list else []
    )
    all_coaches.sort(key=lambda u: (u.coach_display_name or '').lower())

    all_members = TeamMember.query.join(Team, TeamMember.team_id == Team.id).filter(
        Team.project_id == project_id,
        Team.name != ARCHIV_TEAM_NAME,
        Team.active_for_coaching.is_(True),
    ).order_by(Team.name, TeamMember.name).all()

    member_performance = _member_performance_for_assigned_page(project_id) if view_type == 'pl' else []

    project_bar_extra_hidden = {'status': tab_active}
    if team_filter:
        project_bar_extra_hidden['team'] = team_filter
    if coach_filter:
        project_bar_extra_hidden['coach'] = coach_filter
    if member_filter:
        project_bar_extra_hidden['member'] = member_filter
    if search_term:
        project_bar_extra_hidden['search'] = search_term
    if sort_by != 'deadline':
        project_bar_extra_hidden['sort_by'] = sort_by
    if sort_dir != 'asc':
        project_bar_extra_hidden['sort_dir'] = sort_dir

    return render_template(
        'main/assigned_coachings.html',
        assignments=assignments,
        project_bar_endpoint='main.assigned_coachings',
        project_bar_extra_hidden=project_bar_extra_hidden,
        status_filter=tab_active,
        tab_active=tab_active,
        view_type=view_type,
        team_filter=team_filter,
        coach_filter=coach_filter,
        member_filter=member_filter,
        search_term=search_term,
        sort_by=sort_by,
        sort_dir=sort_dir,
        all_teams=all_teams,
        all_coaches=all_coaches,
        all_members=all_members,
        member_performance=member_performance,
        config=current_app.config,
    )


@bp.route('/create-assigned-coaching', methods=['GET', 'POST'])
@login_required
@any_permission_required('assign_coachings', 'view_pl_qm_dashboard')
def create_assigned_coaching():
    _apply_query_project_to_session()
    project_id = get_visible_project_id()
    if not project_id:
        flash('Kein Projekt ausgewählt.', 'danger')
        return redirect(url_for('main.index'))

    tm_for_coaches = request.args.get('member_id', type=int)
    if request.method == 'POST':
        posted_m = request.form.get('team_member_id', type=int)
        if posted_m:
            tm_for_coaches = posted_m

    form = AssignedCoachingForm(allowed_project_ids=[project_id], team_member_id=tm_for_coaches)
    if request.method == 'GET' and tm_for_coaches:
        form.team_member_id.data = tm_for_coaches

    if form.validate_on_submit():
        coach_u = User.query.get(form.coach_id.data)
        if not coach_u or not user_eligible_assignable_coach(
            coach_u, project_id, form.team_member_id.data
        ):
            flash('Ungültige Coach-Auswahl.', 'danger')
            return redirect(url_for('main.create_assigned_coaching', project=project_id))
        tm_as = TeamMember.query.get(form.team_member_id.data)
        if not team_member_eligible_for_new_coaching(tm_as):
            flash('Dieses Teammitglied gehört zu einem Team, das für neue Zuweisungen deaktiviert ist.', 'danger')
            return redirect(url_for('main.create_assigned_coaching', project=project_id))
        d = form.deadline.data
        dl = datetime(d.year, d.month, d.day, 23, 59, 59)
        note_raw = request.form.get('current_note')
        try:
            cur_note = float(note_raw) if note_raw else None
        except (TypeError, ValueError):
            cur_note = None
        assignment = AssignedCoaching(
            project_leader_id=current_user.id,
            coach_id=form.coach_id.data,
            team_member_id=form.team_member_id.data,
            deadline=dl,
            expected_coaching_count=form.expected_coaching_count.data,
            desired_performance_note=form.desired_performance_note.data,
            current_performance_note_at_assign=cur_note,
            status='pending',
        )
        db.session.add(assignment)
        db.session.commit()
        flash('Coaching-Aufgabe zugewiesen.', 'success')
        return redirect(url_for('main.assigned_coachings', project=project_id))

    return render_template('main/create_assigned_coaching.html', form=form, config=current_app.config)


@bp.route('/api/assignment-coaches')
@login_required
@any_permission_required('assign_coachings', 'view_pl_qm_dashboard')
def api_assignment_coaches():
    """Coach dropdown options for the current project; refined by selected team member (optional)."""
    project_id = get_visible_project_id()
    if not project_id:
        return jsonify([])
    mid = request.args.get('team_member_id', type=int)
    if mid:
        m = TeamMember.query.get(mid)
        if not m or not m.team or m.team.project_id != project_id:
            mid = None
    coaches = users_for_assignment_coach_dropdown(project_id, mid)
    return jsonify([
        {'id': u.id, 'label': f"{u.coach_display_name} ({u.role_name})"}
        for u in coaches
    ])


@bp.route('/api/member-current-score')
@login_required
@any_permission_required('assign_coachings', 'view_pl_qm_dashboard')
def get_member_current_score():
    mid = request.args.get('member_id', type=int)
    if not mid:
        return jsonify({'score': 0})
    project_id = get_visible_project_id()
    m = TeamMember.query.get(mid)
    if not m or not m.team or m.team.project_id != project_id:
        return jsonify({'score': 0})
    avg = db.session.query(db.func.avg(Coaching.performance_mark)).filter(
        Coaching.team_member_id == mid,
        Coaching.project_id == project_id,
    ).scalar()
    score = round(float(avg or 0) * 10, 1) if avg is not None else 0.0
    return jsonify({'score': score})


@bp.route('/assigned-coaching-report/<int:assignment_id>')
@login_required
@any_permission_required('assign_coachings', 'view_pl_qm_dashboard')
def assigned_coaching_report(assignment_id):
    project_id = get_visible_project_id()
    if not project_id:
        flash('Kein Projekt ausgewählt.', 'danger')
        return redirect(url_for('main.index'))

    assignment = AssignedCoaching.query.options(
        joinedload(AssignedCoaching.team_member).joinedload(TeamMember.team),
        joinedload(AssignedCoaching.coach),
    ).get_or_404(assignment_id)

    if assignment.team_member.team.project_id != project_id:
        flash('Aufgabe gehört nicht zum aktiven Projekt.', 'danger')
        return redirect(url_for('main.assigned_coachings', project=project_id))
    if assignment.project_leader_id != current_user.id:
        flash('Nur die zuweisende Person kann den Bericht einsehen.', 'danger')
        return redirect(url_for('main.assigned_coachings', project=project_id))

    done_list = Coaching.query.options(joinedload(Coaching.coach)).filter(
        Coaching.assigned_coaching_id == assignment.id
    ).order_by(Coaching.coaching_date).all()

    coachings_done = len(done_list)
    if done_list:
        final_avg = sum(c.overall_score for c in done_list) / len(done_list)
    else:
        final_avg = 0.0

    report = {
        'assignment': assignment,
        'coachings': done_list,
        'coachings_expected': assignment.expected_coaching_count,
        'coachings_done': coachings_done,
        'start_note': assignment.current_performance_note_at_assign,
        'target_note': assignment.desired_performance_note,
        'final_avg_score': final_avg,
        'status': assignment.status,
    }
    return render_template(
        'main/assigned_coaching_report.html',
        report=report,
        assigned_report_project_id=project_id,
        config=current_app.config,
    )


@bp.route('/cancel-assigned-coaching/<int:assignment_id>', methods=['POST'])
@login_required
@any_permission_required('assign_coachings', 'view_pl_qm_dashboard')
def cancel_assigned_coaching(assignment_id):
    assignment = AssignedCoaching.query.get_or_404(assignment_id)
    tm = TeamMember.query.options(joinedload(TeamMember.team)).get(assignment.team_member_id)
    list_pid = tm.team.project_id if tm and tm.team else get_visible_project_id()
    if assignment.project_leader_id != current_user.id:
        flash('Nicht autorisiert.', 'danger')
        return redirect(url_for('main.assigned_coachings', project=list_pid))
    if assignment.status in ('pending', 'accepted', 'in_progress'):
        assignment.status = 'cancelled'
        db.session.commit()
        flash('Aufgabe storniert.', 'success')
    else:
        flash('Aufgabe kann nicht storniert werden.', 'warning')
    return redirect(url_for('main.assigned_coachings', project=list_pid))


@bp.route('/accept-assigned/<int:assignment_id>', methods=['POST'])
@login_required
@permission_required('accept_assigned_coaching')
def accept_assigned_coaching(assignment_id):
    assignment = AssignedCoaching.query.get_or_404(assignment_id)
    tm = TeamMember.query.options(joinedload(TeamMember.team)).get(assignment.team_member_id)
    list_pid = tm.team.project_id if tm and tm.team else get_visible_project_id()
    if assignment.coach_id != current_user.id:
        flash('Nicht autorisiert.', 'danger')
        return redirect(url_for('main.assigned_coachings', project=list_pid))
    if assignment.status == 'pending':
        tm_acc = TeamMember.query.get(assignment.team_member_id)
        if not team_member_eligible_for_new_coaching(tm_acc):
            flash('Annahme nicht möglich: Das Team ist für neue Coachings deaktiviert.', 'danger')
        else:
            assignment.status = 'accepted'
            db.session.commit()
            flash('Aufgabe angenommen.', 'success')
    else:
        flash('Aufgabe kann nicht angenommen werden.', 'warning')
    return redirect(url_for('main.assigned_coachings', project=list_pid))


@bp.route('/reject-assigned/<int:assignment_id>', methods=['POST'])
@login_required
@permission_required('reject_assigned_coaching')
def reject_assigned_coaching(assignment_id):
    assignment = AssignedCoaching.query.get_or_404(assignment_id)
    tm = TeamMember.query.options(joinedload(TeamMember.team)).get(assignment.team_member_id)
    list_pid = tm.team.project_id if tm and tm.team else get_visible_project_id()
    if assignment.coach_id != current_user.id:
        flash('Nicht autorisiert.', 'danger')
        return redirect(url_for('main.assigned_coachings', project=list_pid))
    if assignment.status == 'pending':
        assignment.status = 'rejected'
        db.session.commit()
        flash('Aufgabe abgelehnt.', 'success')
    else:
        flash('Aufgabe kann nicht abgelehnt werden.', 'warning')
    return redirect(url_for('main.assigned_coachings', project=list_pid))
