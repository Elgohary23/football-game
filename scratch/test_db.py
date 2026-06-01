import sys
import os

# Add project root to sys.path
sys.path.append(os.path.abspath('.'))

from app import app, db, init_template, create_new_game, slot_exists, TEMPLATE_DB, SAVES_DIR

# Let's run with a Flask test_request_context to mock the session and request context!
with app.test_request_context():
    print("Testing actual init_template...")
    if os.path.exists(TEMPLATE_DB):
        os.remove(TEMPLATE_DB)
        
    init_template()
    print("TEMPLATE_DB exists after init:", os.path.exists(TEMPLATE_DB))
    if os.path.exists(TEMPLATE_DB):
        print("TEMPLATE_DB file size:", os.path.getsize(TEMPLATE_DB))
        
    print("\nTesting actual create_new_game(1)...")
    save_path = os.path.join(SAVES_DIR, "save1.sqlite3")
    if os.path.exists(save_path):
        os.remove(save_path)
        
    res = create_new_game(1)
    print("Result:", res)
    print("save1.sqlite3 exists:", os.path.exists(save_path))
    if os.path.exists(save_path):
        print("save1.sqlite3 size:", os.path.getsize(save_path))
