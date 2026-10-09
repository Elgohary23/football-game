"""Statistics bluepprint: league leaderboards."""

from flask import Blueprint, render_template, request

bp = Blueprint("stats", __name__)

from models import db, League, Team
from simulation import get_top_assists, get_top_scorers
from services import safe_player_team, safe_state


def _selected_league(league_id):
    return db.session.get(League, league_id) if league_id else None


def _league_id(player_team, fallback=None):
    if fallback is not None:
        return fallback
    return player_team.league_id if player_team else None


def _teams_dict(league_id):
    """Only the clubs actually referenced on the page (league scope)."""
    if league_id is None:
        return {}
    teams = Team.query.filter_by(league_id=league_id).all()
    return {t.id: t.name for t in teams}


@bp.route("/stats")
def stats_center():
    state = safe_state()
    player_team = safe_player_team()

    leagues = League.query.order_by(League.tier).all()
    league_id = request.args.get("league_id", type=int) or _league_id(player_team)
    selected_league = _selected_league(league_id)
    if league_id and selected_league is None:
        league_id = _league_id(player_team)
        selected_league = _selected_league(league_id)
    top_scorers = get_top_scorers(league_id) if league_id else []
    top_assists = get_top_assists(league_id) if league_id else []
    teams_dict = _teams_dict(league_id)

    return render_template(
        "stats.html",
        state=state,
        player_team=player_team,
        leagues=leagues,
        selected_league=selected_league,
        top_scorers=top_scorers,
        top_assists=top_assists,
        teams_dict=teams_dict,
    )


@bp.route("/stats/top-scorers")
def top_scorers():
    state = safe_state()
    player_team = safe_player_team()
    league_id = request.args.get("league_id", type=int) or _league_id(player_team)

    leagues = League.query.order_by(League.tier).all()
    selected_league = _selected_league(league_id)
    if league_id and selected_league is None:
        league_id = _league_id(player_team)
        selected_league = _selected_league(league_id)
    scorers = get_top_scorers(league_id) if league_id else []
    teams_dict = _teams_dict(league_id)

    return render_template(
        "top_scorers.html",
        state=state,
        player_team=player_team,
        leagues=leagues,
        selected_league=selected_league,
        scorers=scorers,
        teams_dict=teams_dict,
    )


@bp.route("/stats/top-assists")
def top_assists():
    state = safe_state()
    player_team = safe_player_team()
    league_id = request.args.get("league_id", type=int) or _league_id(player_team)

    leagues = League.query.order_by(League.tier).all()
    selected_league = _selected_league(league_id)
    if league_id and selected_league is None:
        league_id = _league_id(player_team)
        selected_league = _selected_league(league_id)
    assists = get_top_assists(league_id) if league_id else []
    teams_dict = _teams_dict(league_id)

    return render_template(
        "top_assists.html",
        state=state,
        player_team=player_team,
        leagues=leagues,
        selected_league=selected_league,
        assists=assists,
        teams_dict=teams_dict,
    )