"""Blueprint registration (Tip 1).

Route handlers live in focused modules instead of a single ``app.py``. Every
handler stays thin: data/business rules come from ``services`` / ``simulation``
and only HTTP glue remains here.
"""

from . import save_routes, game_routes, team_routes, inbox_routes, stats_routes, cup_routes


def register_blueprints(application) -> None:
    application.register_blueprint(game_routes.bp)
    application.register_blueprint(team_routes.bp)
    application.register_blueprint(inbox_routes.bp)
    application.register_blueprint(stats_routes.bp)
    application.register_blueprint(cup_routes.bp)
    application.register_blueprint(save_routes.bp)