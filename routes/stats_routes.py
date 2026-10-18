"""Statistics blueprint: redirected to the merged standings/statistics page."""

from flask import Blueprint, redirect, url_for

bp = Blueprint("stats", __name__)


@bp.route("/stats")
def stats_center():
    return redirect(url_for("main.standings"))


@bp.route("/stats/top-scorers")
def top_scorers():
    return redirect(url_for("main.standings"))


@bp.route("/stats/top-assists")
def top_assists():
    return redirect(url_for("main.standings"))
