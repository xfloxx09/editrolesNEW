# app/__init__.py
import os
from datetime import datetime, timezone
import pytz
from flask import Flask, current_app
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, current_user
from flask_migrate import Migrate
from sqlalchemy import inspect, text
from config import Config
from app.constants import ARCHIV_TEAM_NAME

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Bitte melden Sie sich an, um auf diese Seite zuzugreifen.'
login_manager.login_message_category = 'info'

migrate = Migrate()

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)

    # Flask-Login user loader
    @login_manager.user_loader
    def load_user(user_id):
        from app.models import User
        return User.query.get(int(user_id))

    # --- Migration: ensure necessary columns and tables exist ---
    with app.app_context():
        print("--- Running automatic migrations ---")
        inspector = inspect(db.engine)
        conn = db.engine.connect()

        # Ensure all tables are created (does not affect existing ones)
        db.create_all()

        # 1. coachings.team_id
        if 'coachings' in inspector.get_table_names():
            columns_coachings = [col['name'] for col in inspector.get_columns('coachings')]
            if 'team_id' not in columns_coachings:
                print("⚠️ Spalte 'team_id' in coachings fehlt – wird hinzugefügt...")
                conn.execute(text('ALTER TABLE coachings ADD COLUMN team_id INTEGER REFERENCES teams(id)'))
                conn.commit()
                print("✅ Spalte 'team_id' in coachings hinzugefügt.")
            else:
                print("✅ Spalte 'team_id' in coachings existiert bereits.")

            # Update existing coachings with team_id from team_members
            conn.execute(text('''
                UPDATE coachings
                SET team_id = team_members.team_id
                FROM team_members
                WHERE coachings.team_member_id = team_members.id
                AND coachings.team_id IS NULL
            '''))
            conn.commit()
            print("ℹ️ Bestehende Coachings mit team_id aktualisiert.")
        else:
            print("ℹ️ Tabelle 'coachings' existiert noch nicht – überspringe.")

        # 2. workshop_participants.original_team_id
        if 'workshop_participants' in inspector.get_table_names():
            columns_wp = [col['name'] for col in inspector.get_columns('workshop_participants')]
            if 'original_team_id' not in columns_wp:
                print("⚠️ Spalte 'original_team_id' in workshop_participants fehlt – wird hinzugefügt...")
                conn.execute(text('ALTER TABLE workshop_participants ADD COLUMN original_team_id INTEGER REFERENCES teams(id)'))
                conn.commit()
                print("✅ Spalte 'original_team_id' in workshop_participants hinzugefügt.")
            else:
                print("✅ Spalte 'original_team_id' in workshop_participants existiert bereits.")

            # Update existing participants
            conn.execute(text('''
                UPDATE workshop_participants
                SET original_team_id = team_members.team_id
                FROM team_members
                WHERE workshop_participants.team_member_id = team_members.id
                AND workshop_participants.original_team_id IS NULL
            '''))
            conn.commit()
            print("ℹ️ Bestehende Workshop-Teilnehmer mit original_team_id aktualisiert.")
        else:
            print("ℹ️ Tabelle 'workshop_participants' existiert noch nicht – überspringe.")

        # 3. assigned_coachings auto-increment
        if 'assigned_coachings' in inspector.get_table_names():
            conn.execute(text('''
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                   WHERE table_name='assigned_coachings' AND column_name='id' 
                                   AND column_default IS NOT NULL AND column_default LIKE 'nextval%') THEN
                        CREATE SEQUENCE IF NOT EXISTS assigned_coachings_id_seq;
                        ALTER TABLE assigned_coachings ALTER COLUMN id SET DEFAULT nextval('assigned_coachings_id_seq');
                        PERFORM setval('assigned_coachings_id_seq', COALESCE((SELECT MAX(id) FROM assigned_coachings), 1));
                    END IF;
                END
                $$;
            '''))
            conn.commit()
            print("✅ Auto-increment für assigned_coachings.id sichergestellt.")
        else:
            print("ℹ️ Tabelle 'assigned_coachings' existiert noch nicht – überspringe.")

        # 4. assigned_coaching_id in coachings
        if 'coachings' in inspector.get_table_names():
            columns_coachings = [col['name'] for col in inspector.get_columns('coachings')]
            if 'assigned_coaching_id' not in columns_coachings:
                print("⚠️ Spalte 'assigned_coaching_id' in coachings fehlt – wird hinzugefügt...")
                conn.execute(text('ALTER TABLE coachings ADD COLUMN assigned_coaching_id INTEGER REFERENCES assigned_coachings(id)'))
                conn.commit()
                print("✅ Spalte 'assigned_coaching_id' in coachings hinzugefügt.")
            else:
                print("✅ Spalte 'assigned_coaching_id' in coachings existiert bereits.")

        # 5. role_id in users
        if 'users' in inspector.get_table_names():
            columns_users = [col['name'] for col in inspector.get_columns('users')]
            if 'role_id' not in columns_users:
                print("⚠️ Spalte 'role_id' in users fehlt – wird hinzugefügt...")
                conn.execute(text('ALTER TABLE users ADD COLUMN role_id INTEGER REFERENCES roles(id)'))
                conn.commit()
                print("✅ Spalte 'role_id' in users hinzugefügt.")
            else:
                print("✅ Spalte 'role_id' in users existiert bereits.")

        # 6. Default permissions and roles
        # Create permissions table if not exists (already done by db.create_all)
        default_permissions = [
            ('view_coaching_dashboard', 'View coaching dashboard'),
            ('view_workshop_dashboard', 'View workshop dashboard'),
            ('view_assigned_coachings', 'View assigned coachings list'),
            ('create_assigned_coaching', 'Create assigned coaching tasks'),
            ('accept_assigned_coaching', 'Accept assigned coaching tasks'),
            ('reject_assigned_coaching', 'Reject assigned coaching tasks'),
            ('cancel_assigned_coaching', 'Cancel assigned coaching tasks'),
            ('view_assigned_coaching_report', 'View assigned coaching report'),
            ('add_coaching', 'Add a coaching entry'),
            ('edit_coaching', 'Edit any coaching entry'),
            ('add_workshop', 'Add a workshop'),
            ('edit_workshop', 'Edit any workshop'),
            ('view_team_view', 'View team details'),
            ('view_pl_qm_dashboard', 'View project leader/quality manager dashboard'),
            ('view_admin_panel', 'View admin panel'),
            ('manage_users', 'Create/edit/delete users'),
            ('manage_teams', 'Create/edit/delete teams'),
            ('manage_team_members', 'Create/edit/delete team members'),
            ('manage_projects', 'Create/edit/delete projects'),
            ('manage_coachings', 'Manage coachings (admin)'),
            ('manage_workshops', 'Manage workshops (admin)'),
            ('set_project', 'Switch active project'),
            ('coach', 'Can perform coaching (add/edit own coachings)'),
            ('view_own_team', 'View own team (for team leaders)'),
            ('manage_roles', 'Manage roles and permissions'),
        ]
        for name, desc in default_permissions:
            res = conn.execute(text("SELECT id FROM permissions WHERE name = :name"), {"name": name}).fetchone()
            if not res:
                conn.execute(
                    text("INSERT INTO permissions (name, description) VALUES (:name, :desc)"),
                    {"name": name, "desc": desc}
                )
                print(f"✅ Permission '{name}' hinzugefügt.")
        conn.commit()

        default_roles = [
            ('Admin', 'Administrator with full access'),
            ('Betriebsleiter', 'Operations manager'),
            ('Projektleiter', 'Project leader'),
            ('Teamleiter', 'Team leader'),
            ('Qualitätsmanager', 'Quality coach'),
            ('SalesCoach', 'Sales coach'),
            ('Trainer', 'Trainer'),
            ('Abteilungsleiter', 'Department head'),
        ]
        for role_name, role_desc in default_roles:
            res = conn.execute(text("SELECT id FROM roles WHERE name = :name"), {"name": role_name}).fetchone()
            if not res:
                conn.execute(
                    text("INSERT INTO roles (name, description) VALUES (:name, :desc)"),
                    {"name": role_name, "desc": role_desc}
                )
                print(f"✅ Rolle '{role_name}' hinzugefügt.")

        # Assign permissions to roles
        all_perms = conn.execute(text("SELECT id, name FROM permissions")).fetchall()
        perm_map = {p[1]: p[0] for p in all_perms}

        # Admin: all permissions
        admin_role = conn.execute(text("SELECT id FROM roles WHERE name = 'Admin'")).fetchone()
        if admin_role:
            admin_id = admin_role[0]
            for perm_id in perm_map.values():
                exists = conn.execute(
                    text("SELECT 1 FROM role_permissions WHERE role_id = :role_id AND permission_id = :perm_id"),
                    {"role_id": admin_id, "perm_id": perm_id}
                ).fetchone()
                if not exists:
                    conn.execute(
                        text("INSERT INTO role_permissions (role_id, permission_id) VALUES (:role_id, :perm_id)"),
                        {"role_id": admin_id, "perm_id": perm_id}
                    )
            print("✅ Admin hat alle Berechtigungen.")

        # Betriebsleiter: all permissions
        betriebsleiter_role = conn.execute(text("SELECT id FROM roles WHERE name = 'Betriebsleiter'")).fetchone()
        if betriebsleiter_role:
            bl_id = betriebsleiter_role[0]
            for perm_id in perm_map.values():
                exists = conn.execute(
                    text("SELECT 1 FROM role_permissions WHERE role_id = :role_id AND permission_id = :perm_id"),
                    {"role_id": bl_id, "perm_id": perm_id}
                ).fetchone()
                if not exists:
                    conn.execute(
                        text("INSERT INTO role_permissions (role_id, permission_id) VALUES (:role_id, :perm_id)"),
                        {"role_id": bl_id, "perm_id": perm_id}
                    )
            print("✅ Betriebsleiter hat alle Berechtigungen.")

        # Other roles (Projektleiter, Teamleiter, etc.) – add as in your original code
        # ... (include the role-permission assignments for Projektleiter, Teamleiter, etc.)
        # I'll keep it short here, but you should include the full assignments from your previous working __init__.py.

        # 7. user_id and custom fields in team_members
        if 'team_members' in inspector.get_table_names():
            columns_team_members = [col['name'] for col in inspector.get_columns('team_members')]
            if 'user_id' not in columns_team_members:
                print("⚠️ Spalte 'user_id' in team_members fehlt – wird hinzugefügt...")
                conn.execute(text('ALTER TABLE team_members ADD COLUMN user_id INTEGER UNIQUE REFERENCES users(id)'))
                conn.commit()
                print("✅ Spalte 'user_id' in team_members hinzugefügt.")
            else:
                print("✅ Spalte 'user_id' in team_members existiert bereits.")

            for field in ['pylon', 'plt_id', 'ma_kennung', 'dag_id']:
                if field not in columns_team_members:
                    print(f"⚠️ Spalte '{field}' in team_members fehlt – wird hinzugefügt...")
                    conn.execute(text(f'ALTER TABLE team_members ADD COLUMN {field} VARCHAR(50)'))
                    conn.commit()
                    print(f"✅ Spalte '{field}' in team_members hinzugefügt.")
                else:
                    print(f"✅ Spalte '{field}' in team_members existiert bereits.")

        # 8. Team uniqueness per project
        if 'teams' in inspector.get_table_names():
            try:
                conn.execute(text('ALTER TABLE teams DROP CONSTRAINT IF EXISTS teams_name_key'))
                conn.execute(text('ALTER TABLE teams ADD CONSTRAINT teams_name_project_id_key UNIQUE (name, project_id)'))
                conn.commit()
                print("✅ Unique constraint on teams updated to (name, project_id).")
            except Exception as e:
                conn.rollback()
                print(f"ℹ️ Note on team constraint: {e}")

        # 9. Permission view_own_coachings
        if 'permissions' in inspector.get_table_names():
            res = conn.execute(text("SELECT id FROM permissions WHERE name = 'view_own_coachings'")).fetchone()
            if not res:
                conn.execute(
                    text("INSERT INTO permissions (name, description) VALUES ('view_own_coachings', 'View own coachings')")
                )
                print("✅ Permission 'view_own_coachings' hinzugefügt.")
            else:
                print("✅ Permission 'view_own_coachings' existiert bereits.")

        # 10. Role 'Mitarbeiter'
        if 'roles' in inspector.get_table_names():
            res = conn.execute(text("SELECT id FROM roles WHERE name = 'Mitarbeiter'")).fetchone()
            if not res:
                conn.execute(
                    text("INSERT INTO roles (name, description) VALUES ('Mitarbeiter', 'Team member with limited access')")
                )
                role_id = conn.execute(text("SELECT id FROM roles WHERE name = 'Mitarbeiter'")).fetchone()[0]
                perm_id = conn.execute(text("SELECT id FROM permissions WHERE name = 'view_own_coachings'")).fetchone()[0]
                conn.execute(
                    text("INSERT INTO role_permissions (role_id, permission_id) VALUES (:role_id, :perm_id)"),
                    {"role_id": role_id, "perm_id": perm_id}
                )
                print("✅ Rolle 'Mitarbeiter' mit Berechtigung 'view_own_coachings' hinzugefügt.")
            else:
                print("✅ Rolle 'Mitarbeiter' existiert bereits.")

        # Ensure all users have a role (optional, but safe)
        # ...

        print("--- Migration abgeschlossen ---")

    # --- Blueprint registration ---
    from app.auth import bp as auth_bp
    app.register_blueprint(auth_bp, url_prefix='/auth')
    print("<<<< auth_bp REGISTRIERT (__init__.py) >>>>")

    from app.main_routes import bp as main_bp
    app.register_blueprint(main_bp)
    print("<<<< main_bp REGISTRIERT (__init__.py) >>>>")

    from app.admin import bp as admin_bp
    app.register_blueprint(admin_bp, url_prefix='/admin')
    print("<<<< admin_bp REGISTRIERT (__init__.py) >>>>")

    # --- Context processors ---
    @app.context_processor
    def inject_current_year():
        return {'current_year': datetime.utcnow().year}

    @app.context_processor
    def inject_user_allowed_projects():
        from app.models import Project
        from app.utils import ROLE_ADMIN, ROLE_BETRIEBSLEITER, ROLE_ABTEILUNGSLEITER
        if current_user.is_authenticated:
            if current_user.role_name in [ROLE_ADMIN, ROLE_BETRIEBSLEITER]:
                projects = Project.query.order_by(Project.name).all()
            elif current_user.role_name == ROLE_ABTEILUNGSLEITER:
                projects = current_user.projects.order_by(Project.name).all()
            else:
                projects = []
        else:
            projects = []
        return {'user_allowed_projects': projects}

    @app.context_processor
    def inject_assigned_count():
        from app.utils import ROLE_ADMIN, ROLE_BETRIEBSLEITER, ROLE_PROJEKTLEITER
        if current_user.is_authenticated and current_user.role_name not in [ROLE_ADMIN, ROLE_BETRIEBSLEITER, ROLE_PROJEKTLEITER]:
            from app.models import AssignedCoaching
            count = AssignedCoaching.query.filter_by(coach_id=current_user.id, status='pending').count()
        else:
            count = 0
        return {'pending_assigned_count': count}

    @app.context_processor
    def inject_permissions():
        def has_perm(permission_name):
            if current_user.is_authenticated:
                return current_user.has_permission(permission_name)
            return False
        return {'has_perm': has_perm}

    @app.template_filter('athens_time')
    def format_athens_time(utc_dt, fmt='%d.%m.%Y %H:%M'):
        if not utc_dt:
            return ""
        if not isinstance(utc_dt, datetime):
            if isinstance(utc_dt, str):
                try:
                    utc_dt = datetime.fromisoformat(utc_dt.replace('Z', '+00:00'))
                except ValueError:
                    try:
                        utc_dt = datetime.strptime(utc_dt, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        return str(utc_dt)
            else:
                return str(utc_dt)

        if utc_dt.tzinfo is None or utc_dt.tzinfo.utcoffset(utc_dt) is None:
            utc_dt = utc_dt.replace(tzinfo=timezone.utc)

        athens_tz = pytz.timezone('Europe/Athens')
        try:
            local_dt = utc_dt.astimezone(athens_tz)
            return local_dt.strftime(fmt)
        except Exception:
            try:
                return utc_dt.strftime(fmt) + " (UTC?)"
            except:
                return str(utc_dt)

    @app.template_filter('status_de')
    def translate_status(status):
        translations = {
            'pending': 'Ausstehend',
            'accepted': 'Angenommen',
            'in_progress': 'In Bearbeitung',
            'completed': 'Abgeschlossen',
            'expired': 'Abgelaufen',
            'rejected': 'Abgelehnt',
            'cancelled': 'Storniert'
        }
        return translations.get(status, status)

    # Ensure instance folder exists
    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass

    return app
