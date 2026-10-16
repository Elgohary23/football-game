import sqlite3

db = sqlite3.connect(r"D:/software projects/football-game/saves/save1.sqlite3")
c = db.cursor()
for club in ("Bayern M", "Liverpool", "Arsenal", "Real Madrid"):
    tid = c.execute("SELECT id FROM team WHERE name LIKE ?", (club + "%",)).fetchone()[0]
    print("---", club)
    rows = c.execute(
        """SELECT name, position, detailed_position FROM player
           WHERE team_id=? ORDER BY is_starting DESC, position, name""",
        (tid,),
    ).fetchall()
    for r in rows[:24]:
        print("   ", r)
print("schema:", c.execute(
    "SELECT value FROM app_meta WHERE key='schema_version'").fetchone()[0])
