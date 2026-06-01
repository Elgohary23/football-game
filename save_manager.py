import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

SAVES_DIR = Path("saves")
TEMPLATE_DB = SAVES_DIR / "template.db"
MAX_SLOTS = 5


def ensure_saves_dir():
    """Ensure saves directory exists."""
    SAVES_DIR.mkdir(exist_ok=True)


def get_slot_path(slot: int) -> Path:
    """Get path for a save slot."""
    return SAVES_DIR / f"save{slot}.sqlite3"


def list_save_slots() -> list:
    """List all save slots with metadata."""
    ensure_saves_dir()
    slots = []
    for i in range(1, MAX_SLOTS + 1):
        path = get_slot_path(i)
        if path.exists():
            meta = get_save_metadata(i)
            slots.append({"slot": i, "path": path, "exists": True, **meta})
        else:
            slots.append({"slot": i, "path": path, "exists": False, "name": f"Slot {i}", "day": 0, "week": 1})
    return slots


def get_save_metadata(slot: int) -> dict:
    """Extract metadata from a save file."""
    path = get_slot_path(slot)
    if not path.exists():
        return {"name": f"Slot {slot}", "day": 0, "week": 1}
    
    try:
        conn = sqlite3.connect(str(path))
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='game_state'")
        if not cursor.fetchone():
            conn.close()
            return {"name": f"Slot {slot}", "day": 0, "week": 1}
        
        cursor.execute("SELECT current_day, current_week, season_over FROM game_state LIMIT 1")
        row = cursor.fetchone()
        
        cursor.execute("SELECT name FROM team WHERE is_player_team = 1 LIMIT 1")
        team_row = cursor.fetchone()
        
        cursor.execute("SELECT name FROM league WHERE tier = 1 LIMIT 1")
        league_row = cursor.fetchone()
        
        conn.close()
        
        if row:
            return {
                "name": team_row[0] if team_row else f"Slot {slot}",
                "day": row[0] or 0,
                "week": row[1] or 1,
                "season_over": row[2] or False
            }
    except Exception:
        pass
    
    return {"name": f"Slot {slot}", "day": 0, "week": 1}


def create_template_db() -> None:
    """Create template database with base data."""
    ensure_saves_dir()
    
    if TEMPLATE_DB.exists():
        return
    
    conn = sqlite3.connect(str(TEMPLATE_DB))
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS league (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE,
            tier INTEGER,
            reputation INTEGER
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS team (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE,
            morale INTEGER DEFAULT 70,
            budget INTEGER DEFAULT 1000000,
            reputation INTEGER DEFAULT 50,
            training_facilities INTEGER DEFAULT 3,
            youth_facilities INTEGER DEFAULT 3,
            points INTEGER DEFAULT 0,
            goals_for INTEGER DEFAULT 0,
            goals_against INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            draws INTEGER DEFAULT 0,
            is_player_team INTEGER DEFAULT 0,
            league_id INTEGER,
            FOREIGN KEY (league_id) REFERENCES league(id)
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS player (
            id INTEGER PRIMARY KEY,
            name TEXT,
            position TEXT,
            attack INTEGER,
            defense INTEGER,
            potential INTEGER,
            age INTEGER DEFAULT 20,
            fitness INTEGER DEFAULT 100,
            injury_days INTEGER DEFAULT 0,
            personality TEXT DEFAULT 'Normal',
            region TEXT DEFAULT 'Europe',
            height INTEGER,
            weight INTEGER,
            market_value INTEGER DEFAULT 100000,
            is_starting INTEGER DEFAULT 0,
            goals INTEGER DEFAULT 0,
            assists INTEGER DEFAULT 0,
            matches_played INTEGER DEFAULT 0,
            team_id INTEGER,
            FOREIGN KEY (team_id) REFERENCES team(id)
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS game_state (
            id INTEGER PRIMARY KEY,
            current_day INTEGER DEFAULT 1,
            current_week INTEGER DEFAULT 1,
            season_over INTEGER DEFAULT 0,
            user_league_id INTEGER
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS match (
            id INTEGER PRIMARY KEY,
            home_team_id INTEGER,
            away_team_id INTEGER,
            week INTEGER,
            played INTEGER DEFAULT 0,
            result TEXT,
            home_score INTEGER DEFAULT 0,
            away_score INTEGER DEFAULT 0,
            home_events TEXT,
            away_events TEXT,
            home_possession INTEGER DEFAULT 50,
            away_possession INTEGER DEFAULT 50,
            FOREIGN KEY (home_team_id) REFERENCES team(id),
            FOREIGN KEY (away_team_id) REFERENCES team(id)
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS message (
            id INTEGER PRIMARY KEY,
            title TEXT,
            content TEXT,
            type TEXT,
            read INTEGER DEFAULT 0,
            created_day INTEGER,
            urgent INTEGER DEFAULT 0
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS player_rating (
            id INTEGER PRIMARY KEY,
            player_id INTEGER,
            match_id INTEGER,
            rating REAL,
            goals INTEGER DEFAULT 0,
            assists INTEGER DEFAULT 0,
            FOREIGN KEY (player_id) REFERENCES player(id),
            FOREIGN KEY (match_id) REFERENCES match(id)
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transfer_offer (
            id INTEGER PRIMARY KEY,
            player_id INTEGER,
            from_team_id INTEGER,
            to_team_id INTEGER,
            amount INTEGER,
            status TEXT DEFAULT 'pending',
            created_day INTEGER,
            deadline_day INTEGER,
            FOREIGN KEY (player_id) REFERENCES player(id),
            FOREIGN KEY (from_team_id) REFERENCES team(id),
            FOREIGN KEY (to_team_id) REFERENCES team(id)
        )
    """)
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_team_id ON player(team_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_position_attack_age ON player(position, attack, age)")
    
    conn.commit()
    conn.close()


def copy_template_to_slot(slot: int) -> bool:
    """Copy template to a new save slot."""
    ensure_saves_dir()
    create_template_db()
    
    dest = get_slot_path(slot)
    if dest.exists():
        return False
    
    try:
        shutil.copy2(TEMPLATE_DB, dest)
        return True
    except Exception:
        return False


def delete_save_slot(slot: int) -> bool:
    """Delete a save slot."""
    path = get_slot_path(slot)
    if path.exists():
        try:
            path.unlink()
            return True
        except Exception:
            return False
    return False


def load_slot_uri(slot: int) -> str:
    """Get URI for a slot."""
    return f"sqlite:///{get_slot_path(slot).as_posix()}"