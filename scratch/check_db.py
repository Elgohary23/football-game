import sqlite3
conn = sqlite3.connect('template.db')
cursor = conn.cursor()
try:
    cursor.execute("SELECT count(*) FROM game_state")
    print(f"GameState count: {cursor.fetchone()[0]}")
    cursor.execute("SELECT * FROM game_state")
    print(f"GameState: {cursor.fetchone()}")
except Exception as e:
    print(f"Error: {e}")
conn.close()
