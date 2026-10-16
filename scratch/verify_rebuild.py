import sqlite3
from collections import Counter

for db_path in (
    r"D:/software projects/football-game/saves/save1.sqlite3",
    r"D:/software projects/football-game/saves/template.db",
    r"D:/software projects/football-game/instance/db.sqlite3",
):
    db = sqlite3.connect(db_path)
    c = db.cursor()
    teams = c.execute("SELECT id, name FROM team").fetchall()
    no_lb = no_rb = no_cb = bad_xi = 0
    det_all = Counter()
    for tid, _name in teams:
        rows = c.execute(
            "SELECT position, detailed_position, is_starting FROM player WHERE team_id=?",
            (tid,),
        ).fetchall()
        det = Counter(r[1] or "" for r in rows if r[1])
        det_all.update(det)
        if det["LB"] < 1:
            no_lb += 1
        if det["RB"] < 1:
            no_rb += 1
        if det["CB"] < 2:
            no_cb += 1
        xi = Counter(r[0] for r in rows if r[2])
        if xi.get("GK", 0) != 1:
            bad_xi += 1
    print(f"== {db_path}")
    print(f"   teams={len(teams)}  squads_w/o_LB={no_lb}  w/o_RB={no_rb}  w/o_2xCB={no_cb}")
    print(f"   XIs without exactly 1 GK: {bad_xi}")
    print(f"   detailed spread: {dict(det_all)}")
    # sample starting XI of one club
    one = c.execute("SELECT id FROM team LIMIT 1").fetchone()
    xi = c.execute(
        "SELECT name, position, detailed_position FROM player WHERE team_id=? AND is_starting=1 ORDER BY position",
        (one[0],),
    ).fetchall()
    for row in xi:
        print("   XI:", row)
    db.close()
