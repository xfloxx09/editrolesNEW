#!/usr/bin/env python3
"""
Import legacy coachings/workshops from old PostgreSQL dumps into target database.

Workflow:
1) Restore each legacy dump into a temporary PostgreSQL database.
2) Read legacy rows (projects/teams/team members/users/coachings/workshops).
3) Map entities by normalized names to the target schema.
4) Insert mapped rows with duplicate protection.
5) Print an import report (imported/skipped/unmapped).

Default mode is DRY-RUN (transaction rollback). Use --execute to persist changes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Set, Tuple

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor


def normalize_text(value: Optional[str]) -> str:
    if value is None:
        return ""
    cleaned = re.sub(r"\s+", " ", str(value)).strip().lower()
    return cleaned


def normalize_person_name(value: Optional[str]) -> str:
    base = normalize_text(value)
    # Legacy dumps often contain name suffixes like "(archiv)".
    base = re.sub(r"\s*\(archiv\)\s*$", "", base).strip()
    return base


def reverse_person_name_key(value: Optional[str]) -> str:
    key = normalize_person_name(value)
    parts = [p for p in key.split(" ") if p]
    if len(parts) < 2:
        return ""
    return " ".join(reversed(parts))


def relaxed_project_key(value: Optional[str]) -> str:
    base = normalize_text(value)
    return re.sub(r"[^a-z0-9]+", "", base)


def maybe_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass
class DbConn:
    host: str
    port: int
    dbname: str
    user: str
    password: str

    def psycopg2_kwargs(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
            "user": self.user,
            "password": self.password,
        }

    def psql_args(self) -> List[str]:
        return ["-h", self.host, "-p", str(self.port), "-U", self.user, "-d", self.dbname]


@dataclass
class ImportStats:
    source_label: str
    coachings_seen: int = 0
    coachings_imported: int = 0
    coachings_skipped_duplicate: int = 0
    coachings_skipped_unmapped_project: int = 0
    coachings_skipped_unmapped_team: int = 0
    coachings_skipped_unmapped_member: int = 0
    coachings_skipped_unmapped_coach: int = 0
    workshops_seen: int = 0
    workshops_imported: int = 0
    workshops_skipped_duplicate: int = 0
    workshops_skipped_unmapped_project: int = 0
    workshops_skipped_unmapped_coach: int = 0
    created_teams: int = 0
    created_team_members: int = 0
    created_projects: int = 0
    created_coaches: int = 0
    unmapped_samples: Dict[str, Set[str]] = field(
        default_factory=lambda: {
            "projects": set(),
            "teams": set(),
            "members": set(),
            "coaches": set(),
        }
    )

    def as_dict(self) -> dict:
        data = {
            "source": self.source_label,
            "coachings": {
                "seen": self.coachings_seen,
                "imported": self.coachings_imported,
                "skipped_duplicate": self.coachings_skipped_duplicate,
                "skipped_unmapped_project": self.coachings_skipped_unmapped_project,
                "skipped_unmapped_team": self.coachings_skipped_unmapped_team,
                "skipped_unmapped_member": self.coachings_skipped_unmapped_member,
                "skipped_unmapped_coach": self.coachings_skipped_unmapped_coach,
            },
            "workshops": {
                "seen": self.workshops_seen,
                "imported": self.workshops_imported,
                "skipped_duplicate": self.workshops_skipped_duplicate,
                "skipped_unmapped_project": self.workshops_skipped_unmapped_project,
                "skipped_unmapped_coach": self.workshops_skipped_unmapped_coach,
            },
            "created": {
                "projects": self.created_projects,
                "teams": self.created_teams,
                "team_members": self.created_team_members,
                "coaches": self.created_coaches,
            },
            "unmapped_samples": {k: sorted(list(v))[:20] for k, v in self.unmapped_samples.items()},
        }
        return data


def resolve_pg_tools(pg_bin_dir: Optional[str] = None) -> Dict[str, str]:
    bin_dir = pg_bin_dir or os.getenv("PG_BIN_DIR")
    tools: Dict[str, str] = {}

    for tool in ("pg_restore", "psql"):
        candidates: List[str] = []
        if bin_dir:
            candidates.append(os.path.join(bin_dir, f"{tool}.exe"))
            candidates.append(os.path.join(bin_dir, tool))
        from_path = shutil.which(tool)
        if from_path:
            candidates.append(from_path)

        resolved = next((c for c in candidates if c and os.path.exists(c)), None)
        if not resolved:
            extra = (
                " Provide --pg-bin-dir \"C:\\Program Files\\PostgreSQL\\<version>\\bin\" "
                "or set PG_BIN_DIR."
            )
            raise RuntimeError(f"Required tool '{tool}' not found in PATH.{extra}")
        tools[tool] = resolved

    return tools


def run_cmd(cmd: List[str], env: dict, label: str) -> None:
    proc = subprocess.run(cmd, text=True, capture_output=True, env=env)
    if proc.returncode != 0:
        details = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"{label} failed (exit {proc.returncode}): {details}")


def create_temp_db(admin_conn: DbConn, db_name: str) -> None:
    conn = psycopg2.connect(**admin_conn.psycopg2_kwargs())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s"),
                (db_name,),
            )
            cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(db_name)))
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
    finally:
        conn.close()


def drop_temp_db(admin_conn: DbConn, db_name: str) -> None:
    conn = psycopg2.connect(**admin_conn.psycopg2_kwargs())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s"),
                (db_name,),
            )
            cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(db_name)))
    finally:
        conn.close()


def restore_dump_to_db(temp_conn: DbConn, dump_path: str, pg_tools: Dict[str, str]) -> None:
    env = os.environ.copy()
    env["PGPASSWORD"] = temp_conn.password

    pg_restore_cmd = [
        pg_tools["pg_restore"],
        "--no-owner",
        "--no-privileges",
        *temp_conn.psql_args(),
        dump_path,
    ]
    pg_restore_proc = subprocess.run(pg_restore_cmd, text=True, capture_output=True, env=env)
    if pg_restore_proc.returncode == 0:
        return

    stderr = (pg_restore_proc.stderr or "").lower()
    # Fallback for plain SQL dumps.
    if "input file appears to be a text format dump" in stderr or "text format" in stderr:
        run_cmd(
            [pg_tools["psql"], *temp_conn.psql_args(), "-v", "ON_ERROR_STOP=1", "-f", dump_path],
            env,
            "psql restore",
        )
        return

    details = (pg_restore_proc.stderr or pg_restore_proc.stdout or "").strip()
    raise RuntimeError(f"pg_restore failed for '{dump_path}': {details}")


def load_target_maps(conn) -> dict:
    maps = {
        "projects": {},
        "projects_relaxed": {},
        "teams": {},
        "users_by_username": {},
        "users_by_email": {},
        "users_by_member_name": {},
        "team_members": {},
        "team_members_by_project_name": {},
        "member_team_by_id": {},
    }
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, name FROM projects")
        for row in cur.fetchall():
            nkey = normalize_text(row["name"])
            maps["projects"][nkey] = row["id"]
            rkey = relaxed_project_key(row["name"])
            if rkey:
                if rkey in maps["projects_relaxed"] and maps["projects_relaxed"][rkey] != row["id"]:
                    # Ambiguous relaxed key: disable auto-map for this key.
                    maps["projects_relaxed"][rkey] = None
                elif rkey not in maps["projects_relaxed"]:
                    maps["projects_relaxed"][rkey] = row["id"]

        cur.execute("SELECT id, name, project_id FROM teams")
        for row in cur.fetchall():
            key = (row["project_id"], normalize_text(row["name"]))
            maps["teams"][key] = row["id"]

        cur.execute("SELECT id, username, email FROM users")
        for row in cur.fetchall():
            uname = normalize_text(row["username"])
            mail = normalize_text(row["email"])
            if uname:
                maps["users_by_username"][uname] = row["id"]
                # Some legacy systems stored full coach names in username.
                pname = normalize_person_name(row["username"])
                if pname and pname not in maps["users_by_member_name"]:
                    maps["users_by_member_name"][pname] = row["id"]
            if mail:
                maps["users_by_email"][mail] = row["id"]

        cur.execute(
            """
            SELECT DISTINCT tm.name AS member_name, tm.user_id
            FROM team_members tm
            WHERE tm.user_id IS NOT NULL
            """
        )
        for row in cur.fetchall():
            pname = normalize_person_name(row["member_name"])
            if pname and pname not in maps["users_by_member_name"]:
                maps["users_by_member_name"][pname] = row["user_id"]

        cur.execute(
            """
            SELECT tm.id, tm.team_id, tm.name, t.project_id
            FROM team_members tm
            JOIN teams t ON t.id = tm.team_id
            """
        )
        for row in cur.fetchall():
            key = (row["team_id"], normalize_text(row["name"]))
            maps["team_members"][key] = row["id"]
            maps["member_team_by_id"][row["id"]] = row["team_id"]

            pkey = (row["project_id"], normalize_person_name(row["name"]))
            if pkey[1]:
                if pkey in maps["team_members_by_project_name"] and maps["team_members_by_project_name"][pkey] != row["id"]:
                    maps["team_members_by_project_name"][pkey] = None
                elif pkey not in maps["team_members_by_project_name"]:
                    maps["team_members_by_project_name"][pkey] = row["id"]

    return maps


def load_existing_fingerprints(conn) -> Tuple[Set[Tuple], Set[Tuple]]:
    coaching_fps: Set[Tuple] = set()
    workshop_fps: Set[Tuple] = set()

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                team_member_id,
                coach_id,
                coaching_date,
                COALESCE(coaching_style, '') AS coaching_style,
                COALESCE(tcap_id, '') AS tcap_id,
                COALESCE(coaching_subject, '') AS coaching_subject,
                COALESCE(performance_mark, -1) AS performance_mark,
                COALESCE(time_spent, -1) AS time_spent,
                COALESCE(project_id, -1) AS project_id,
                COALESCE(team_id, -1) AS team_id
            FROM coachings
            """
        )
        for row in cur.fetchall():
            coaching_fps.add(
                (
                    row["team_member_id"],
                    row["coach_id"],
                    row["coaching_date"],
                    row["coaching_style"],
                    row["tcap_id"],
                    normalize_text(row["coaching_subject"]),
                    row["performance_mark"],
                    row["time_spent"],
                    row["project_id"],
                    row["team_id"],
                )
            )

        cur.execute(
            """
            SELECT
                COALESCE(title, '') AS title,
                coach_id,
                workshop_date,
                COALESCE(overall_rating, -1) AS overall_rating,
                COALESCE(time_spent, -1) AS time_spent,
                COALESCE(project_id, -1) AS project_id
            FROM workshops
            """
        )
        for row in cur.fetchall():
            workshop_fps.add(
                (
                    normalize_text(row["title"]),
                    row["coach_id"],
                    row["workshop_date"],
                    row["overall_rating"],
                    row["time_spent"],
                    row["project_id"],
                )
            )

    return coaching_fps, workshop_fps


def ensure_project(
    conn,
    maps: dict,
    project_name: str,
    stats: ImportStats,
    create_missing_projects: bool,
    project_aliases: Dict[str, str],
) -> Optional[int]:
    key = normalize_text(project_name)

    # Explicit alias mapping first: OLD_PROJECT_NAME=TARGET_PROJECT_NAME
    alias_target_name = project_aliases.get(key)
    if alias_target_name:
        alias_id = maps["projects"].get(normalize_text(alias_target_name))
        if alias_id:
            return alias_id

    existing = maps["projects"].get(key)
    if existing:
        return existing

    # Try relaxed (punctuation/spacing-insensitive) project matching.
    relaxed = relaxed_project_key(project_name)
    relaxed_match = maps["projects_relaxed"].get(relaxed)
    if relaxed and relaxed_match:
        return relaxed_match

    if not create_missing_projects:
        stats.unmapped_samples["projects"].add(str(project_name))
        return None

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO projects (name, description)
            VALUES (%s, %s)
            RETURNING id
            """,
            (project_name, "Imported from legacy dump"),
        )
        new_id = cur.fetchone()[0]
    maps["projects"][key] = new_id
    stats.created_projects += 1
    return new_id


def ensure_team(
    conn,
    maps: dict,
    team_name: str,
    project_id: int,
    stats: ImportStats,
    create_missing_teams: bool,
) -> Optional[int]:
    key = (project_id, normalize_text(team_name))
    existing = maps["teams"].get(key)
    if existing:
        return existing
    if not create_missing_teams:
        stats.unmapped_samples["teams"].add(f"{team_name} @ project_id={project_id}")
        return None

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO teams (name, project_id, active_for_coaching, visible_for_coaching_assignment)
            VALUES (%s, %s, TRUE, FALSE)
            RETURNING id
            """,
            (team_name, project_id),
        )
        new_id = cur.fetchone()[0]
    maps["teams"][key] = new_id
    stats.created_teams += 1
    return new_id


def ensure_team_member(
    conn,
    maps: dict,
    member_name: str,
    team_id: Optional[int],
    project_id: int,
    stats: ImportStats,
    create_missing_members: bool,
) -> Optional[int]:
    if team_id is not None:
        key = (team_id, normalize_text(member_name))
        existing = maps["team_members"].get(key)
        if existing:
            return existing

    # Fallback: same member name within same project, even if legacy team cannot be mapped.
    pkey = (project_id, normalize_person_name(member_name))
    project_match = maps["team_members_by_project_name"].get(pkey)
    if project_match:
        return project_match

    if not create_missing_members:
        stats.unmapped_samples["members"].add(f"{member_name} @ team_id={team_id}")
        return None

    if team_id is None:
        stats.unmapped_samples["members"].add(f"{member_name} @ team_id=None")
        return None

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO team_members (name, team_id, original_team_id, original_project_id)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (member_name, team_id, team_id, project_id),
        )
        new_id = cur.fetchone()[0]
    maps["team_members"][key] = new_id
    maps["member_team_by_id"][new_id] = team_id
    pkey = (project_id, normalize_person_name(member_name))
    if pkey[1]:
        maps["team_members_by_project_name"][pkey] = new_id
    stats.created_team_members += 1
    return new_id


def map_coach_id(
    maps: dict,
    username: Optional[str],
    email: Optional[str],
    coach_aliases: Dict[str, int],
) -> Optional[int]:
    uname = normalize_text(username)
    mail = normalize_text(email)
    pname = normalize_person_name(username)
    if uname and uname in coach_aliases:
        return coach_aliases[uname]
    if pname and pname in coach_aliases:
        return coach_aliases[pname]
    if uname and uname in maps["users_by_username"]:
        return maps["users_by_username"][uname]
    if mail and mail in maps["users_by_email"]:
        return maps["users_by_email"][mail]
    if pname and pname in maps["users_by_member_name"]:
        return maps["users_by_member_name"][pname]
    reversed_pname = reverse_person_name_key(username)
    if reversed_pname and reversed_pname in maps["users_by_member_name"]:
        return maps["users_by_member_name"][reversed_pname]
    return None


def build_legacy_username(display_name: str, project_id: int) -> str:
    raw = normalize_person_name(display_name) or "legacy coach"
    slug = re.sub(r"[^a-z0-9]+", ".", raw.lower()).strip(".")
    if not slug:
        slug = "legacy.coach"
    username = f"legacy.{slug}.p{project_id}"
    return username[:64]


def ensure_legacy_coach(
    conn,
    maps: dict,
    legacy_label: str,
    project_id: int,
    stats: ImportStats,
) -> int:
    candidate_username = build_legacy_username(legacy_label, project_id)
    existing = maps["users_by_username"].get(normalize_text(candidate_username))
    if existing:
        return existing

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO users (username, email, password_hash, team_id_if_leader, project_id, role_id, abteilung_id)
            VALUES (%s, NULL, NULL, NULL, %s, NULL, NULL)
            RETURNING id, username
            """,
            (candidate_username, project_id),
        )
        row = cur.fetchone()

    user_id = row["id"]
    maps["users_by_username"][normalize_text(row["username"])] = user_id
    label_key = normalize_person_name(legacy_label)
    if label_key and label_key not in maps["users_by_member_name"]:
        maps["users_by_member_name"][label_key] = user_id
    stats.created_coaches += 1
    return user_id


def source_rows(conn, query: str) -> Iterable[dict]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query)
        for row in cur.fetchall():
            yield row


def import_coachings(
    src_conn,
    dst_conn,
    maps: dict,
    coaching_fps: Set[Tuple],
    stats: ImportStats,
    create_missing_projects: bool,
    project_aliases: Dict[str, str],
    coach_aliases: Dict[str, int],
    create_missing_coaches: bool,
    create_missing_teams: bool,
    create_missing_members: bool,
) -> None:
    query = """
        SELECT
            c.*,
            p.name AS project_name,
            t.name AS team_name,
            tm.name AS member_name,
            u.username AS coach_username,
            u.email AS coach_email
        FROM coachings c
        LEFT JOIN projects p ON p.id = c.project_id
        LEFT JOIN teams t ON t.id = c.team_id
        LEFT JOIN team_members tm ON tm.id = c.team_member_id
        LEFT JOIN users u ON u.id = c.coach_id
        ORDER BY c.id
    """

    insert_sql = """
        INSERT INTO coachings (
            team_member_id, coach_id, coaching_date, coaching_style, tcap_id, coaching_subject,
            leitfaden_begruessung, leitfaden_legitimation, leitfaden_pka, leitfaden_kek,
            leitfaden_angebot, leitfaden_zusammenfassung, leitfaden_kzb, performance_mark,
            time_spent, coach_notes, project_leader_notes, project_id, team_id
        ) VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s, %s
        )
    """

    for row in source_rows(src_conn, query):
        stats.coachings_seen += 1
        project_name = row.get("project_name")
        team_name = row.get("team_name")
        member_name = row.get("member_name")

        project_id = ensure_project(
            dst_conn,
            maps,
            project_name,
            stats,
            create_missing_projects,
            project_aliases,
        )
        if not project_id:
            stats.coachings_skipped_unmapped_project += 1
            continue

        mapped_team_id: Optional[int] = None
        if team_name:
            mapped_team_id = ensure_team(dst_conn, maps, team_name, project_id, stats, create_missing_teams)
            if not mapped_team_id:
                stats.coachings_skipped_unmapped_team += 1
        elif create_missing_teams:
            mapped_team_id = ensure_team(
                dst_conn,
                maps,
                "LEGACY_UNASSIGNED",
                project_id,
                stats,
                create_missing_teams=True,
            )

        if not member_name:
            stats.coachings_skipped_unmapped_member += 1
            stats.unmapped_samples["members"].add("(empty member name)")
            continue

        team_member_id = ensure_team_member(
            dst_conn,
            maps,
            member_name,
            mapped_team_id,
            project_id,
            stats,
            create_missing_members,
        )
        if not team_member_id:
            stats.coachings_skipped_unmapped_member += 1
            continue
        if mapped_team_id is None:
            mapped_team_id = maps["member_team_by_id"].get(team_member_id)

        coach_id = map_coach_id(
            maps,
            row.get("coach_username"),
            row.get("coach_email"),
            coach_aliases,
        )
        if not coach_id:
            label = row.get("coach_username") or row.get("coach_email") or "(unknown coach)"
            if create_missing_coaches:
                coach_id = ensure_legacy_coach(dst_conn, maps, str(label), project_id, stats)
            else:
                stats.coachings_skipped_unmapped_coach += 1
                stats.unmapped_samples["coaches"].add(str(label))
                continue

        fp = (
            team_member_id,
            coach_id,
            row.get("coaching_date"),
            row.get("coaching_style") or "",
            row.get("tcap_id") or "",
            normalize_text(row.get("coaching_subject")),
            maybe_int(row.get("performance_mark")) or -1,
            maybe_int(row.get("time_spent")) or -1,
            project_id if project_id is not None else -1,
            mapped_team_id if mapped_team_id is not None else -1,
        )
        if fp in coaching_fps:
            stats.coachings_skipped_duplicate += 1
            continue

        with dst_conn.cursor() as cur:
            cur.execute(
                insert_sql,
                (
                    team_member_id,
                    coach_id,
                    row.get("coaching_date"),
                    row.get("coaching_style"),
                    row.get("tcap_id"),
                    row.get("coaching_subject"),
                    row.get("leitfaden_begruessung"),
                    row.get("leitfaden_legitimation"),
                    row.get("leitfaden_pka"),
                    row.get("leitfaden_kek"),
                    row.get("leitfaden_angebot"),
                    row.get("leitfaden_zusammenfassung"),
                    row.get("leitfaden_kzb"),
                    row.get("performance_mark"),
                    row.get("time_spent"),
                    row.get("coach_notes"),
                    row.get("project_leader_notes"),
                    project_id,
                    mapped_team_id,
                ),
            )

        coaching_fps.add(fp)
        stats.coachings_imported += 1


def import_workshops(
    src_conn,
    dst_conn,
    maps: dict,
    workshop_fps: Set[Tuple],
    stats: ImportStats,
    create_missing_projects: bool,
    project_aliases: Dict[str, str],
    coach_aliases: Dict[str, int],
    create_missing_coaches: bool,
) -> None:
    query = """
        SELECT
            w.*,
            p.name AS project_name,
            u.username AS coach_username,
            u.email AS coach_email
        FROM workshops w
        LEFT JOIN projects p ON p.id = w.project_id
        LEFT JOIN users u ON u.id = w.coach_id
        ORDER BY w.id
    """
    insert_sql = """
        INSERT INTO workshops (title, coach_id, workshop_date, overall_rating, time_spent, notes, project_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """
    participants_sql = """
        SELECT tm.name AS member_name, t.name AS team_name, p.name AS project_name
        FROM workshop_participants wp
        JOIN team_members tm ON tm.id = wp.team_member_id
        LEFT JOIN teams t ON t.id = tm.team_id
        LEFT JOIN projects p ON p.id = t.project_id
        WHERE wp.workshop_id = %s
    """
    insert_participant_sql = """
        INSERT INTO workshop_participants (workshop_id, team_member_id, individual_rating, original_team_id)
        VALUES (%s, %s, NULL, %s)
    """

    for row in source_rows(src_conn, query):
        stats.workshops_seen += 1

        project_id = ensure_project(
            dst_conn,
            maps,
            row.get("project_name"),
            stats,
            create_missing_projects,
            project_aliases,
        )
        if not project_id:
            stats.workshops_skipped_unmapped_project += 1
            continue

        coach_id = map_coach_id(
            maps,
            row.get("coach_username"),
            row.get("coach_email"),
            coach_aliases,
        )
        if not coach_id:
            label = row.get("coach_username") or row.get("coach_email") or "(unknown coach)"
            if create_missing_coaches:
                coach_id = ensure_legacy_coach(dst_conn, maps, str(label), project_id, stats)
            else:
                stats.workshops_skipped_unmapped_coach += 1
                stats.unmapped_samples["coaches"].add(str(label))
                continue

        fp = (
            normalize_text(row.get("title")),
            coach_id,
            row.get("workshop_date"),
            maybe_int(row.get("overall_rating")) or -1,
            maybe_int(row.get("time_spent")) or -1,
            project_id,
        )
        if fp in workshop_fps:
            stats.workshops_skipped_duplicate += 1
            continue

        with dst_conn.cursor() as dst_cur:
            dst_cur.execute(
                insert_sql,
                (
                    row.get("title"),
                    coach_id,
                    row.get("workshop_date"),
                    row.get("overall_rating"),
                    row.get("time_spent"),
                    row.get("notes"),
                    project_id,
                ),
            )
            new_workshop_id = dst_cur.fetchone()[0]

            with src_conn.cursor(cursor_factory=RealDictCursor) as src_cur:
                src_cur.execute(participants_sql, (row["id"],))
                participants = src_cur.fetchall()

            for p in participants:
                p_project_id = maps["projects"].get(normalize_text(p.get("project_name")))
                if not p_project_id:
                    continue
                p_team_id = maps["teams"].get((p_project_id, normalize_text(p.get("team_name"))))
                if not p_team_id:
                    continue
                p_member_id = maps["team_members"].get((p_team_id, normalize_text(p.get("member_name"))))
                if not p_member_id:
                    continue
                dst_cur.execute(insert_participant_sql, (new_workshop_id, p_member_id, p_team_id))

        workshop_fps.add(fp)
        stats.workshops_imported += 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import legacy coachings/workshops into current database")
    parser.add_argument("--old-dumps", nargs="+", required=True, help="Paths to old dump files")
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--target-port", type=int, default=5432)
    parser.add_argument("--target-db", required=True)
    parser.add_argument("--target-user", required=True)
    parser.add_argument(
        "--pg-bin-dir",
        default=os.getenv("PG_BIN_DIR"),
        help="Path to PostgreSQL bin folder (contains pg_restore/psql).",
    )
    parser.add_argument(
        "--target-password",
        default=os.getenv("TARGET_DB_PASSWORD") or os.getenv("PGPASSWORD"),
        help="Prefer env var TARGET_DB_PASSWORD/PGPASSWORD",
    )
    parser.add_argument(
        "--admin-db",
        default="postgres",
        help="Maintenance DB used for CREATE/DROP DATABASE for temporary restores",
    )
    parser.add_argument(
        "--include-workshops",
        action="store_true",
        help="Also import workshops and workshop participants",
    )
    parser.add_argument(
        "--create-missing-projects",
        action="store_true",
        help="Auto-create missing projects in target DB",
    )
    parser.add_argument(
        "--create-missing-teams",
        action="store_true",
        help="Auto-create missing teams in target DB",
    )
    parser.add_argument(
        "--create-missing-team-members",
        action="store_true",
        help="Auto-create missing team members in target DB",
    )
    parser.add_argument(
        "--project-map",
        action="append",
        default=[],
        help="Map old to current project name: OLD_NAME=TARGET_NAME (repeatable)",
    )
    parser.add_argument(
        "--coach-map",
        action="append",
        default=[],
        help="Map old coach label/name to existing target user (username/email/fullname): OLD=TARGET",
    )
    parser.add_argument(
        "--create-missing-coaches",
        action="store_true",
        help="Auto-create placeholder users for unmapped legacy coaches",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Persist changes. Without this flag, dry-run with rollback.",
    )
    return parser.parse_args()


def parse_project_aliases(entries: List[str]) -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    for entry in entries:
        if "=" not in entry:
            raise ValueError(f"Invalid --project-map entry '{entry}'. Expected OLD=TARGET.")
        old_name, new_name = entry.split("=", 1)
        old_name = old_name.strip()
        new_name = new_name.strip()
        if not old_name or not new_name:
            raise ValueError(f"Invalid --project-map entry '{entry}'. Empty old/target name.")
        aliases[normalize_text(old_name)] = new_name
    return aliases


def resolve_user_id(maps: dict, target_user_key: str) -> Optional[int]:
    key = normalize_text(target_user_key)
    pname = normalize_person_name(target_user_key)
    if key in maps["users_by_username"]:
        return maps["users_by_username"][key]
    if key in maps["users_by_email"]:
        return maps["users_by_email"][key]
    if pname in maps["users_by_member_name"]:
        return maps["users_by_member_name"][pname]
    reversed_pname = reverse_person_name_key(target_user_key)
    if reversed_pname in maps["users_by_member_name"]:
        return maps["users_by_member_name"][reversed_pname]
    return None


def parse_coach_aliases(entries: List[str], maps: dict) -> Dict[str, int]:
    aliases: Dict[str, int] = {}
    for entry in entries:
        if "=" not in entry:
            raise ValueError(f"Invalid --coach-map entry '{entry}'. Expected OLD=TARGET.")
        old_name, target_user = entry.split("=", 1)
        old_name = old_name.strip()
        target_user = target_user.strip()
        if not old_name or not target_user:
            raise ValueError(f"Invalid --coach-map entry '{entry}'. Empty old/target value.")
        target_id = resolve_user_id(maps, target_user)
        if not target_id:
            raise ValueError(
                f"--coach-map target '{target_user}' could not be resolved to an existing user."
            )
        aliases[normalize_text(old_name)] = target_id
        aliases[normalize_person_name(old_name)] = target_id
    return aliases


def main() -> int:
    args = parse_args()
    if not args.target_password:
        print("Missing target password. Set --target-password or TARGET_DB_PASSWORD env.", file=sys.stderr)
        return 2

    pg_tools = resolve_pg_tools(args.pg_bin_dir)
    project_aliases = parse_project_aliases(args.project_map)

    target_conn_info = DbConn(
        host=args.target_host,
        port=args.target_port,
        dbname=args.target_db,
        user=args.target_user,
        password=args.target_password,
    )
    admin_conn_info = DbConn(
        host=args.target_host,
        port=args.target_port,
        dbname=args.admin_db,
        user=args.target_user,
        password=args.target_password,
    )

    temp_db_names: List[str] = []
    source_db_infos: List[Tuple[str, DbConn]] = []

    try:
        print("Preparing temporary databases and restoring dumps...")
        for dump_path in args.old_dumps:
            if not os.path.exists(dump_path):
                raise FileNotFoundError(f"Dump not found: {dump_path}")
            temp_name = f"tmp_legacy_{secrets.token_hex(4)}"
            create_temp_db(admin_conn_info, temp_name)
            temp_db_names.append(temp_name)
            temp_conn = DbConn(
                host=args.target_host,
                port=args.target_port,
                dbname=temp_name,
                user=args.target_user,
                password=args.target_password,
            )
            restore_dump_to_db(temp_conn, dump_path, pg_tools)
            source_db_infos.append((os.path.basename(dump_path), temp_conn))
            print(f"  restored: {dump_path} -> {temp_name}")

        dst_conn = psycopg2.connect(**target_conn_info.psycopg2_kwargs())
        dst_conn.autocommit = False
        try:
            maps = load_target_maps(dst_conn)
            coach_aliases = parse_coach_aliases(args.coach_map, maps)
            coaching_fps, workshop_fps = load_existing_fingerprints(dst_conn)

            all_stats: List[ImportStats] = []
            for source_label, source_info in source_db_infos:
                stats = ImportStats(source_label=source_label)
                src_conn = psycopg2.connect(**source_info.psycopg2_kwargs())
                try:
                    import_coachings(
                        src_conn,
                        dst_conn,
                        maps,
                        coaching_fps,
                        stats,
                        create_missing_projects=args.create_missing_projects,
                        project_aliases=project_aliases,
                        coach_aliases=coach_aliases,
                        create_missing_coaches=args.create_missing_coaches,
                        create_missing_teams=args.create_missing_teams,
                        create_missing_members=args.create_missing_team_members,
                    )
                    if args.include_workshops:
                        import_workshops(
                            src_conn,
                            dst_conn,
                            maps,
                            workshop_fps,
                            stats,
                            create_missing_projects=args.create_missing_projects,
                            project_aliases=project_aliases,
                            coach_aliases=coach_aliases,
                            create_missing_coaches=args.create_missing_coaches,
                        )
                finally:
                    src_conn.close()
                all_stats.append(stats)

            if args.execute:
                dst_conn.commit()
                mode = "execute"
            else:
                dst_conn.rollback()
                mode = "dry-run (rolled back)"
        finally:
            dst_conn.close()

        output = {
            "mode": mode,
            "target": {
                "host": args.target_host,
                "port": args.target_port,
                "db": args.target_db,
                "user": args.target_user,
            },
            "include_workshops": bool(args.include_workshops),
            "sources": [s.as_dict() for s in all_stats],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        print(json.dumps(output, indent=2, ensure_ascii=True))
        return 0

    except Exception as exc:  # pylint: disable=broad-except
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        for name in reversed(temp_db_names):
            try:
                drop_temp_db(admin_conn_info, name)
            except Exception as cleanup_exc:  # pylint: disable=broad-except
                print(f"WARN: failed to drop temp db {name}: {cleanup_exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
