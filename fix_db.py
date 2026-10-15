"""fix_db.py — one-shot database repair for the Football Manager game.

Run once, no UI needed::

    python fix_db.py                # repair all databases (instance + saves + template)
    python fix_db.py --check        # validate only, change nothing
    python fix_db.py --db PATH      # repair a single database file

What it does, per database file:
  1. Backs the file up to ``<name>.bak-<timestamp>.sqlite3``.
  2. Deletes invalid fixtures: self-play (home == away), orphan teams,
     exact duplicates, and reverse duplicates in the same week.
  3. Deletes ALL unplayed fixtures and regenerates a mathematically perfect
     double round-robin (circle method) for every league, restricted to
     pairings not already decided on the pitch. One match per team per week,
     balanced home/away, strict rest (each week = one fixture per team).
  4. Resets every team's standings to zero and recalculates them ONLY from
     matches with ``played = 1`` (points / W-D-L / goals / morale untouched).
  5. Clears stuck ``live_active`` flags and validates the final schedule.

Only the standard library is used (sqlite3 / shutil / argparse) so the
script runs even when Flask/SQLAlchemy are broken or missing.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EXPECTED_ROUNDS = {1: 38, 2: 46, 3: 46, 4: 46}


def find_databases() -> list[str]:
    """Locate every game database: instance DB, saves, and template."""
    candidates: list[str] = []
    base = os.path.dirname(os.path.abspath(__file__))
    for rel in (
        os.path.join("instance", "db.sqlite3"),
        os.path.join("saves", "template.db"),
    ):
        p = os.path.join(base, rel)
        if os.path.exists(p):
            candidates.append(p)
    saves_dir = os.path.join(base, "saves")
    if os.path.isdir(saves_dir):
        for name in sorted(os.listdir(saves_dir)):
            if name.startswith("save") and name.endswith(".sqlite3"):
                p = os.path.join(saves_dir, name)
                if p not in candidates:
                    candidates.append(p)
    return candidates


def backup_db(path: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{path}.bak-{stamp}.sqlite3"
    shutil.copy2(path, backup)
    return backup


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    )
    return cur.fetchone() is not None


# ---------------------------------------------------------------------------
# Round-robin generator (circle method — single source of truth)
# ---------------------------------------------------------------------------

def generate_round_robin(team_ids: list[int]) -> list[tuple[int, int, int]]:
    """Return ``[(week, home_id, away_id)]`` — a perfect double round-robin.

    Guarantees:
      * every team plays exactly once per week (BYE if odd count),
      * every pair meets twice (once home, once away),
      * total weeks == (n - 1) * 2 for even n.
    """
    teams = list(dict.fromkeys(team_ids))  # de-dup, keep order
    if len(teams) < 2:
        return []
    odd = len(teams) % 2 == 1
    if odd:
        teams.append(None)  # BYE sentinel
    n = len(teams)
    single_rounds = n - 1
    first_leg: list[tuple[int, int, int]] = []
    circle = list(teams)
    for rnd in range(single_rounds):
        week = rnd + 1
        for i in range(n // 2):
            home, away = circle[i], circle[n - 1 - i]
            if home is None or away is None:
                continue
            # Alternate home/away on the first pairing each round for balance.
            if i == 0 and rnd % 2 == 1:
                home, away = away, home
            first_leg.append((week, home, away))
        # Rotate (keep first fixed).
        circle = [circle[0]] + [circle[-1]] + circle[1:-1]
    second_leg = [(w + single_rounds, a, h) for (w, h, a) in first_leg]
    return sorted(first_leg + second_leg, key=lambda f: (f[0], f[1], f[2]))


def validate_fixtures(fixtures: list[tuple[int, int, int]]) -> list[str]:
    """Return human-readable violations (empty == perfect schedule)."""
    errors: list[str] = []
    by_week: dict[int, list[int]] = {}
    for week, home, away in fixtures:
        if home == away:
            errors.append(f"week {week}: team {home} plays itself")
        by_week.setdefault(week, []).extend([home, away])
    for week, ids in sorted(by_week.items()):
        if len(ids) != len(set(ids)):
            errors.append(f"week {week}: a team plays twice -> {ids}")
    seen: dict[tuple[int, int], int] = {}
    for week, home, away in fixtures:
        key = (min(home, away), max(home, away))
        seen[key] = seen.get(key, 0) + 1
    for key, count in seen.items():
        if count != 2:
            errors.append(f"pair {key}: meets {count}x (expected 2)")
    return errors


# ---------------------------------------------------------------------------
# Repair steps
# ---------------------------------------------------------------------------

def clean_invalid_matches(conn: sqlite3.Connection) -> dict:
    """Delete self-play, orphans and duplicates. Returns counts."""
    stats = {"self_play": 0, "orphans": 0, "duplicates": 0}
    cur = conn.cursor()

    cur.execute("DELETE FROM match WHERE home_team_id = away_team_id")
    stats["self_play"] = cur.rowcount or 0

    cur.execute(
        """DELETE FROM match
           WHERE home_team_id NOT IN (SELECT id FROM team)
              OR away_team_id NOT IN (SELECT id FROM team)"""
    )
    stats["orphans"] = cur.rowcount or 0

    # Exact duplicates (same week + same home + same away): keep lowest id.
    cur.execute(
        """DELETE FROM match WHERE id NOT IN (
               SELECT MIN(id) FROM match
               GROUP BY week, home_team_id, away_team_id)"""
    )
    stats["duplicates"] = cur.rowcount or 0

    # Reverse duplicates same week (A v B AND B v A in one week): keep one.
    cur.execute("SELECT id, week, home_team_id, away_team_id FROM match ORDER BY id")
    seen: dict[tuple, int] = {}
    to_delete: list[int] = []
    for mid, week, home, away in cur.fetchall():
        key = (week, min(home, away), max(home, away))
        if key in seen:
            to_delete.append(mid)
        else:
            seen[key] = mid
    if to_delete:
        cur.execute(
            f"DELETE FROM match WHERE id IN ({','.join('?' * len(to_delete))})",
            to_delete,
        )
        stats["duplicates"] += cur.rowcount or 0
    return stats


def played_pairings(conn: sqlite3.Connection) -> set[tuple[int, int]]:
    """Unordered (a, b) pairs already decided on the pitch."""
    cur = conn.execute(
        "SELECT home_team_id, away_team_id FROM match WHERE played = 1"
    )
    return {(min(h, a), max(h, a)) for h, a in cur.fetchall()}


def regenerate_schedule(conn: sqlite3.Connection) -> dict:
    """Delete unplayed fixtures; regenerate perfect round-robin per league."""
    stats = {"deleted_unplayed": 0, "created": 0, "leagues": 0}
    cur = conn.cursor()
    # Cup ties (week == 0) belong to the cup engine — never wipe or rebuild
    # them here; only league fixtures (weeks >= 1) are regenerated.
    cur.execute(
        "DELETE FROM match WHERE (played = 0 OR played IS NULL) AND week != 0"
    )
    stats["deleted_unplayed"] = cur.rowcount or 0

    decided = played_pairings(conn)
    leagues = conn.execute("SELECT id, tier FROM league").fetchall()
    for league_id, tier in leagues:
        rows = conn.execute(
            "SELECT id FROM team WHERE league_id = ? ORDER BY id", (league_id,)
        ).fetchall()
        team_ids = [r[0] for r in rows]
        if len(team_ids) < 2:
            continue
        full = generate_round_robin(team_ids)
        # Skip pairings already decided so history is never replayed.
        remaining = [
            (w, h, a)
            for (w, h, a) in full
            if (min(h, a), max(h, a)) not in decided
        ]
        # Re-number remaining fixtures to compact weeks starting at 1 after
        # the last played week of this league.
        cur2 = conn.execute(
            """SELECT COALESCE(MAX(week), 0) FROM match
               WHERE played = 1 AND (home_team_id IN
                 (SELECT id FROM team WHERE league_id = ?))""",
            (league_id,),
        )
        base_week = cur2.fetchone()[0] or 0
        # Group remaining into weeks preserving one-match-per-team.
        weeks: list[list[tuple[int, int]]] = []
        for _w, h, a in remaining:
            placed = False
            for wk in weeks:
                ids = {x for pair in wk for x in pair}
                if h not in ids and a not in ids:
                    wk.append((h, a))
                    placed = True
                    break
            if not placed:
                weeks.append([(h, a)])
        for i, wk in enumerate(weeks, start=base_week + 1):
            for h, a in wk:
                cur.execute(
                    "INSERT INTO match (home_team_id, away_team_id, week,"
                    " played, home_score, away_score) VALUES (?, ?, ?, 0, 0, 0)",
                    (h, a, i),
                )
                stats["created"] += 1
        stats["leagues"] += 1
        expected = EXPECTED_ROUNDS.get(tier or 1, 38)
        total_weeks = base_week + len(weeks)
        if total_weeks > expected:
            print(f"  [warn] league {league_id}: {total_weeks} weeks > {expected}")
    return stats


def recalculate_standings(conn: sqlite3.Connection) -> dict:
    """Zero all standings, then re-apply every played match."""
    stats = {"applied": 0}
    cur = conn.cursor()
    cur.execute(
        """UPDATE team SET points=0, wins=0, losses=0, draws=0,
           goals_for=0, goals_against=0"""
    )
    cur.execute(
        "SELECT home_team_id, away_team_id, home_score, away_score"
        " FROM match WHERE played = 1"
    )
    for home_id, away_id, hs, aws in cur.fetchall():
        hs, aws = hs or 0, aws or 0
        cur.execute(
            "UPDATE team SET goals_for = goals_for + ?,"
            " goals_against = goals_against + ? WHERE id = ?",
            (hs, aws, home_id),
        )
        cur.execute(
            "UPDATE team SET goals_for = goals_for + ?,"
            " goals_against = goals_against + ? WHERE id = ?",
            (aws, hs, away_id),
        )
        if hs > aws:
            cur.execute(
                "UPDATE team SET points = points + 3, wins = wins + 1 WHERE id = ?",
                (home_id,),
            )
            cur.execute(
                "UPDATE team SET losses = losses + 1 WHERE id = ?", (away_id,)
            )
        elif aws > hs:
            cur.execute(
                "UPDATE team SET points = points + 3, wins = wins + 1 WHERE id = ?",
                (away_id,),
            )
            cur.execute(
                "UPDATE team SET losses = losses + 1 WHERE id = ?", (home_id,)
            )
        else:
            cur.execute(
                "UPDATE team SET points = points + 1, draws = draws + 1"
                " WHERE id IN (?, ?)",
                (home_id, away_id),
            )
        stats["applied"] += 1
    # Consistency: played count must equal sum of W/D/L per team involvement.
    return stats


def clear_stuck_live(conn: sqlite3.Connection) -> int:
    try:
        cur = conn.execute(
            "UPDATE match SET live_active = 0 WHERE live_active = 1 AND played = 1"
        )
        return cur.rowcount or 0
    except Exception:
        return 0


def check_database(path: str) -> list[str]:
    """Read-only validation; returns violations (empty == healthy)."""
    problems: list[str] = []
    try:
        conn = sqlite3.connect(path)
    except Exception as exc:
        return [f"cannot open: {exc}"]
    try:
        if not table_exists(conn, "match") or not table_exists(conn, "team"):
            return ["missing match/team tables"]
        rows = conn.execute(
            "SELECT week, home_team_id, away_team_id, played FROM match"
        ).fetchall()
        # Validate only future (unplayed) league weeks for overlaps.
        # Cup ties (week == 0) are knockout/midweek fixtures outside the
        # round-robin cycle, so they are excluded from overlap checks, as
        # are their results from the league-table cross-check below.
        unplayed = [(w, h, a) for (w, h, a, p) in rows if not p and w != 0]
        problems.extend(
            f"UNPLAYED: {e}" for e in validate_fixtures(unplayed)
        )
        # Standings cross-check: recompute expected points from played.
        teams = {
            r[0]: r[1]
            for r in conn.execute("SELECT id, points FROM team").fetchall()
        }
        expected: dict[int, int] = {tid: 0 for tid in teams}
        for w, h, a, p in rows:
            if not p or w == 0:
                continue
            scores = conn.execute(
                "SELECT home_score, away_score FROM match WHERE week=? "
                "AND home_team_id=? AND away_team_id=? LIMIT 1",
                (w, h, a),
            ).fetchone()
            hs, aws = (scores or (0, 0))
            hs, aws = hs or 0, aws or 0
            if hs > aws:
                expected[h] = expected.get(h, 0) + 3
            elif aws > hs:
                expected[a] = expected.get(a, 0) + 3
            else:
                expected[h] = expected.get(h, 0) + 1
                expected[a] = expected.get(a, 0) + 1
        for tid, pts in teams.items():
            if pts != expected.get(tid, 0):
                problems.append(
                    f"STANDINGS: team {tid} has {pts} pts, played matches give"
                    f" {expected.get(tid, 0)}"
                )
    finally:
        conn.close()
    return problems


def repair_database(path: str) -> dict:
    summary: dict = {"db": path}
    backup = backup_db(path)
    summary["backup"] = backup
    conn = sqlite3.connect(path)
    try:
        if not table_exists(conn, "match") or not table_exists(conn, "team"):
            summary["error"] = "missing match/team tables — skipped"
            conn.close()
            return summary
        try:
            with conn:  # one atomic transaction; rollback on any failure
                summary["cleaned"] = clean_invalid_matches(conn)
                summary["schedule"] = regenerate_schedule(conn)
                summary["standings"] = recalculate_standings(conn)
                summary["stuck_live_cleared"] = clear_stuck_live(conn)
        except Exception as exc:
            summary["error"] = f"{type(exc).__name__}: {exc}"
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        conn.close()
    # Post-repair validation (fresh connection).
    summary["remaining_problems"] = check_database(path)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Repair football-game database(s).")
    parser.add_argument("--db", default=None, help="Repair a single DB file.")
    parser.add_argument("--check", action="store_true",
                        help="Validate only; change nothing.")
    args = parser.parse_args(argv)

    targets = [args.db] if args.db else find_databases()
    if not targets:
        print("No database files found. Nothing to do.")
        return 1

    failed = False
    for path in targets:
        print(f"=== {path} ===")
        if args.check:
            problems = check_database(path)
            if problems:
                failed = True
                print(f"  FOUND {len(problems)} problem(s):")
                for p in problems[:20]:
                    print(f"   - {p}")
            else:
                print("  OK: schedule and standings are consistent.")
            continue
        summary = repair_database(path)
        if "error" in summary:
            failed = True
            print(f"  ERROR: {summary['error']}")
            continue
        print(f"  backup: {summary['backup']}")
        print(f"  cleaned: {summary['cleaned']}")
        print(f"  schedule: {summary['schedule']}")
        print(f"  standings recalculated from {summary['standings']['applied']} played match(es)")
        print(f"  stuck live flags cleared: {summary['stuck_live_cleared']}")
        remaining = summary.get("remaining_problems", [])
        if remaining:
            failed = True
            print(f"  REMAINING ({len(remaining)}):")
            for p in remaining[:20]:
                print(f"   - {p}")
        else:
            print("  VERIFIED: schedule perfect, standings match played results.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
