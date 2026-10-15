"""Cup & continental competitions blueprint: brackets + league-phase tables."""

from flask import Blueprint, flash, redirect, render_template, request

bp = Blueprint("cups", __name__)

from models import ContinentalStandings, Tournament
from services import safe_player_team, safe_state


@bp.route("/cups")
def cups_overview():
    from cup_services import (
        active_tournaments,
        continental_table,
        cup_name,
        ensure_cups_for_state,
        get_tournament_bracket,
        round_label,
    )
    from models import db

    state = safe_state()
    player_team = safe_player_team()

    # One-time bootstrap for saves created before cups existed. POST-only
    # would be purer, but a GET that only ever creates the season's missing
    # editions (never duplicates, never deletes) keeps old saves working
    # with a single click on the nav link.
    if not active_tournaments(season=state.season_number or 1):
        try:
            ensure_cups_for_state()
            db.session.commit()
        except Exception:
            db.session.rollback()

    tournaments = active_tournaments(season=state.season_number or 1)
    if not tournaments:
        tournaments = active_tournaments()

    view = []
    for t in tournaments:
        entry = {
            "tournament": t,
            "bracket": get_tournament_bracket(t.id),
            "table": None,
            "champion": None,
        }
        if t.scope == "continental" and t.current_round in ("League Phase", "Play-off"):
            try:
                entry["table"] = continental_table(t.id)
            except Exception:
                entry["table"] = []
        if t.winner_team_id:
            try:
                from models import Team
                entry["champion"] = db.session.get(Team, t.winner_team_id)
            except Exception:
                entry["champion"] = None
        view.append(entry)

    return render_template(
        "cups.html",
        state=state,
        player_team=player_team,
        view=view,
        round_label=round_label,
        cup_name=cup_name,
    )


@bp.route("/cups/start", methods=["POST"])
def cups_start():
    from cup_services import ensure_cups_for_state
    from models import db

    safe_state()
    try:
        created = ensure_cups_for_state()
        db.session.commit()
        if created:
            flash(f"انطلقت منافسات الكؤوس! ({len(created)} بطولة)")
        else:
            flash("منافسات الكؤوس جارية بالفعل.")
    except Exception:
        db.session.rollback()
        flash("تعذر بدء منافسات الكؤوس!")
    return redirect("/cups")
