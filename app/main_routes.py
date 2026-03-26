# app/main_routes.py
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, current_app, jsonify, session
from flask_login import login_required, current_user
from app import db
from app.models import User, Team, TeamMember, Coaching, Workshop, workshop_participants, Project, AssignedCoaching, Role
from app.forms import CoachingForm, ProjectLeaderNoteForm, PasswordChangeForm, WorkshopForm, AssignedCoachingForm
from app.utils import role_required, permission_required, ROLE_ADMIN, ROLE_BETRIEBSLEITER, ROLE_PROJEKTLEITER, ROLE_QM, ROLE_SALESCOACH, ROLE_TRAINER, ROLE_TEAMLEITER, ROLE_ABTEILUNGSLEITER, ARCHIV_TEAM_NAME
from sqlalchemy import desc, func, or_, and_, false
from datetime import datetime, timedelta, timezone, time
import sqlalchemy
from calendar import monthrange

bp = Blueprint('main', __name__)

# --- HILFSFUNKTIONEN (unchanged) ---
def get_month_name_german(month_number):
    months_german = {1:"Januar",2:"Februar",3:"März",4:"April",5:"Mai",6:"Juni",7:"Juli",8:"August",9:"September",10:"Oktober",11:"November",12:"Dezember"}
    return months_german.get(month_number, "")

def calculate_date_range(period_filter_str=None):
    now = datetime.now(timezone.utc); start_date, end_date = None, None
    if not period_filter_str or period_filter_str == 'all': return None, None
    if period_filter_str == '7days': start_date=(now-timedelta(days=6)).replace(hour=0,minute=0,second=0,microsecond=0); end_date=now.replace(hour=23,minute=59,second=59,microsecond=999999)
    elif period_filter_str == '30days': start_date=(now-timedelta(days=29)).replace(hour=0,minute=0,second=0,microsecond=0); end_date=now.replace(hour=23,minute=59,second=59,microsecond=999999)
    elif period_filter_str == 'current_quarter':
        c_month=now.month; yr=now.year
        if 1<=c_month<=3: start_date,end_date=datetime(yr,1,1,0,0,0,tzinfo=timezone.utc),datetime(yr,3,monthrange(yr,3)[1],23,59,59,999999,tzinfo=timezone.utc)
        elif 4<=c_month<=6: start_date,end_date=datetime(yr,4,1,0,0,0,tzinfo=timezone.utc),datetime(yr,6,monthrange(yr,6)[1],23,59,59,999999,tzinfo=timezone.utc)
        elif 7<=c_month<=9: start_date,end_date=datetime(yr,7,1,0,0,0,tzinfo=timezone.utc),datetime(yr,9,monthrange(yr,9)[1],23,59,59,999999,tzinfo=timezone.utc)
        else: start_date,end_date=datetime(yr,10,1,0,0,0,tzinfo=timezone.utc),datetime(yr,12,monthrange(yr,12)[1],23,59,59,999999,tzinfo=timezone.utc)
    elif period_filter_str == 'current_year': yr=now.year; start_date,end_date=datetime(yr,1,1,0,0,0,tzinfo=timezone.utc),datetime(yr,12,monthrange(yr,12)[1],23,59,59,999999,tzinfo=timezone.utc)
    elif '-' in period_filter_str and len(period_filter_str)==7:
        try:
            y_s,m_s=period_filter_str.split('-'); yr=int(y_s); m_i=int(m_s)
            if 1<=m_i<=12:
                start_date=datetime(yr,m_i,1,0,0,0,tzinfo=timezone.utc)
                end_date=datetime(yr,m_i,monthrange(yr,m_i)[1],23,59,59,999999,tzinfo=timezone.utc)
        except ValueError:
            pass
    return start_date,end_date

def get_filtered_coachings_subquery(period_filter_str=None, project_id=None):
    q = db.session.query(
        Coaching.id.label("coaching_id_sq"),
        Coaching.team_id.label("team_id_sq"),
        Coaching.performance_mark.label("performance_mark_sq"),
        Coaching.time_spent.label("time_spent_sq"),
        Coaching.coaching_subject.label("coaching_subject_sq")
    )
    s_d,e_d = calculate_date_range(period_filter_str)
    if s_d: q = q.filter(Coaching.coaching_date >= s_d)
    if e_d: q = q.filter(Coaching.coaching_date <= e_d)
    if project_id:
        q = q.filter(Coaching.project_id == project_id)
    return q.subquery('filtered_coachings_sq')

def get_performance_data_for_charts(period_filter_str=None, selected_team_id_str=None, project_id=None):
    sq = get_filtered_coachings_subquery(period_filter_str, project_id)
    q = db.session.query(
        Team.id.label('team_id'),
        Team.name.label('team_name'),
        func.coalesce(func.avg(sq.c.performance_mark_sq), 0).label('avg_perf_mark'),
        func.coalesce(func.sum(sq.c.time_spent_sq), 0).label('total_time'),
        func.coalesce(func.count(sq.c.coaching_id_sq), 0).label('num_coachings')
    ).select_from(Team)\
     .outerjoin(sq, Team.id == sq.c.team_id_sq)

    q = q.filter(Team.name != ARCHIV_TEAM_NAME)

    if project_id:
        q = q.filter(Team.project_id == project_id)

    if selected_team_id_str and selected_team_id_str.isdigit():
        q = q.filter(Team.id == int(selected_team_id_str))

    res = q.group_by(Team.id, Team.name).having(func.count(sq.c.coaching_id_sq) > 0).order_by(Team.name).all()
    avg_perf_pcnt = [round(r.avg_perf_mark * 10, 2) if r.avg_perf_mark is not None else 0 for r in res]
    total_time_spent_values_list = [r.total_time for r in res]

    return {
        'labels': [r.team_name for r in res],
        'avg_performance_values': avg_perf_pcnt,
        'total_time_spent_values': total_time_spent_values_list,
        'coachings_done_values': [r.num_coachings for r in res]
    }

def get_coaching_subject_distribution(period_filter_str=None, selected_team_id_str=None, project_id=None):
    sq = get_filtered_coachings_subquery(period_filter_str, project_id)
    q = db.session.query(
        sq.c.coaching_subject_sq.label('subject'),
        func.count(sq.c.coaching_id_sq).label('count')
    ).select_from(sq).filter(sq.c.coaching_subject_sq.isnot(None)).filter(sq.c.coaching_subject_sq != '')

    q = q.join(Team, sq.c.team_id_sq == Team.id)\
         .filter(Team.name != ARCHIV_TEAM_NAME)

    if project_id:
        q = q.filter(Team.project_id == project_id)

    if selected_team_id_str and selected_team_id_str.isdigit():
        q = q.filter(Team.id == int(selected_team_id_str))

    res = q.group_by(sq.c.coaching_subject_sq).order_by(desc('count')).all()
    return {'labels':[r.subject for r in res if r.subject],'values':[r.count for r in res if r.subject]}

def get_visible_project_id():
    if current_user.role_name in [ROLE_ADMIN, ROLE_BETRIEBSLEITER, ROLE_ABTEILUNGSLEITER]:
        return request.args.get('project', type=int) or session.get('active_project')
    else:
        return current_user.project_id

def update_assignment_progress(assignment_id):
    assignment = AssignedCoaching.query.get(assignment_id)
    if assignment and assignment.status in ['accepted', 'in_progress', 'pending']:
        completed = assignment.coachings.count()
        if completed >= assignment.expected_coaching_count:
            assignment.status = 'completed'
        elif completed > 0:
            assignment.status = 'in_progress'
        db.session.commit()

# --- ROUTEN (only the ones that changed) ---
# The following routes remain exactly as they were in your version, only the decorators/logic changed where noted.

# ... (other routes unchanged: index, coaching_dashboard, team_view, add_coaching, add_workshop, edit_workshop, workshop_dashboard, profile, edit_coaching)

# ---- PL/QM Dashboard (changed decorator) ----
@bp.route('/coaching_review_dashboard', methods=['GET', 'POST'])
@login_required
@permission_required('view_pl_qm_dashboard')
def pl_qm_dashboard():
    # the function body stays exactly the same as before
    # (no other changes needed inside)
    page = request.args.get('page', 1, type=int)
    selected_team_id_filter_str = request.args.get('team_id_filter', None)
    project_filter = get_visible_project_id()

    coachings_query = Coaching.query.join(TeamMember, Coaching.team_member_id == TeamMember.id)\
                                     .join(Team, TeamMember.team_id == Team.id)\
                                     .filter(Team.name != ARCHIV_TEAM_NAME)

    if project_filter:
        coachings_query = coachings_query.filter(Coaching.project_id == project_filter)

    coachings_paginated = coachings_query.order_by(desc(Coaching.coaching_date)).paginate(page=page, per_page=10, error_out=False)
    note_form = ProjectLeaderNoteForm()
    title = "Notizen Dashboard"
    if current_user.role_name == ROLE_QM:
        title = "Quality Coach Dashboard"
    elif current_user.role_name == ROLE_PROJEKTLEITER:
        title = "Projektleiter Dashboard"
    elif current_user.role_name == ROLE_ABTEILUNGSLEITER:
        title = "Abteilungsleiter Dashboard"
    elif current_user.role_name in [ROLE_ADMIN, ROLE_BETRIEBSLEITER]:
        title = "Dashboard (Alle Projekte)"

    if request.method == 'POST' and 'submit_note' in request.form:
        form_val = ProjectLeaderNoteForm(request.form)
        coaching_id_str = request.form.get('coaching_id')
        if not coaching_id_str or not coaching_id_str.isdigit():
            flash("Gültige Coaching-ID fehlt.", 'danger')
        elif form_val.validate():
            try:
                coaching = Coaching.query.get_or_404(int(coaching_id_str))
                if current_user.role_name not in [ROLE_ADMIN, ROLE_BETRIEBSLEITER] and coaching.project_id != project_filter:
                    abort(403)
                coaching.project_leader_notes = form_val.notes.data
                db.session.commit()
                flash(f'Notiz für Coaching ID {coaching_id_str} gespeichert.', 'success')
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"Note save error: {e}")
                flash('Fehler Notizspeicherung.', 'danger')
        else:
            for f, errs in form_val.errors.items():
                flash(f"Validierungsfehler '{form_val[f].label.text}': {'; '.join(errs)}", 'danger')
        return redirect(url_for('main.pl_qm_dashboard',
                                page=request.args.get('page', 1, type=int),
                                team_id_filter=selected_team_id_filter_str))

    # rest of the function remains unchanged
    # ... (all the code that computes top_3, flop_3, etc.)
    # ... (until the final return)

    return render_template('main/projektleiter_dashboard.html',
                           title=title,
                           coachings_paginated=coachings_paginated,
                           note_form=note_form,
                           top_3_teams=top_3,
                           flop_3_teams=flop_3,
                           all_teams_for_filter=all_teams_for_filter_dropdown,
                           selected_team_id_filter=selected_team_id_filter_str,
                           selected_team_object_for_cards=selected_team_object_for_cards,
                           members_data_for_cards=members_data_for_cards,
                           total_coachings_overall=total_coachings_overall,
                           total_time_overall=total_time_overall,
                           avg_score_overall=avg_score_overall,
                           teams_stats=teams_stats,
                           chart_labels=chart_labels,
                           chart_avg_performance_values=chart_avg_performance_values,
                           subject_labels=subject_labels,
                           subject_values=subject_values,
                           config=current_app.config)


# --- Assigned Coachings (changed view_type detection) ---
@bp.route('/assigned-coachings')
@login_required
def assigned_coachings():
    page = request.args.get('page', 1, type=int)
    project_filter = get_visible_project_id()
    
    # Get filter parameters for assignments
    status_filter = request.args.get('status', 'current')  # 'current' or 'completed'
    team_filter = request.args.get('team', type=int)
    coach_filter = request.args.get('coach', type=int)
    member_filter = request.args.get('member', type=int)
    search_term = request.args.get('search', default="", type=str).strip()
    sort_by = request.args.get('sort_by', 'deadline')
    sort_dir = request.args.get('sort_dir', 'asc')
    
    # Define status groups
    current_statuses = ['pending', 'accepted', 'in_progress']
    completed_statuses = ['completed', 'expired', 'cancelled', 'rejected']
    
    # Determine which statuses to show based on tab
    if status_filter == 'current':
        statuses_to_show = current_statuses
        tab_active = 'current'
    else:
        statuses_to_show = completed_statuses
        tab_active = 'completed'
    
    # Build base query – decide view_type based on permission to create assignments
    if current_user.has_permission('create_assigned_coaching'):
        view_type = 'pl'
        if current_user.role_name in [ROLE_ADMIN, ROLE_BETRIEBSLEITER]:
            query = AssignedCoaching.query
        else:
            query = AssignedCoaching.query.filter_by(project_leader_id=current_user.id)
        if project_filter:
            query = query.join(AssignedCoaching.team_member).join(TeamMember.team).filter(Team.project_id == project_filter)
        
        # --- Fetch member performance data for quick overview (only for PL) ---
        # Get allowed project IDs
        if current_user.role_name in [ROLE_ADMIN, ROLE_BETRIEBSLEITER]:
            allowed_project_ids = [p.id for p in Project.query.all()]
        elif current_user.role_name == ROLE_ABTEILUNGSLEITER:
            allowed_project_ids = current_user.get_allowed_project_ids()
        else:
            allowed_project_ids = [current_user.project_id]
        
        # If project filter is active, restrict to that project
        if project_filter and project_filter in allowed_project_ids:
            allowed_project_ids = [project_filter]
        
        # Get all members from allowed projects, excluding archiv
        members = TeamMember.query.join(Team, TeamMember.team_id == Team.id).filter(
            Team.project_id.in_(allowed_project_ids),
            Team.name != ARCHIV_TEAM_NAME
        ).order_by(Team.name, TeamMember.name).all()
        
        # Compute performance for each member with combined score
        member_performance = []
        all_scores = []
        for member in members:
            coachings = Coaching.query.filter_by(team_member_id=member.id).all()
            avg_score = 0
            coaching_count = len(coachings)
            total_time = 0
            if coachings:
                avg_score = sum(c.overall_score for c in coachings) / coaching_count
                total_time = sum(c.time_spent for c in coachings)
            member_performance.append({
                'id': member.id,
                'name': member.name,
                'team_name': member.team.name,
                'avg_score': round(avg_score, 2),
                'coaching_count': coaching_count,
                'total_time': total_time,
                'last_coaching_date': coachings[-1].coaching_date if coachings else None
            })
            all_scores.append(avg_score)
        
        # Calculate combined score (weighted: 40% performance, 30% coaching count, 30% total time)
        if member_performance:
            max_avg_score = max(m['avg_score'] for m in member_performance) or 1
            max_coaching_count = max(m['coaching_count'] for m in member_performance) or 1
            max_total_time = max(m['total_time'] for m in member_performance) or 1
            
            for m in member_performance:
                norm_score = (m['avg_score'] / max_avg_score) * 100 if max_avg_score else 0
                norm_count = (m['coaching_count'] / max_coaching_count) * 100 if max_coaching_count else 0
                norm_time = (m['total_time'] / max_total_time) * 100 if max_total_time else 0
                combined = (norm_score * 0.4) + (norm_count * 0.3) + (norm_time * 0.3)
                m['combined_score'] = round(combined, 2)
        
        member_performance_sorted = sorted(member_performance, key=lambda x: x.get('combined_score', 0), reverse=True)
        top_performers = member_performance_sorted[:5]
        bottom_performers = member_performance_sorted[-5:] if len(member_performance_sorted) >= 5 else member_performance_sorted
        bottom_performers.sort(key=lambda x: x.get('combined_score', 0))
    else:
        view_type = 'coach'
        query = AssignedCoaching.query.filter_by(coach_id=current_user.id)
        member_performance = []
        top_performers = []
        bottom_performers = []
    
    # Apply status filter
    query = query.filter(AssignedCoaching.status.in_(statuses_to_show))
    
    # Apply additional filters
    if team_filter:
        query = query.join(AssignedCoaching.team_member).join(TeamMember.team).filter(Team.id == team_filter)
    if coach_filter:
        query = query.filter(AssignedCoaching.coach_id == coach_filter)
    if member_filter:
        query = query.filter(AssignedCoaching.team_member_id == member_filter)
    if search_term:
        search_pattern = f"%{search_term}%"
        if 'team_member' not in str(query):
            query = query.join(AssignedCoaching.team_member)
        if 'coach' not in str(query):
            query = query.join(User, AssignedCoaching.coach_id == User.id)
        query = query.filter(
            or_(
                TeamMember.name.ilike(search_pattern),
                User.username.ilike(search_pattern),
                AssignedCoaching.status.ilike(search_pattern)
            )
        )
    
    # Apply sorting
    sort_column = {
        'deadline': AssignedCoaching.deadline,
        'member_name': TeamMember.name,
        'coach_name': User.username,
        'progress': AssignedCoaching.expected_coaching_count,
        'expected_count': AssignedCoaching.expected_coaching_count
    }.get(sort_by, AssignedCoaching.deadline)
    
    if sort_by in ['member_name', 'coach_name']:
        if 'team_member' not in str(query):
            query = query.join(AssignedCoaching.team_member)
        if sort_by == 'coach_name' and 'coach' not in str(query):
            query = query.join(User, AssignedCoaching.coach_id == User.id)
    
    if sort_dir == 'asc':
        query = query.order_by(sort_column.asc())
    else:
        query = query.order_by(sort_column.desc())
    
    assignments = query.paginate(page=page, per_page=10, error_out=False)
    
    # Prepare filter dropdowns
    all_teams = []
    all_coaches = []
    all_members = []
    if view_type == 'pl':
        if current_user.role_name in [ROLE_ADMIN, ROLE_BETRIEBSLEITER]:
            project_ids = [p.id for p in Project.query.all()]
        elif current_user.role_name == ROLE_ABTEILUNGSLEITER:
            project_ids = current_user.get_allowed_project_ids()
        else:
            project_ids = [current_user.project_id]
        
        teams_q = Team.query.filter(Team.project_id.in_(project_ids), Team.name != ARCHIV_TEAM_NAME).order_by(Team.name)
        all_teams = teams_q.all()
        
        # use join with Role to filter coaches by role name
        coaches_q = User.query.join(User.role).filter(Role.name.in_(['Teamleiter', 'Qualitätsmanager', 'SalesCoach', 'Trainer', 'Betriebsleiter'])).order_by(User.username)
        all_coaches = coaches_q.all()
        
        members_q = TeamMember.query.join(Team, TeamMember.team_id == Team.id).filter(
            Team.project_id.in_(project_ids),
            Team.name != ARCHIV_TEAM_NAME
        ).order_by(TeamMember.name)
        all_members = members_q.all()
    
    all_projects = []
    if current_user.role_name in [ROLE_ADMIN, ROLE_BETRIEBSLEITER]:
        all_projects = Project.query.order_by(Project.name).all()
    elif current_user.role_name == ROLE_ABTEILUNGSLEITER:
        all_projects = current_user.projects.order_by(Project.name).all()
    
    return render_template('main/assigned_coachings.html',
                           assignments=assignments,
                           view_type=view_type,
                           all_projects=all_projects,
                           current_project_filter=project_filter if view_type == 'pl' else None,
                           member_performance=member_performance,
                           top_performers=top_performers,
                           bottom_performers=bottom_performers,
                           tab_active=tab_active,
                           status_filter=status_filter,
                           team_filter=team_filter,
                           coach_filter=coach_filter,
                           member_filter=member_filter,
                           search_term=search_term,
                           sort_by=sort_by,
                           sort_dir=sort_dir,
                           all_teams=all_teams,
                           all_coaches=all_coaches,
                           all_members=all_members,
                           config=current_app.config)


# --- Create Assigned Coaching (changed decorator) ---
@bp.route('/assigned-coachings/create', methods=['GET', 'POST'])
@login_required
@permission_required('create_assigned_coaching')
def create_assigned_coaching():
    # function body unchanged – it already uses allowed_project_ids based on role name
    # (no changes needed inside)
    pass

