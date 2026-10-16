"""Back-fill world_sim_last_week = 0 on existing saves (fix.txt deliverable 8)."""
import os
import sqlite3

BASES = ["saves", "instance"]


def _files():
    out = []
    for base in BASES:
        if not os.path.isdir(base):
            continue
        for fn in os.listdir(base):
            if fn.endswith(".sqlite3") or fn.endswith(".db"):
                out.append(os.path.join(base, fn))
    return out


def _ensure_columns(conn):
    cur = conn.cursor()
    try:
        cur.execute("PRAGMA table_info(game_state)")
        cols = {row[1] for row in cur.fetchall()}
    except Exception:
        return
    if "world_sim_last_week" not in cols:
        try:
            cur.execute("ALTER TABLE game_state ADD COLUMN world_sim_last_week INTEGER DEFAULT 0")
        except Exception:
            pass
    if "world_last_summary" not in cols:
        try:
            cur.execute("ALTER TABLE game_state ADD COLUMN world_last_summary TEXT")
        except Exception:
            pass


def main():
    files = _files()
    if not files:
        print("no db files found")
        return
    for f in files:
        try:
            conn = sqlite3.connect(f)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='game_state'")
            if not cur.fetchone():
                conn.close()
                continue
            _ensure_columns(conn)
            try:
                cur.execute("UPDATE game_state SET world_sim_last_week = 0 WHERE world_sim_last_week IS NULL")
            except Exception:
                pass
            conn.commit()
            conn.close()
            print(f"migrated {f}")
        except Exception as exc:
            print(f"skip {f}: {exc}")


if __name__ == "__main__":
    main()
