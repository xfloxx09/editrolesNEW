# app/models.py
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app import db
from datetime import datetime

team_leaders = db.Table('team_leaders',
    db.Column('team_id', db.Integer, db.ForeignKey('teams.id')),
    db.Column('user_id', db.Integer, db.ForeignKey('users.id'))
)

user_projects = db.Table('user_projects',
    db.Column('user_id', db.Integer, db.ForeignKey('users.id')),
    db.Column('project_id', db.Integer, db.ForeignKey('projects.id'))
)

workshop_participants = db.Table('workshop_participants',
    db.Column('workshop_id', db.Integer, db.ForeignKey('workshops.id')),
    db.Column('team_member_id', db.Integer, db.ForeignKey('team_members.id')),
    db.Column('individual_rating', db.Integer),
    db.Column('original_team_id', db.Integer, db.ForeignKey('teams.id'))
)


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    email = db.Column(db.String(120))
    password_hash = db.Column(db.String(128))
    role_id = db.Column(db.Integer, db.ForeignKey('roles.id'))
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    team_id_if_leader = db.Column(db.Integer, db.ForeignKey('teams.id'))

    role = db.relationship('Role', backref='users')
    project = db.relationship('Project', backref='users')
    teams_led = db.relationship('Team', secondary=team_leaders, backref='leaders')
    projects = db.relationship('Project', secondary=user_projects, backref='users')
    team_members = db.relationship('TeamMember', backref='user', lazy='dynamic')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def role_name(self):
        return self.role.name if self.role else None

    def has_permission(self, permission_name):
        if self.role:
            return self.role.has_permission(permission_name)
        return False


class Role(db.Model):
    __tablename__ = 'roles'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(255))

    permissions = db.relationship('Permission', secondary='role_permissions', backref='roles')
    projects = db.relationship('Project', secondary='role_projects', backref='roles')

    def has_permission(self, permission_name):
        return any(perm.name == permission_name for perm in self.permissions)


class Permission(db.Model):
    __tablename__ = 'permissions'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(255))


class Project(db.Model):
    __tablename__ = 'projects'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.String(500))

    users = db.relationship('User', backref='project_ref')
    teams = db.relationship('Team', backref='project_ref')
    workshops = db.relationship('Workshop', backref='project')
    coachings = db.relationship('Coaching', backref='project')


class Team(db.Model):
    __tablename__ = 'teams'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)

    members = db.relationship('TeamMember', backref='team')
    __table_args__ = (db.UniqueConstraint('name', 'project_id', name='teams_name_project_id_key'),)


class TeamMember(db.Model):
    __tablename__ = 'team_members'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True)
    pylon = db.Column(db.String(50))
    plt_id = db.Column(db.String(50))
    ma_kennung = db.Column(db.String(50))
    dag_id = db.Column(db.String(50))
    original_team_id = db.Column(db.Integer, db.ForeignKey('teams.id'))
    original_project_id = db.Column(db.Integer, db.ForeignKey('projects.id'))

    team = db.relationship('Team', foreign_keys=[team_id])
    original_team = db.relationship('Team', foreign_keys=[original_team_id])
    original_project = db.relationship('Project', foreign_keys=[original_project_id])


class Coaching(db.Model):
    __tablename__ = 'coachings'
    id = db.Column(db.Integer, primary_key=True)
    team_member_id = db.Column(db.Integer, db.ForeignKey('team_members.id'), nullable=False)
    coach_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    coaching_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    coaching_style = db.Column(db.String(50), nullable=False)
    tcap_id = db.Column(db.String(100))
    coaching_subject = db.Column(db.String(50))
    leitfaden_begruessung = db.Column(db.String(10), default='k.A.')
    leitfaden_legitimation = db.Column(db.String(10), default='k.A.')
    leitfaden_pka = db.Column(db.String(10), default='k.A.')
    leitfaden_kek = db.Column(db.String(10), default='k.A.')
    leitfaden_angebot = db.Column(db.String(10), default='k.A.')
    leitfaden_zusammenfassung = db.Column(db.String(10), default='k.A.')
    leitfaden_kzb = db.Column(db.String(10), default='k.A.')
    performance_mark = db.Column(db.Integer, nullable=False)
    time_spent = db.Column(db.Integer, nullable=False)
    coach_notes = db.Column(db.Text)
    project_leader_notes = db.Column(db.Text)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'))
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id'))
    assigned_coaching_id = db.Column(db.Integer, db.ForeignKey('assigned_coachings.id'))

    team_member = db.relationship('TeamMember', backref='coachings')
    coach = db.relationship('User', backref='coachings_done')
    team = db.relationship('Team')


class Workshop(db.Model):
    __tablename__ = 'workshops'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    coach_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    workshop_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    overall_rating = db.Column(db.Integer, nullable=False)
    time_spent = db.Column(db.Integer, nullable=False)
    notes = db.Column(db.Text)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'))

    coach = db.relationship('User', backref='workshops')
    participants = db.relationship('TeamMember', secondary=workshop_participants, backref='workshops')
    project = db.relationship('Project')


class AssignedCoaching(db.Model):
    __tablename__ = 'assigned_coachings'
    id = db.Column(db.Integer, primary_key=True)
    project_leader_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    coach_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    team_member_id = db.Column(db.Integer, db.ForeignKey('team_members.id'), nullable=False)
    deadline = db.Column(db.DateTime, nullable=False)
    expected_coaching_count = db.Column(db.Integer, nullable=False)
    desired_performance_note = db.Column(db.Integer)
    current_performance_note_at_assign = db.Column(db.Float)
    status = db.Column(db.String(20), nullable=False, default='pending')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    project_leader = db.relationship('User', foreign_keys=[project_leader_id])
    coach = db.relationship('User', foreign_keys=[coach_id])
    team_member = db.relationship('TeamMember', backref='assigned_coachings')
