# app/main_routes.py
from flask import Blueprint, render_template, redirect, url_for, flash, request, session, current_app, jsonify
from flask_login import login_required, current_user
from sqlalchemy import desc, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload, selectinload
from app import db
from app.models import User, Team, TeamMember, Coaching, Workshop, workshop_participants, Project, Role, AssignedCoaching, LeitfadenItem, CoachingLeitfadenResponse, CoachingReview
from app.forms import CoachingForm, WorkshopForm, ProjectLeaderNoteForm, PasswordChangeForm, CoachingReviewForm
from app.utils import role_required, permission_required, any_permission_required, ROLE_ADMIN, ROLE_BETRIEBSLEITER, ROLE_PROJEKTLEITER, ROLE_TEAMLEITER, ROLE_ABTEILUNGSLEITER, ROLE_QM, ROLE_SALESCOACH, ROLE_TRAINER, get_or_create_archiv_team, ARCHIV_TEAM_NAME
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
            if project_id and project_id in [p.id for p in current_user.projects]:
                return project_id
            first = current_user.projects.first()
            return first.id if first else None
        else:
            return current_user.project_id
    return None

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
    else:
        start = None
        end = None
    return start, end

def get_month_name_german(month_num):
    return ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni',
            'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'][month_num-1]


def get_allowed_project_ids_for_reviews():
    """Projects a user may see when using view_all_reviews."""
    if current_user.role_name in [ROLE_ADMIN, ROLE_BETRIEBSLEITER]:
        ap = session.get('active_project')
        if ap:
            return [ap]
        return [p.id for p in Project.query.order_by(Project.name).all()]
    if current_user.role_name == ROLE_ABTEILUNGSLEITER:
        return [p.id for p in current_user.projects]
    if current_user.project_id:
        return [current_user.project_id]
    return []


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
    """Teams for /team-view: PL/QM = alle Projektteams; view_own_team = alle Teams mit TeamMember-Zeile (nicht ARCHIV)."""
    archiv = get_or_create_archiv_team()
    archiv_id = archiv.id
    if current_user.has_permission('view_pl_qm_dashboard'):
        project_id = get_visible_project_id()
        if not project_id:
            return []
        return Team.query.filter(
            Team.project_id == project_id,
            Team.id != archiv_id,
        ).order_by(Team.name).all()
    if not current_user.has_permission('view_own_team'):
        return []
    seen = set()
    teams = []
    for tm in current_user.team_members:
        if not tm.team_id or tm.team_id == archiv_id or tm.team_id in seen:
            continue
        team = Team.query.get(tm.team_id)
        if team:
            teams.append(team)
            seen.add(team.id)
    teams.sort(key=lambda x: x.name)
    return teams


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
    """Auf der Karte als Teamleiter: im Team als Mitglied zugeordnet (TeamMember.user_id) und Berechtigung view_own_team."""
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
        1 if u.has_permission('view_assigned_coachings') else 0,
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
    # Your original code (as you had it) – I'm restoring it from your template's expectations.
    # I'll use the logic that you originally had, which your `index.html` template relies on.
    # Since I don't have your exact original code, I will write a version that matches the variables your template uses.
    # Your template uses: coachings_paginated, total_coachings, chart_labels, etc.
    # I'll reconstruct the essential parts.
    
    page = request.args.get('page', 1, type=int)
    period_arg = request.args.get('period', 'all')
    team_arg = request.args.get('team', 'all')
    search_arg = request.args.get('search', default='', type=str).strip()
    project_filter = request.args.get('project', type=int)
    
    # Build reusable filter conditions
    filters = []
    if project_filter:
        filters.append(Coaching.project_id == project_filter)
    elif current_user.role_name not in [ROLE_ADMIN, ROLE_BETRIEBSLEITER]:
        filters.append(Coaching.project_id == current_user.project_id)

    # Period filter
    start_date, end_date = calculate_date_range(period_arg)
    if start_date:
        filters.append(Coaching.coaching_date >= start_date)
    if end_date:
        filters.append(Coaching.coaching_date <= end_date)

    # Team filter
    if team_arg != 'all' and team_arg.isdigit():
        filters.append(Team.id == int(team_arg))

    # Search filter
    if search_arg:
        pattern = f"%{search_arg}%"
        filters.append(
            or_(
                TeamMember.name.ilike(pattern),
                User.username.ilike(pattern),
                Coaching.coaching_subject.ilike(pattern),
                Coaching.coach_notes.ilike(pattern),
                Coaching.project_leader_notes.ilike(pattern)
            )
        )

    # Build query (eager employee_review + coach names from linked TeamMember)
    query = Coaching.query.options(
        joinedload(Coaching.employee_review),
        selectinload(Coaching.coach).selectinload(User.team_members),
    ).join(
        TeamMember, Coaching.team_member_id == TeamMember.id
    ).join(Team, TeamMember.team_id == Team.id).join(
        User, Coaching.coach_id == User.id, isouter=True
    ).filter(*filters)

    # Pagination
    coachings_paginated = query.order_by(desc(Coaching.coaching_date)).paginate(page=page, per_page=15, error_out=False)

    can_leave_review = current_user.has_permission('leave_coaching_review')
    review_form_dashboard = None
    review_redirect_next = ''
    if can_leave_review:
        qv = request.query_string.decode()
        review_redirect_next = request.path + (('?' + qv) if qv else '')
        review_form_dashboard = CoachingReviewForm()
        review_form_dashboard.next.data = review_redirect_next

    # Compute total coachings count for the filter set
    total_coachings = query.count()

    # Prepare data for charts
    teams_for_charts = db.session.query(Team.id, Team.name).join(TeamMember, Team.id == TeamMember.team_id).join(Coaching, TeamMember.id == Coaching.team_member_id).filter(*filters).distinct().all()
    chart_labels = [t.name for t in teams_for_charts]
    chart_avg_performance = []
    chart_total_time = []
    chart_coachings_count = []
    for team in teams_for_charts:
        team_filters = [TeamMember.team_id == team.id] + filters
        stats = db.session.query(db.func.avg(Coaching.performance_mark), db.func.sum(Coaching.time_spent), db.func.count(Coaching.id)).join(TeamMember, Coaching.team_member_id == TeamMember.id).filter(*team_filters).first()
        chart_avg_performance.append(round((stats[0] or 0) * 10, 1))  # percentage
        chart_total_time.append(stats[1] or 0)
        chart_coachings_count.append(stats[2] or 0)

    subject_counts = db.session.query(Coaching.coaching_subject, db.func.count(Coaching.id)).filter(*filters).group_by(Coaching.coaching_subject).all()
    subject_chart_labels = [s[0] or 'Unbekannt' for s in subject_counts]
    subject_chart_values = [s[1] for s in subject_counts]

    # Global totals
    global_stats = db.session.query(db.func.count(Coaching.id), db.func.sum(Coaching.time_spent)).filter(*filters).first()
    global_total_coachings_count = global_stats[0] or 0
    total_minutes = global_stats[1] or 0
    hours = total_minutes // 60
    minutes = total_minutes % 60
    global_time_coached_display = f"{hours} Std. {minutes} Min. ({total_minutes} Min. gesamt)"
    
    # Teams for filter dropdown
    all_teams_for_filter = Team.query.filter(Team.name != ARCHIV_TEAM_NAME).order_by(Team.name).all()
    
    # Month options
    now = datetime.now(timezone.utc)
    current_year = now.year
    previous_year = current_year - 1
    month_options = []
    for m in range(12, 0, -1):
        month_options.append({'value': f"{previous_year}-{m:02d}", 'text': f"{get_month_name_german(m)} {previous_year}"})
    for m in range(now.month, 0, -1):
        month_options.append({'value': f"{current_year}-{m:02d}", 'text': f"{get_month_name_german(m)} {current_year}"})
    
    # All projects for admin filter
    all_projects = Project.query.order_by(Project.name).all() if current_user.role_name in [ROLE_ADMIN, ROLE_BETRIEBSLEITER] else []
    
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
                           current_project_filter=project_filter,
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
    project_id = get_visible_project_id()
    if not project_id:
        flash('Kein Projekt ausgewählt oder zugeordnet.', 'danger')
        return redirect(url_for('main.index'))

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
            return redirect(url_for('main.add_coaching'))

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
        if form.assigned_coaching_id.data and form.assigned_coaching_id.data != 0:
            coaching.assigned_coaching_id = form.assigned_coaching_id.data
            assignment = AssignedCoaching.query.get(form.assigned_coaching_id.data)
            if assignment:
                assignment.status = 'in_progress'

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
        db.session.commit()
        flash('Coaching erfolgreich gespeichert!', 'success')
        return redirect(url_for('main.coaching_dashboard'))

    assigned_id = request.args.get('assigned_id', type=int)
    if assigned_id:
        assignment = AssignedCoaching.query.get(assigned_id)
        if assignment and assignment.coach_id == current_user.id and assignment.status == 'pending':
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
    form.update_team_member_choices(exclude_archiv=True, project_id=coaching.project_id)
    leitfaden_items = get_active_leitfaden_items_safe()
    selected_leitfaden_values = {}
    if leitfaden_items:
        try:
            selected_leitfaden_values = {response.item_id: response.value for response in coaching.leitfaden_responses}
        except SQLAlchemyError:
            db.session.rollback()
            selected_leitfaden_values = {}

    if form.validate_on_submit():
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
    db.session.delete(coaching)
    db.session.commit()
    flash('Coaching gelöscht.', 'success')
    return redirect(url_for('main.coaching_dashboard'))


# --- Workshop routes (keep as you had) ---
@bp.route('/add-workshop', methods=['GET', 'POST'])
@login_required
@permission_required('add_workshop')
def add_workshop():
    project_id = get_visible_project_id()
    if not project_id:
        flash('Kein Projekt ausgewählt.', 'danger')
        return redirect(url_for('main.index'))
    form = WorkshopForm(current_user_role=current_user.role_name, current_user_team_ids=[])
    form.update_participant_choices(project_id=project_id)
    if form.validate_on_submit():
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
            individual_rating = request.form.get(f'individual_rating_{member_id}', type=int)
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
    return render_template('main/add_workshop.html', form=form, config=current_app.config)


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
                           config=current_app.config)


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
@bp.route('/pl-qm-dashboard', methods=['GET', 'POST'])
@login_required
@permission_required('view_pl_qm_dashboard')
def pl_qm_dashboard():
    project_id = get_visible_project_id()
    if not project_id:
        flash('Kein Projekt ausgewählt.', 'danger')
        return redirect(url_for('main.index'))

    # Handle POST for saving project leader notes
    note_form = ProjectLeaderNoteForm()
    if request.method == 'POST' and note_form.validate_on_submit():
        coaching_id = request.form.get('coaching_id', type=int)
        if coaching_id:
            coaching = Coaching.query.get(coaching_id)
            if coaching and coaching.project_id == project_id:
                coaching.project_leader_notes = note_form.notes.data
                db.session.commit()
                flash('Notiz gespeichert.', 'success')
            else:
                flash('Coaching nicht gefunden.', 'danger')
        return redirect(request.url)

    page = request.args.get('page', 1, type=int)
    selected_team_id_filter = request.args.get('team_id_filter', default='', type=str)

    project = Project.query.get(project_id)
    all_teams = Team.query.filter_by(project_id=project_id).filter(Team.name != ARCHIV_TEAM_NAME).order_by(Team.name).all()

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

    # Paginated coachings for notes table
    coachings_paginated = Coaching.query.filter_by(project_id=project_id).order_by(
        desc(Coaching.coaching_date)
    ).paginate(page=page, per_page=15, error_out=False)

    return render_template('main/projektleiter_dashboard.html',
                           title='PL/QM Dashboard',
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
                           note_form=note_form,
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
        flash('Sie haben keine Berechtigung für dieses Projekt.', 'danger')
        return redirect(url_for('main.index'))
    flash(f'Projekt gewechselt zu {project.name}.', 'success')
    return redirect(request.referrer or url_for('main.index'))


# --- Assigned Coachings (for coaches) ---
@bp.route('/assigned-coachings')
@login_required
@permission_required('view_assigned_coachings')
def assigned_coachings():
    page = request.args.get('page', 1, type=int)
    status_filter = request.args.get('status', 'pending')
    query = AssignedCoaching.query.filter_by(coach_id=current_user.id)
    if status_filter != 'all':
        query = query.filter_by(status=status_filter)
    assignments = query.order_by(AssignedCoaching.deadline).paginate(page=page, per_page=15, error_out=False)
    return render_template('main/assigned_coachings.html', assignments=assignments, status_filter=status_filter, config=current_app.config)


@bp.route('/accept-assigned/<int:assignment_id>')
@login_required
@permission_required('accept_assigned_coaching')
def accept_assigned(assignment_id):
    assignment = AssignedCoaching.query.get_or_404(assignment_id)
    if assignment.coach_id != current_user.id:
        flash('Nicht autorisiert.', 'danger')
        return redirect(url_for('main.assigned_coachings'))
    if assignment.status == 'pending':
        assignment.status = 'accepted'
        db.session.commit()
        flash('Aufgabe angenommen.', 'success')
    else:
        flash('Aufgabe kann nicht angenommen werden.', 'warning')
    return redirect(url_for('main.assigned_coachings'))


@bp.route('/reject-assigned/<int:assignment_id>')
@login_required
@permission_required('reject_assigned_coaching')
def reject_assigned(assignment_id):
    assignment = AssignedCoaching.query.get_or_404(assignment_id)
    if assignment.coach_id != current_user.id:
        flash('Nicht autorisiert.', 'danger')
        return redirect(url_for('main.assigned_coachings'))
    if assignment.status == 'pending':
        assignment.status = 'rejected'
        db.session.commit()
        flash('Aufgabe abgelehnt.', 'success')
    else:
        flash('Aufgabe kann nicht abgelehnt werden.', 'warning')
    return redirect(url_for('main.assigned_coachings'))
