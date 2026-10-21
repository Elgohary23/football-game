"""Migrate existing saves to the realistic multi-competition calendar.

Additive only: the script never deletes or recreates a save. It

1. adds ``match.comp_type``, ``match.matchday``, ``game_state.season_phase``
   and ``game_state.is_fifa_break`` when they are missing;
2. back-fills ``comp_type`` for cup / European fixtures from
   ``tournament_fixtures`` + ``tournaments.scope``;
3. numbers continental League Phase fixtures (matchday 1..N) by date;
4. derives ``season_phase`` / ``is_fifa_break`` from ``current_date``.

Run from the repo root::

    python scripts/migrate_realistic_calendar.py
"""
import datetime as dt
import os
import sqlite3

BASES = ["saves", "instance"]
EXTENSIONS = (".sqlite3", ".db")

# Mirrors services._ensure_schema_columns / config.FIFA_BREAK_WINDOWS.
FIFA_BREAK_WINDOWS = (
    (9, 1, 10),
    (10, 6, 17),
    (11, 10, 21),
    (3, 17, 28),
)
BOXING_DAY_RANGE = ((12, 22), (1, 5))

NEW_COLUMNS = {
    "match": {
        "comp_type": "VARCHAR(20) DEFAULT 'league'",
        "matchday": "INTEGER",
    },
    "game_state": {
        "season_phase": "VARCHAR(20) DEFAULT 'preseason'",
        "is_fifa_break": "BOOLEAN DEFAULT 0",
    },
}


def _files():
    out = []
    for base in BASES:
        if not os.path.isdir(base):
            continue
        for fn in sorted(os.listdir(base)):
            if fn.lower().endswith(EXTENSIONS):
                out.append(os.path.join(base, fn))
    return out


def _tables(cur):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in cur.fetchall()}


def _columns(cur, table):
    cur.execute(f'PRAGMA table_info("{table}")')
    return {row[1] for row in cur.fetchall()}


def _ensure_columns(conn, tables):
    cur = conn.cursor()
    for table, columns in NEW_COLUMNS.items():
        if table not in tables:
            continue
        existing = _columns(cur, table)
        for name, ddl in columns.items():
            if name in existing:
                continue
            try:
                cur.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {ddl}')
            except sqlite3.Error as exc:
                print(f"  ! {table}.{name}: {exc}")


def _parse_date(value):
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _is_fifa_break(d):
    return any(d.month == m and lo <= d.day <= hi
               for m, lo, hi in FIFA_BREAK_WINDOWS)


def _is_boxing_day(d):
    (sm, sd), (em, ed) = BOXING_DAY_RANGE
    md = (d.month, d.day)
    if (sm, sd) <= (em, ed):
        return (sm, sd) <= md <= (em, ed)
    return md >= (sm, sd) or md <= (em, ed)


def season_phase_for_date(d):
    """Same mapping as ``services.season_phase_for_date``."""
    if d is None:
        return "preseason"
    if _is_boxing_day(d):
        return "boxing_day"
    if d.month == 1:
        return "winter_break"
    if d.month in (2, 3):
        return "spring"
    if d.month in (4, 5):
        return "run_in"
    if d.month in (6, 7):
        return "offseason"
    if d.month == 8:
        return "preseason"
    if d.month in (9, 10):
        return "autumn"
    return "winter"


def _backfill_comp_type(conn, tables):
    if "match" not in tables:
        return
    cur = conn.cursor()
    has_fixtures = "tournament_fixtures" in tables
    has_tournaments = "tournaments" in tables
    if has_fixtures and has_tournaments:
        cur.execute("""
            UPDATE "match"
               SET comp_type = 'continental'
             WHERE (comp_type IS NULL OR comp_type = 'league')
               AND id IN (
                   SELECT tf.match_id
                     FROM tournament_fixtures tf
                     JOIN tournaments t ON t.id = tf.tournament_id
                    WHERE tf.match_id IS NOT NULL
                      AND t.scope = 'continental'
               )
        """)
        cur.execute("""
            UPDATE "match"
               SET comp_type = 'domestic_cup'
             WHERE (comp_type IS NULL OR comp_type = 'league')
               AND id IN (
                   SELECT tf.match_id
                     FROM tournament_fixtures tf
                     JOIN tournaments t ON t.id = tf.tournament_id
                    WHERE tf.match_id IS NOT NULL
                      AND (t.scope IS NULL OR t.scope <> 'continental')
               )
        """)
    # Legacy cup ties are week 0 without a reliable tournament link.
    cur.execute("""
        UPDATE "match"
           SET comp_type = 'domestic_cup'
         WHERE (comp_type IS NULL OR comp_type = 'league')
           AND week = 0
    """)


def _backfill_matchdays(conn, tables):
    if not ({"match", "tournament_fixtures", "tournaments"} <= tables):
        return
    cur = conn.cursor()
    cur.execute("""
        SELECT tf.tournament_id, tf.match_id, m.date
          FROM tournament_fixtures tf
          JOIN tournaments t ON t.id = tf.tournament_id
          JOIN "match" m ON m.id = tf.match_id
         WHERE tf.round_name = 'League Phase'
           AND tf.match_id IS NOT NULL
         ORDER BY tf.tournament_id, m.date, tf.match_id
    """)
    by_tournament = {}
    for tournament_id, match_id, date in cur.fetchall():
        by_tournament.setdefault(tournament_id, []).append((match_id, date))

    for match_ids in by_tournament.values():
        # Old saves have no matchday column, so each distinct fixture date is
        # one matchday (League Phase dates were generated two weeks apart).
        dates = sorted({date for _, date in match_ids if date})
        matchday_by_date = {date: i + 1 for i, date in enumerate(dates)}
        fallback = len(dates)
        for match_id, date in match_ids:
            matchday = matchday_by_date.get(date)
            if matchday is None:
                fallback += 1
                matchday = fallback
            cur.execute(
                'UPDATE "match" SET matchday = ? WHERE id = ?', (matchday, match_id)
            )


def _backfill_state(conn, tables):
    if "game_state" not in tables:
        return
    cur = conn.cursor()
    # "current_date" must stay quoted: SQLite otherwise reads it as the
    # CURRENT_DATE keyword and returns today instead of the save's date.
    cur.execute('SELECT id, "current_date" FROM game_state')
    for state_id, current_date in cur.fetchall():
        d = _parse_date(current_date)
        if d is None:
            continue
        cur.execute(
            "UPDATE game_state SET season_phase = ?, is_fifa_break = ? WHERE id = ?",
            (season_phase_for_date(d), 1 if _is_fifa_break(d) else 0, state_id),
        )


def migrate(path):
    conn = sqlite3.connect(path)
    try:
        tables = _tables(conn.cursor())
        if "match" not in tables and "game_state" not in tables:
            return False
        _ensure_columns(conn, tables)
        _backfill_comp_type(conn, tables)
        _backfill_matchdays(conn, tables)
        _backfill_state(conn, tables)
        conn.commit()
        return True
    finally:
        conn.close()


def main():
    files = _files()
    if not files:
        print("no db files found")
        return
    migrated = 0
    for path in files:
        try:
            if migrate(path):
                migrated += 1
                print(f"migrated {path}")
            else:
                print(f"skip {path}: not a game database")
        except Exception as exc:  # keep going: one bad save must not block the rest
            print(f"skip {path}: {exc}")
    print(f"done: {migrated}/{len(files)} database(s) migrated")


if __name__ == "__main__":
    main()
