"""Application entry point.

Keeps only app wiring: config, CSRF, context processor, error handlers and the
startup bootstrap. All routes live in the ``routes`` package and all business
rules live in ``services`` / ``simulation`` (Tips 1 & 3).
"""

import os
import shutil
import sqlite3

from flask import Flask, current_app, flash, jsonify, redirect, request
from flask_wtf import CSRFProtect

from models import db, GameState, Team
from services import (
    ensure_saves_dir,
    get_active_slot,
    init_template,
    instance_db_path,
    refresh_db_engine,
    setup_logging,
    template_db_path,
)
from simulation import get_unread_count


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///db.sqlite3"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.secret_key = os.environ.get("SECRET_KEY", "football_secret_key_prod")

    db.init_app(app)
    CSRFProtect(app)

    from routes import register_blueprints

    register_blueprints(app)

    @app.context_processor
    def inject_globals():
        ctx = {
            "unread_count": 0,
            "manager_name": "",
            "manager_nationality": "",
            "player_team_name": "",
            "current_week": 0,
            "current_day": 0,
        }
        if get_active_slot() == 0:
            return ctx
        try:
            ctx["unread_count"] = get_unread_count()
            state = GameState.query.first()
            if state:
                ctx["manager_name"] = state.manager_name or ""
                ctx["manager_nationality"] = state.manager_nationality or ""
                ctx["current_week"] = state.current_week or 0
                ctx["current_day"] = state.current_day or 0
            player_team = Team.query.filter_by(is_player_team=True).first()
            if player_team:
                ctx["player_team_name"] = player_team.name or ""
        except Exception:
            pass
        return ctx

    # JSON-only endpoint families: they have no human page, only fetch()
    # callers, so a redirect would break response.json() for every caller.
    _JSON_PREFIXES = ("/static/", "/live/", "/inbox", "/api/",
                      "/toggle_starter", "/formation", "/player_profile")

    @app.errorhandler(405)
    def method_not_allowed(e):
        # Same JSON-vs-redirect split as the 404 handler: a wrong-method
        # fetch() gets JSON it can parse instead of an HTML redirect.
        wants_json = (
            (request.path or "").startswith(_JSON_PREFIXES)
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or request.accept_mimetypes.best == "application/json"
            or "application/json" in (request.headers.get("Accept") or "")
        )
        if wants_json:
            return jsonify({"error": "method not allowed"}), 405
        flash("حدث خطأ في الطلب، تم إعادتك للرئيسية.")
        return redirect("/")

    @app.errorhandler(404)
    def not_found(e):
        path = request.path or ""
        # Browser-internal files: silence them instead of flashing + redirect loop.
        if path == "/favicon.ico" or path.startswith("/.well-known/"):
            return "", 204
        # API / fetch / static callers expect JSON, not an HTML redirect
        # (a redirect breaks response.json() and logs a misleading 404).
        wants_json = (
            path.startswith(_JSON_PREFIXES)
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or request.accept_mimetypes.best == "application/json"
            or "application/json" in (request.headers.get("Accept") or "")
        )
        if wants_json:
            return jsonify({"error": "not found"}), 404
        flash("الصفحة غير موجودة!")
        return redirect("/")

    @app.errorhandler(500)
    def server_error(e):
        # Persist the real traceback: the browser console only shows a bare
        # 500, so without this the cause is invisible after the fact.
        try:
            current_app.logger.exception(
                "Internal server error on %s", request.path
            )
        except Exception:
            pass
        if request.path == "/load":
            return "حدث خطأ جسيم في الخادم. يرجى المحاولة لاحقاً.", 500
        # fetch() callers (formation saves, toggles, live state...) expect
        # JSON — an HTML redirect breaks response.json(). Same JSON-vs-
        # redirect split as the 404/405 handlers above.
        wants_json = (
            (request.path or "").startswith(_JSON_PREFIXES)
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or request.accept_mimetypes.best == "application/json"
            or "application/json" in (request.headers.get("Accept") or "")
        )
        if wants_json:
            return jsonify({"error": "حدث خطأ في الخادم."}), 500
        flash("حدث خطأ في الخادم. تم إعادتك لصفحة الحفظ.")
        return redirect("/load")

    return app


app = create_app()


def _needs_rebuild() -> bool:
    from config import TEMPLATE_SCHEMA_VERSION

    template_db = template_db_path()
    if not os.path.exists(template_db):
        return False
    try:
        conn = sqlite3.connect(template_db)
        cursor = conn.cursor()
        # Preferred: version marker written by services._write_schema_version().
        try:
            cursor.execute(
                "SELECT value FROM app_meta WHERE key='schema_version'"
            )
            row = cursor.fetchone()
            if row is not None:
                conn.close()
                return int(row[0]) < TEMPLATE_SCHEMA_VERSION
        except Exception:
            pass
        # Fallback: structural check. NOTE: training_focus lives on team,
        # not player — checking player caused a rebuild on every startup.
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='staff'"
        )
        has_staff = cursor.fetchone()
        cursor.execute("PRAGMA table_info(team);")
        team_columns = [row[1] for row in cursor.fetchall()]
        cursor.execute("PRAGMA table_info(game_state);")
        gs_columns = [row[1] for row in cursor.fetchall()]
        conn.close()
        if not has_staff or "training_focus" not in team_columns or "start_date" not in gs_columns:
            return True
    except Exception:
        return True
    return False


def _copy_template_to_instance() -> None:
    db_path = instance_db_path()
    copy_needed = not os.path.exists(db_path) or os.path.getsize(db_path) == 0
    if not copy_needed:
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='game_state'"
            )
            copy_needed = cursor.fetchone() is None
            conn.close()
        except Exception:
            copy_needed = True

    if copy_needed:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        if os.path.exists(db_path):
            os.remove(db_path)
        shutil.copy2(template_db_path(), db_path)


if __name__ == "__main__":
    setup_logging()
    ensure_saves_dir()
    template_db = template_db_path()

    with app.app_context():
        if _needs_rebuild():
            print("[INFO] Old database schema detected. Rebuilding template...")
            try:
                if os.path.exists(template_db):
                    os.remove(template_db)
                # NOTE: user saves (saves/save*.sqlite3) are intentionally
                # preserved here. Deleting them on every false-positive
                # rebuild wiped player progress. Old saves with a stale
                # schema will fail their own version check on load instead.
                db_path = instance_db_path()
                if os.path.exists(db_path):
                    os.remove(db_path)
            except Exception:
                pass

        if not os.path.exists(template_db):
            init_template()
        _copy_template_to_instance()

    # use_reloader=False: the Werkzeug reloader re-executes this block in a
    # child process, which printed the [INFO] line twice and rebuilt the
    # template DB concurrently (looked like a freeze on startup).
    app.run(debug=True, port=5000, use_reloader=False)