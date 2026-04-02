# app/main_routes.py
from flask import Blueprint, render_template, redirect, url_for, flash, request, session, current_app
from flask_login import login_required, current_user
from sqlalchemy import desc, or_
from app import db
from app.models import User, Team, TeamMember, Coaching, Workshop, workshop_participants, Project, Role, AssignedCoaching
from app.forms import CoachingForm, WorkshopForm, ProjectLeaderNoteForm, PasswordChangeForm
from app.utils import role_required, permission_required, ROLE_ADMIN, ROLE_BETRIEBSLEITER, ROLE_PROJEKTLEITER, ROLE_TEAMLEITER, ROLE_ABTEILUNGSLEITER, ROLE_QM, ROLE_SALESCOACH, ROLE_TRAINER, get_or_create_archiv_team, ARCHIV_TEAM_NAME
from datetime import datetime, timezone, timedelta
import calendar

bp = Blueprint('main', __name__)

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


@bp.route('/')
@login_required
def index():
    return render_template('main/index_choice.html', config=current_app.config)


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

    # Build query
    query = Coaching.query.join(TeamMember, Coaching.team_member_id == TeamMember.id)\
                           .join(Team, TeamMember.team_id == Team.id)\
                           .join(User, Coaching.coach_id == User.id, isouter=True)\
                           .filter(*filters)

    # Pagination
    coachings_paginated = query.order_by(desc(Coaching.coaching_date)).paginate(page=page, per_page=15, error_out=False)

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
                           config=current_app.config)


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
    current_user_team_ids = [team.id for team in current_user.teams_led] if current_user_role == ROLE_TEAMLEITER else []
    form = CoachingForm(current_user_role=current_user_role, current_user_team_ids=current_user_team_ids)
    form.update_team_member_choices(exclude_archiv=True, project_id=project_id)

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

    return render_template('main/add_coaching.html', form=form, config=current_app.config)


# --- Edit Coaching ---
@bp.route('/edit-coaching/<int:coaching_id>', methods=['GET', 'POST'])
@login_required
@permission_required('edit_coaching')
def edit_coaching(coaching_id):
    coaching = Coaching.query.get_or_404(coaching_id)
    if current_user.role_name not in [ROLE_ADMIN, ROLE_BETRIEBSLEITER] and coaching.coach_id != current_user.id:
        flash('Sie haben keine Berechtigung, dieses Coaching zu bearbeiten.', 'danger')
        return redirect(url_for('main.coaching_dashboard'))

    form = CoachingForm(obj=coaching, current_user_role=current_user.role_name, current_user_team_ids=[])
    form.update_team_member_choices(exclude_archiv=True, project_id=coaching.project_id)

    if form.validate_on_submit():
        form.populate_obj(coaching)
        if form.coaching_style.data != 'TCAP':
            coaching.tcap_id = None
        db.session.commit()
        flash('Coaching erfolgreich aktualisiert.', 'success')
        return redirect(url_for('main.coaching_dashboard'))

    return render_template('main/add_coaching.html', form=form, is_edit_mode=True, coaching=coaching, config=current_app.config)


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


# --- Team View (for team leaders) ---
@bp.route('/team-view')
@login_required
@permission_required('view_own_team')
def team_view():
    teams_led = current_user.teams_led.all()
    if not teams_led:
        flash('Sie sind kein Teamleiter eines Teams.', 'info')
        return redirect(url_for('main.index'))
    team = teams_led[0]
    members = team.members.order_by(TeamMember.name).all()
    return render_template('main/team_view.html', team=team, members=members, config=current_app.config)


# --- PL/QM Dashboard ---
@bp.route('/pl-qm-dashboard')
@login_required
@permission_required('view_pl_qm_dashboard')
def pl_qm_dashboard():
    project_id = get_visible_project_id()
    if not project_id:
        flash('Kein Projekt ausgewählt.', 'danger')
        return redirect(url_for('main.index'))
    project = Project.query.get(project_id)
    teams = Team.query.filter_by(project_id=project_id).order_by(Team.name).all()
    members = TeamMember.query.join(Team).filter(Team.project_id == project_id).order_by(Team.name, TeamMember.name).all()
    coachings = Coaching.query.filter_by(project_id=project_id).order_by(desc(Coaching.coaching_date)).limit(50).all()
    return render_template('main/pl_qm_dashboard.html', project=project, teams=teams, members=members, coachings=coachings, config=current_app.config)


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
