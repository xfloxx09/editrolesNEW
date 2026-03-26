# app/__init__.py (with fix to assign default roles to users with NULL role_id AND invalid role_id)
print("<<<< START __init__.py wird GELADEN >>>>")

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, current_user
from flask_migrate import Migrate
from config import Config
import os
from datetime import datetime, timezone
import pytz
from sqlalchemy import inspect, text

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = "Bitte melden Sie sich an, um auf diese Seite zuzugreifen."
login_manager.login_message_category = "info"

migrate = Migrate()

def create_app(config_class=Config):
    print("<<<< create_app() WIRD AUFGERUFEN (__init__.py) >>>>")
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    print("<<<< db.init_app() VORBEI (__init__.py) >>>>")
    login_manager.init_app(app)
    migrate.init_app(app, db)

    # --- Migration: ensure necessary columns and tables exist ---
    with app.app_context():
        print("--- Running automatic migrations ---")
        inspector = inspect(db.engine)
        conn = db.engine.connect()

        # 1. coachings.team_id
        columns_coachings = [col['name'] for col in inspector.get_columns('coachings')]
        if 'team_id' not in columns_coachings:
            print("⚠️ Spalte 'team_id' in coachings fehlt – wird hinzugefügt...")
            conn.execute(text('ALTER TABLE coachings ADD COLUMN team_id INTEGER REFERENCES teams(id)'))
            conn.commit()
            print("✅ Spalte 'team_id' in coachings hinzugefügt.")
        else:
            print("✅ Spalte 'team_id' in coachings existiert bereits.")

        # 2. Für bestehende Coachings die team_id nachtragen (falls NULL)
        conn.execute(text('''
            UPDATE coachings
            SET team_id = team_members.team_id
            FROM team_members
            WHERE coachings.team_member_id = team_members.id
            AND coachings.team_id IS NULL
        '''))
        conn.commit()
        print("ℹ️ Bestehende Coachings mit team_id aktualisiert.")

        # 3. Prüfen und Hinzufügen von workshop_participants.original_team_id
        if 'workshop_participants' in inspector.get_table_names():
            columns_wp = [col['name'] for col in inspector.get_columns('workshop_participants')]
            if 'original_team_id' not in columns_wp:
                print("⚠️ Spalte 'original_team_id' in workshop_participants fehlt – wird hinzugefügt...")
                conn.execute(text('ALTER TABLE workshop_participants ADD COLUMN original_team_id INTEGER REFERENCES teams(id)'))
                conn.commit()
                print("✅ Spalte 'original_team_id' in workshop_participants hinzugefügt.")
            else:
                print("✅ Spalte 'original_team_id' in workshop_participants existiert bereits.")

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
            print("✅ Tabelle 'workshop_participants' existiert noch nicht, später automatisch.")

        # 4. user_projects table
        if 'user_projects' not in inspector.get_table_names():
            print("⚠️ Tabelle 'user_projects' fehlt – wird erstellt...")
            conn.execute(text('''
                CREATE TABLE user_projects (
                    user_id INTEGER NOT NULL,
                    project_id INTEGER NOT NULL,
                    PRIMARY KEY (user_id, project_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                )
            '''))
            conn.commit()
            print("✅ Tabelle 'user_projects' erstellt.")

            # Für alle bestehenden Abteilungsleiter die Zuordnung zum aktuellen Projekt eintragen
            columns_users = [col['name'] for col in inspector.get_columns('users')]
            if 'role' in columns_users:
                res = conn.execute(text("SELECT id, project_id FROM users WHERE role = 'Abteilungsleiter' AND project_id IS NOT NULL"))
                rows = res.fetchall()
                for user_id, project_id in rows:
                    conn.execute(
                        text("INSERT INTO user_projects (user_id, project_id) VALUES (:user_id, :project_id)"),
                        {"user_id": user_id, "project_id": project_id}
                    )
                conn.commit()
                print(f"ℹ️ {len(rows)} Abteilungsleiter-Zuordnungen in user_projects eingetragen.")
            else:
                print("ℹ️ Alte Spalte 'role' existiert nicht mehr, überspringe Migration von Abteilungsleitern.")
        else:
            print("✅ Tabelle 'user_projects' existiert bereits.")

        # 5. Fehlende Zuordnungen nachholen (nur wenn 'role' Spalte noch existiert)
        columns_users = [col['name'] for col in inspector.get_columns('users')]
        if 'role' in columns_users:
            res = conn.execute(text("""
                SELECT u.id, u.project_id
                FROM users u
                WHERE u.role = 'Abteilungsleiter'
                  AND u.project_id IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM user_projects up WHERE up.user_id = u.id AND up.project_id = u.project_id)
            """))
            rows = res.fetchall()
            for user_id, project_id in rows:
                conn.execute(
                    text("INSERT INTO user_projects (user_id, project_id) VALUES (:user_id, :project_id)"),
                    {"user_id": user_id, "project_id": project_id}
                )
            conn.commit()
            if rows:
                print(f"ℹ️ {len(rows)} zusätzliche Abteilungsleiter-Zuordnungen in user_projects nachgetragen.")
        else:
            print("ℹ️ Alte Spalte 'role' existiert nicht mehr, überspringe zusätzliche Migration von Abteilungsleitern.")

        # ========== assigned_coachings table ==========
        # 6. Create assigned_coachings table if not exists
        if 'assigned_coachings' not in inspector.get_table_names():
            print("⚠️ Tabelle 'assigned_coachings' fehlt – wird erstellt...")
            conn.execute(text('''
                CREATE TABLE assigned_coachings (
                    id SERIAL NOT NULL,
                    project_leader_id INTEGER NOT NULL,
                    coach_id INTEGER NOT NULL,
                    team_member_id INTEGER NOT NULL,
                    deadline TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                    expected_coaching_count INTEGER NOT NULL,
                    desired_performance_note INTEGER,
                    current_performance_note_at_assign FLOAT,
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (id),
                    FOREIGN KEY(project_leader_id) REFERENCES users(id),
                    FOREIGN KEY(coach_id) REFERENCES users(id),
                    FOREIGN KEY(team_member_id) REFERENCES team_members(id)
                )
            '''))
            conn.commit()
            print("✅ Tabelle 'assigned_coachings' erstellt.")

            # Create index on status
            conn.execute(text('CREATE INDEX ix_assigned_coachings_status ON assigned_coachings (status)'))
            conn.commit()
        else:
            print("✅ Tabelle 'assigned_coachings' existiert bereits.")
            # Ensure id column has auto-increment (if table was created without SERIAL)
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

        # 7. Add assigned_coaching_id to coachings table if not exists
        columns_coachings = [col['name'] for col in inspector.get_columns('coachings')]
        if 'assigned_coaching_id' not in columns_coachings:
            print("⚠️ Spalte 'assigned_coaching_id' in coachings fehlt – wird hinzugefügt...")
            conn.execute(text('ALTER TABLE coachings ADD COLUMN assigned_coaching_id INTEGER REFERENCES assigned_coachings(id)'))
            conn.commit()
            print("✅ Spalte 'assigned_coaching_id' in coachings hinzugefügt.")
        else:
            print("✅ Spalte 'assigned_coaching_id' in coachings existiert bereits.")

        # ========== NEW: roles and permissions ==========
        # 8. Create permissions table
        if 'permissions' not in inspector.get_table_names():
            print("⚠️ Tabelle 'permissions' fehlt – wird erstellt...")
            conn.execute(text('''
                CREATE TABLE permissions (
                    id SERIAL NOT NULL,
                    name VARCHAR(100) NOT NULL UNIQUE,
                    description VARCHAR(255),
                    PRIMARY KEY (id)
                )
            '''))
            conn.commit()
            print("✅ Tabelle 'permissions' erstellt.")
        else:
            print("✅ Tabelle 'permissions' existiert bereits.")

        # 9. Create roles table
        if 'roles' not in inspector.get_table_names():
            print("⚠️ Tabelle 'roles' fehlt – wird erstellt...")
            conn.execute(text('''
                CREATE TABLE roles (
                    id SERIAL NOT NULL,
                    name VARCHAR(100) NOT NULL UNIQUE,
                    description VARCHAR(255),
                    PRIMARY KEY (id)
                )
            '''))
            conn.commit()
            print("✅ Tabelle 'roles' erstellt.")
        else:
            print("✅ Tabelle 'roles' existiert bereits.")

        # 10. Create role_permissions junction table
        if 'role_permissions' not in inspector.get_table_names():
            print("⚠️ Tabelle 'role_permissions' fehlt – wird erstellt...")
            conn.execute(text('''
                CREATE TABLE role_permissions (
                    role_id INTEGER NOT NULL,
                    permission_id INTEGER NOT NULL,
                    PRIMARY KEY (role_id, permission_id),
                    FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE,
                    FOREIGN KEY (permission_id) REFERENCES permissions(id) ON DELETE CASCADE
                )
            '''))
            conn.commit()
            print("✅ Tabelle 'role_permissions' erstellt.")
        else:
            print("✅ Tabelle 'role_permissions' existiert bereits.")

        # 11. Create role_projects junction table
        if 'role_projects' not in inspector.get_table_names():
            print("⚠️ Tabelle 'role_projects' fehlt – wird erstellt...")
            conn.execute(text('''
                CREATE TABLE role_projects (
                    role_id INTEGER NOT NULL,
                    project_id INTEGER NOT NULL,
                    PRIMARY KEY (role_id, project_id),
                    FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                )
            '''))
            conn.commit()
            print("✅ Tabelle 'role_projects' erstellt.")
        else:
            print("✅ Tabelle 'role_projects' existiert bereits.")

        # 12. Add role_id to users table
        columns_users = [col['name'] for col in inspector.get_columns('users')]
        if 'role_id' not in columns_users:
            print("⚠️ Spalte 'role_id' in users fehlt – wird hinzugefügt...")
            conn.execute(text('ALTER TABLE users ADD COLUMN role_id INTEGER REFERENCES roles(id)'))
            conn.commit()
            print("✅ Spalte 'role_id' in users hinzugefügt.")
        else:
            print("✅ Spalte 'role_id' in users existiert bereits.")

        # 13. Insert default permissions if they don't exist
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

        # 14. Insert default roles if they don't exist
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

        # 15. Assign permissions to roles
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

        # Betriebsleiter: same as admin for now
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

        # Projektleiter
        projleiter_role = conn.execute(text("SELECT id FROM roles WHERE name = 'Projektleiter'")).fetchone()
        if projleiter_role:
            pl_id = projleiter_role[0]
            pl_perms = ['view_coaching_dashboard', 'view_workshop_dashboard', 'view_assigned_coachings', 'create_assigned_coaching', 'cancel_assigned_coaching', 'view_assigned_coaching_report', 'view_pl_qm_dashboard', 'set_project']
            for perm_name in pl_perms:
                perm_id = perm_map.get(perm_name)
                if perm_id:
                    exists = conn.execute(
                        text("SELECT 1 FROM role_permissions WHERE role_id = :role_id AND permission_id = :perm_id"),
                        {"role_id": pl_id, "perm_id": perm_id}
                    ).fetchone()
                    if not exists:
                        conn.execute(
                            text("INSERT INTO role_permissions (role_id, permission_id) VALUES (:role_id, :perm_id)"),
                            {"role_id": pl_id, "perm_id": perm_id}
                        )
            print("✅ Projektleiter Berechtigungen gesetzt.")

        # Teamleiter
        teamleiter_role = conn.execute(text("SELECT id FROM roles WHERE name = 'Teamleiter'")).fetchone()
        if teamleiter_role:
            tl_id = teamleiter_role[0]
            tl_perms = ['view_coaching_dashboard', 'view_workshop_dashboard', 'view_assigned_coachings', 'accept_assigned_coaching', 'reject_assigned_coaching', 'add_coaching', 'edit_coaching', 'add_workshop', 'edit_workshop', 'view_team_view', 'coach']
            for perm_name in tl_perms:
                perm_id = perm_map.get(perm_name)
                if perm_id:
                    exists = conn.execute(
                        text("SELECT 1 FROM role_permissions WHERE role_id = :role_id AND permission_id = :perm_id"),
                        {"role_id": tl_id, "perm_id": perm_id}
                    ).fetchone()
                    if not exists:
                        conn.execute(
                            text("INSERT INTO role_permissions (role_id, permission_id) VALUES (:role_id, :perm_id)"),
                            {"role_id": tl_id, "perm_id": perm_id}
                        )
            print("✅ Teamleiter Berechtigungen gesetzt.")

        # Qualitätsmanager
        qm_role = conn.execute(text("SELECT id FROM roles WHERE name = 'Qualitätsmanager'")).fetchone()
        if qm_role:
            qm_id = qm_role[0]
            qm_perms = ['view_coaching_dashboard', 'view_workshop_dashboard', 'view_assigned_coachings', 'accept_assigned_coaching', 'reject_assigned_coaching', 'add_coaching', 'edit_coaching', 'add_workshop', 'edit_workshop', 'view_pl_qm_dashboard', 'coach']
            for perm_name in qm_perms:
                perm_id = perm_map.get(perm_name)
                if perm_id:
                    exists = conn.execute(
                        text("SELECT 1 FROM role_permissions WHERE role_id = :role_id AND permission_id = :perm_id"),
                        {"role_id": qm_id, "perm_id": perm_id}
                    ).fetchone()
                    if not exists:
                        conn.execute(
                            text("INSERT INTO role_permissions (role_id, permission_id) VALUES (:role_id, :perm_id)"),
                            {"role_id": qm_id, "perm_id": perm_id}
                        )
            print("✅ Qualitätsmanager Berechtigungen gesetzt.")

        # SalesCoach, Trainer
        sales_role = conn.execute(text("SELECT id FROM roles WHERE name = 'SalesCoach'")).fetchone()
        trainer_role = conn.execute(text("SELECT id FROM roles WHERE name = 'Trainer'")).fetchone()
        coach_perms = ['view_coaching_dashboard', 'view_workshop_dashboard', 'view_assigned_coachings', 'accept_assigned_coaching', 'reject_assigned_coaching', 'add_coaching', 'edit_coaching', 'add_workshop', 'edit_workshop', 'coach']
        for role_id in [sales_role[0] if sales_role else None, trainer_role[0] if trainer_role else None]:
            if role_id:
                for perm_name in coach_perms:
                    perm_id = perm_map.get(perm_name)
                    if perm_id:
                        exists = conn.execute(
                            text("SELECT 1 FROM role_permissions WHERE role_id = :role_id AND permission_id = :perm_id"),
                            {"role_id": role_id, "perm_id": perm_id}
                        ).fetchone()
                        if not exists:
                            conn.execute(
                                text("INSERT INTO role_permissions (role_id, permission_id) VALUES (:role_id, :perm_id)"),
                                {"role_id": role_id, "perm_id": perm_id}
                            )
        print("✅ SalesCoach/Trainer Berechtigungen gesetzt.")

        # Abteilungsleiter
        abt_role = conn.execute(text("SELECT id FROM roles WHERE name = 'Abteilungsleiter'")).fetchone()
        if abt_role:
            abt_id = abt_role[0]
            abt_perms = ['view_coaching_dashboard', 'view_workshop_dashboard', 'view_assigned_coachings', 'create_assigned_coaching', 'cancel_assigned_coaching', 'view_assigned_coaching_report', 'view_pl_qm_dashboard', 'set_project']
            for perm_name in abt_perms:
                perm_id = perm_map.get(perm_name)
                if perm_id:
                    exists = conn.execute(
                        text("SELECT 1 FROM role_permissions WHERE role_id = :role_id AND permission_id = :perm_id"),
                        {"role_id": abt_id, "perm_id": perm_id}
                    ).fetchone()
                    if not exists:
                        conn.execute(
                            text("INSERT INTO role_permissions (role_id, permission_id) VALUES (:role_id, :perm_id)"),
                            {"role_id": abt_id, "perm_id": perm_id}
                        )
            print("✅ Abteilungsleiter Berechtigungen gesetzt.")

        # 16. Ensure all users have a role_id (assign default if NULL)
        users_without_role = conn.execute(text("SELECT id FROM users WHERE role_id IS NULL")).fetchall()
        if users_without_role:
            print(f"⚠️ {len(users_without_role)} Benutzer ohne Rolle gefunden. Setze Standardrolle 'Teamleiter'...")
            default_role = conn.execute(text("SELECT id FROM roles WHERE name = 'Teamleiter'")).fetchone()
            if default_role:
                for user in users_without_role:
                    conn.execute(
                        text("UPDATE users SET role_id = :role_id WHERE id = :user_id"),
                        {"role_id": default_role[0], "user_id": user[0]}
                    )
                conn.commit()
                print(f"✅ {len(users_without_role)} Benutzern wurde die Rolle 'Teamleiter' zugewiesen.")
            else:
                print("❌ Standardrolle 'Teamleiter' nicht gefunden.")
        else:
            print("✅ Alle Benutzer haben bereits eine Rolle.")

        # 16.5. Ensure all role_id values are valid (point to existing roles)
        valid_role_ids = [row[0] for row in conn.execute(text("SELECT id FROM roles")).fetchall()]
        if valid_role_ids:
            # Find users with role_id not in valid_role_ids
            # Use text with bindparams because we need to pass a tuple
            # We'll construct the query with the tuple directly
            placeholders = ','.join(['?'] * len(valid_role_ids))
            query = text(f"SELECT id, role_id FROM users WHERE role_id IS NOT NULL AND role_id NOT IN ({placeholders})")
            # Execute with the tuple
            invalid_users = conn.execute(query, valid_role_ids).fetchall()
            if invalid_users:
                print(f"⚠️ {len(invalid_users)} Benutzer haben ungültige role_id. Setze Standardrolle 'Teamleiter'...")
                default_role = conn.execute(text("SELECT id FROM roles WHERE name = 'Teamleiter'")).fetchone()
                if default_role:
                    for user_id, old_role_id in invalid_users:
                        conn.execute(
                            text("UPDATE users SET role_id = :new_role_id WHERE id = :user_id"),
                            {"new_role_id": default_role[0], "user_id": user_id}
                        )
                    conn.commit()
                    print(f"✅ {len(invalid_users)} Benutzer korrigiert.")
                else:
                    print("❌ Standardrolle 'Teamleiter' nicht gefunden.")
            else:
                print("✅ Alle Benutzer haben gültige role_id.")
        else:
            print("⚠️ Keine Rollen in der Datenbank gefunden.")

        # 17. Migrate existing users to role_id (only if 'role' column still exists)
        columns_users = [col['name'] for col in inspector.get_columns('users')]
        if 'role' in columns_users:
            users_to_migrate = conn.execute(text("SELECT id, role FROM users WHERE role_id IS NULL")).fetchall()
            if users_to_migrate:
                print(f"⚠️ {len(users_to_migrate)} Benutzer ohne Rolle gefunden – wird migriert...")
                for user_id, old_role in users_to_migrate:
                    role_name = old_role
                    role_id = conn.execute(text("SELECT id FROM roles WHERE name = :name"), {"name": role_name}).fetchone()
                    if role_id:
                        conn.execute(
                            text("UPDATE users SET role_id = :role_id WHERE id = :user_id"),
                            {"role_id": role_id[0], "user_id": user_id}
                        )
                        print(f"✅ Benutzer ID {user_id} zu Rolle '{role_name}' zugewiesen.")
                    else:
                        print(f"⚠️ Keine Rolle für '{old_role}' gefunden, Benutzer ID {user_id} wird auf Standard gesetzt.")
                        default_role = conn.execute(text("SELECT id FROM roles WHERE name = 'Teamleiter'")).fetchone()
                        if default_role:
                            conn.execute(
                                text("UPDATE users SET role_id = :role_id WHERE id = :user_id"),
                                {"role_id": default_role[0], "user_id": user_id}
                            )
                conn.commit()
                print("✅ Migration der Benutzer abgeschlossen.")
            else:
                print("✅ Alle Benutzer haben bereits eine Rolle.")
        else:
            print("✅ Alte Spalte 'role' existiert nicht mehr, überspringe Benutzermigration.")

        # 18. Drop old 'role' column if it exists
        columns_users = [col['name'] for col in inspector.get_columns('users')]
        if 'role' in columns_users:
            print("⚠️ Alte Spalte 'role' wird gelöscht...")
            conn.execute(text('ALTER TABLE users DROP COLUMN role'))
            conn.commit()
            print("✅ Alte Spalte 'role' gelöscht.")
        else:
            print("✅ Alte Spalte 'role' existiert nicht mehr.")

        print("--- Migration abgeschlossen ---")

    # Blueprints registrieren
    from app.auth import bp as auth_bp
    app.register_blueprint(auth_bp, url_prefix='/auth')
    print("<<<< auth_bp REGISTRIERT (__init__.py) >>>>")

    from app.main_routes import bp as main_bp 
    app.register_blueprint(main_bp)
    print("<<<< main_bp REGISTRIERT (__init__.py) >>>>")

    from app.admin import bp as admin_bp
    app.register_blueprint(admin_bp, url_prefix='/admin')
    print("<<<< admin_bp REGISTRIERT (__init__.py) >>>>")
    
    # Kontextprozessor für globale Variablen in Templates (z.B. aktuelles Jahr)
    @app.context_processor
    def inject_current_year():
        return {'current_year': datetime.utcnow().year}

    # Kontextprozessor für erlaubte Projekte (für Projektwechsler in der Navbar)
    @app.context_processor
    def inject_user_allowed_projects():
        from app.models import Project
        from app.roles import ROLE_ADMIN, ROLE_BETRIEBSLEITER, ROLE_ABTEILUNGSLEITER
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

    # Kontextprozessor für die Anzahl ausstehender zugewiesener Coachings (für Badge)
    @app.context_processor
    def inject_assigned_count():
        from app.roles import ROLE_ADMIN, ROLE_BETRIEBSLEITER, ROLE_PROJEKTLEITER
        if current_user.is_authenticated and current_user.role_name not in [ROLE_ADMIN, ROLE_BETRIEBSLEITER, ROLE_PROJEKTLEITER]:
            from app.models import AssignedCoaching
            count = AssignedCoaching.query.filter_by(coach_id=current_user.id, status='pending').count()
        else:
            count = 0
        return {'pending_assigned_count': count}

    # Kontextprozessor für Berechtigungsprüfungen in Templates
    @app.context_processor
    def inject_permissions():
        def has_perm(permission_name):
            if current_user.is_authenticated:
                return current_user.has_permission(permission_name)
            return False
        return {'has_perm': has_perm}

    # Benutzerdefinierter Jinja-Filter für Athener Zeit
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
        except Exception as e:
            try:
                return utc_dt.strftime(fmt) + " (UTC?)" 
            except:
                return str(utc_dt)

    # Filter für Status-Übersetzung
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

    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass
    
    print("<<<< VOR Import von app.models in create_app (__init__.py) >>>>")
    from app import models
    print("<<<< NACH Import von app.models in create_app (__init__.py) >>>>")

    print("<<<< create_app() FERTIG, app wird zurückgegeben (__init__.py) >>>>")
    return app

print("<<<< VOR globalem Import von app.models am Ende von __init__.py >>>>")
from app import models 
print("<<<< NACH globalem Import von app.models am Ende von __init__.py >>>>")

print("<<<< ENDE __init__.py wurde GELADEN >>>>")
