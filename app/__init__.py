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

        # Create all tables if they don't exist
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

        # 6. Insert default permissions and roles (simplified – your existing code can be kept)
        # (I'm omitting the full permission/role setup for brevity, but you must keep your existing one)
        # Insert default permissions and roles here if not exist – as in your original __init__.py

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
                conn.execute(text("INSERT INTO permissions (name, description) VALUES ('view_own_coachings', 'View own coachings')"))
                print("✅ Permission 'view_own_coachings' hinzugefügt.")
            else:
                print("✅ Permission 'view_own_coachings' existiert bereits.")

        # 10. Role 'Mitarbeiter'
        if 'roles' in inspector.get_table_names():
            res = conn.execute(text("SELECT id FROM roles WHERE name = 'Mitarbeiter'")).fetchone()
            if not res:
                conn.execute(text("INSERT INTO roles (name, description) VALUES ('Mitarbeiter', 'Team member with limited access')"))
                role_id = conn.execute(text("SELECT id FROM roles WHERE name = 'Mitarbeiter'")).fetchone()[0]
                perm_id = conn.execute(text("SELECT id FROM permissions WHERE name = 'view_own_coachings'")).fetchone()[0]
                conn.execute(text("INSERT INTO role_permissions (role_id, permission_id) VALUES (:role_id, :perm_id)"), {"role_id": role_id, "perm_id": perm_id})
                print("✅ Rolle 'Mitarbeiter' mit Berechtigung 'view_own_coachings' hinzugefügt.")
            else:
                print("✅ Rolle 'Mitarbeiter' existiert bereits.")

        print("--- Migration abgeschlossen ---")

    # Register blueprints
    from app.auth import bp as auth_bp
    app.register_blueprint(auth_bp, url_prefix='/auth')

    from app.main_routes import bp as main_bp
    app.register_blueprint(main_bp)

    from app.admin import bp as admin_bp
    app.register_blueprint(admin_bp, url_prefix='/admin')

    # Context processors (shortened – add your full ones)
    @app.context_processor
    def inject_current_year():
        return {'current_year': datetime.utcnow().year}

    @app.context_processor
    def inject_permissions():
        def has_perm(permission_name):
            if current_user.is_authenticated:
                return current_user.has_permission(permission_name)
            return False
        return {'has_perm': has_perm}

    # Additional context processors (projects, etc.) – keep your existing ones
    # (I've omitted them for brevity, but you should keep the ones from your original __init__.py)

    return app
