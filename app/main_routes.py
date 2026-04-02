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
        # Admin and Betriebsleiter see all projects – they can select via session
        if current_user.role_name in [ROLE_ADMIN, ROLE_BETRIEBSLEITER]:
            project_id = session.get('active_project')
            if project_id:
                return project_id
            # Fallback to first project
            first = Project.query.first()
            return first.id if first else None
        elif current_user.role_name == ROLE_ABTEILUNGSLEITER:
            # Abteilungsleiter have multiple projects – use session or first
            project_id = session.get('active_project')
            if project_id and project_id in [p.id for p in current_user.projects]:
                return project_id
            first = current_user.projects.first()
            return first.id if first else None
        else:
            # Regular users have a single project
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


# --- Coaching Dashboard (for coaches) ---
@bp.route('/coaching-dashboard')
@login_required
@permission_required('view_coaching_dashboard')
def coaching_dashboard():
    # Get the coach's own team member (if any)
    team_member = current_user.team_members[0] if current_user.team_members else None
    if not team_member:
        flash('Kein Teammitglied gefunden. Bitte kontaktieren Sie den Administrator.', 'warning')
        return redirect(url_for('main.index'))
    
    page = request.args.get('page', 1, type=int)
    period_arg = request.args.get('period', 'all')
    team_arg = request.args.get('team', "all")
    search_arg = request.args.get('search', default="", type=str).strip()
    member_filter = request.args.get('member_id', type=int)
    project_filter = get_visible_project_id()

    coachings_query = Coaching.query \
        .join(TeamMember, Coaching.team_member_id == TeamMember.id) \
        .join(User, Coaching.coach_id == User.id, isouter=True) \
        .join(Team, TeamMember.team_id == Team.id)

    # Restrict to own team if the permission is present
    if current_user.has_permission('coach_own_team_only') and team_member:
        coachings_query = coachings_query.filter(TeamMember.team_id == team_member.team_id)
        # Override team_arg to show only own team in filters
        team_arg = str(team_member.team_id)
    else:
        # Original behaviour: exclude ARCHIV team when "all" is selected
        if team_arg == 'all':
            coachings_query = coachings_query.filter(Team.name != ARCHIV_TEAM_NAME)

    if project_filter:
        coachings_query = coachings_query.filter(Coaching.project_id == project_filter)

    start_date, end_date = calculate_date_range(period_arg)
    if start_date:
        coachings_query = coachings_query.filter(Coaching.coaching_date >= start_date)
    if end_date:
        coachings_query = coachings_query.filter(Coaching.coaching_date <= end_date)

    if team_arg != 'all' and team_arg.isdigit():
        coachings_query = coachings_query.filter(Team.id == int(team_arg))

    if member_filter:
        coachings_query = coachings_query.filter(Coaching.team_member_id == member_filter)

    if search_arg:
        search_pattern = f"%{search_arg}%"
        coachings_query = coachings_query.filter(
            or_(
                TeamMember.name.ilike(search_pattern),
                User.username.ilike(search_pattern),
                Coaching.coaching_subject.ilike(search_pattern),
                Coaching.coach_notes.ilike(search_pattern)
            )
        )

    coachings_paginated = coachings_query.order_by(desc(Coaching.coaching_date)).paginate(page=page, per_page=15, error_out=False)

    # Prepare filter dropdown lists (respect the permission)
    if current_user.has_permission('coach_own_team_only') and team_member:
        # Only show the coach's own team and its members
        all_teams = [team_member.team]
        all_team_members = TeamMember.query.filter_by(team_id=team_member.team_id).order_by(TeamMember.name).all()
        all_coaches = [current_user]
    else:
        all_teams = Team.query.filter(Team.name != ARCHIV_TEAM_NAME).order_by(Team.name).all()
        all_team_members = TeamMember.query.join(Team).filter(Team.name != ARCHIV_TEAM_NAME).order_by(TeamMember.name).all()
        all_coaches = User.query.filter(User.coachings_done.any()).distinct().order_by(User.username).all()

    # Month options for period filter
    now = datetime.now(timezone.utc)
    current_year = now.year
    previous_year = current_year - 1
    month_options = []
    for m in range(12, 0, -1):
        month_options.append({'value': f"{previous_year}-{m:02d}", 'text': f"{get_month_name_german(m)} {previous_year}"})
    for m in range(now.month, 0, -1):
        month_options.append({'value': f"{current_year}-{m:02d}", 'text': f"{get_month_name_german(m)} {current_year}"})

    return render_template('main/coaching_dashboard.html',
                           coachings=coachings_paginated,
                           all_teams=all_teams,
                           all_team_members=all_team_members,
                           all_coaches=all_coaches,
                           month_options=month_options,
                           current_period=period_arg,
                           current_team=team_arg,
                           current_member=member_filter,
                           current_search=search_arg,
                           config=current_app.config)


@bp.route('/add-coaching', methods=['GET', 'POST'])
@login_required
@permission_required('add_coaching')
def add_coaching():
    # Determine which project to use
    project_id = get_visible_project_id()
    if not project_id:
        flash('Kein Projekt ausgewählt oder zugeordnet.', 'danger')
        return redirect(url_for('main.index'))

    # Prepare form with current user's role and team IDs
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
            # Update assignment status
            assignment = AssignedCoaching.query.get(form.assigned_coaching_id.data)
            if assignment:
                assignment.status = 'in_progress'

        db.session.add(coaching)
        db.session.commit()
        flash('Coaching erfolgreich gespeichert!', 'success')
        return redirect(url_for('main.coaching_dashboard'))

    # For GET, if there is an assigned coaching ID in the URL, pre-fill
    assigned_id = request.args.get('assigned_id', type=int)
    if assigned_id:
        assignment = AssignedCoaching.query.get(assigned_id)
        if assignment and assignment.coach_id == current_user.id and assignment.status == 'pending':
            form.assigned_coaching_id.data = assigned_id
            form.team_member_id.data = assignment.team_member_id
            # Optionally pre-fill desired performance note
            if assignment.desired_performance_note:
                form.performance_mark.data = assignment.desired_performance_note
            # Update assignment status to accepted
            assignment.status = 'accepted'
            db.session.commit()
            flash('Coaching-Aufgabe angenommen.', 'success')
        else:
            flash('Ungültige oder nicht verfügbare Aufgabe.', 'danger')

    return render_template('main/add_coaching.html', form=form, config=current_app.config)


@bp.route('/edit-coaching/<int:coaching_id>', methods=['GET', 'POST'])
@login_required
@permission_required('edit_coaching')
def edit_coaching(coaching_id):
    coaching = Coaching.query.get_or_404(coaching_id)
    # Permission check: only admin, betriebsleiter, or the coach who created it can edit
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


# --- Workshop routes (simplified, add as needed) ---
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
    workshops_query = Workshop.query
    if project_filter:
        workshops_query = workshops_query.filter(Workshop.project_id == project_filter)
    start_date, end_date = calculate_date_range(period_arg)
    if start_date:
        workshops_query = workshops_query.filter(Workshop.workshop_date >= start_date)
    if end_date:
        workshops_query = workshops_query.filter(Workshop.workshop_date <= end_date)
    if search_arg:
        pattern = f"%{search_arg}%"
        workshops_query = workshops_query.filter(
            or_(
                Workshop.title.ilike(pattern),
                Workshop.notes.ilike(pattern),
                User.username.ilike(pattern)
            )
        ).join(User, Workshop.coach_id == User.id)
    workshops_paginated = workshops_query.order_by(desc(Workshop.workshop_date)).paginate(page=page, per_page=15, error_out=False)
    return render_template('main/workshop_dashboard.html', workshops=workshops_paginated, current_search=search_arg, current_period=period_arg, config=current_app.config)


# --- Team View (for team leaders) ---
@bp.route('/team-view')
@login_required
@permission_required('view_own_team')
def team_view():
    # Get teams that the current user leads
    teams_led = current_user.teams_led.all()
    if not teams_led:
        flash('Sie sind kein Teamleiter eines Teams.', 'info')
        return redirect(url_for('main.index'))
    # For simplicity, show the first team
    team = teams_led[0]
    members = team.members.order_by(TeamMember.name).all()
    return render_template('main/team_view.html', team=team, members=members, config=current_app.config)


# --- PL/QM Dashboard (project leader / quality manager) ---
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


# --- Project selection for admins and abteilungsleiter ---
@bp.route('/set-project/<int:project_id>')
@login_required
def set_project(project_id):
    project = Project.query.get_or_404(project_id)
    # Check permission
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
