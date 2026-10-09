"""Shared pytest fixtures for backend + frontend tests."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app import create_app
from models import db, GameState, League, Match, Player, Team


def seed_world():
    """Minimal deterministic world: 1 league, 2 teams x 15 players, state, match."""
    league = League(name="Test Premier League", tier=1, reputation=90)
    db.session.add(league)
    db.session.flush()

    player_team = Team(
        name="Player FC",
        morale=70,
        budget=1000000,
        reputation=70,
        is_player_team=True,
        league_id=league.id,
        training_facilities=3,
        youth_facilities=3,
    )
    opponent = Team(
        name="Opponent FC",
        morale=70,
        budget=1000000,
        reputation=60,
        is_player_team=False,
        league_id=league.id,
        training_facilities=3,
        youth_facilities=3,
    )
    db.session.add_all([player_team, opponent])
    db.session.flush()

    positions = ["GK", "GK"] + ["DEF"] * 4 + ["MID"] * 5 + ["FWD"] * 4  # 15
    for team in (player_team, opponent):
        for i, pos in enumerate(positions):
            attack = 70 if pos in ("FWD", "MID") else 55
            defense = 70 if pos in ("DEF", "GK") else 55
            db.session.add(
                Player(
                    name=f"{team.name} P{i}",
                    position=pos,
                    attack=attack,
                    defense=defense,
                    potential=85,
                    age=24,
                    height=180,
                    weight=75,
                    personality="Normal",
                    region="Europe",
                    market_value=100000,
                    team_id=team.id,
                    is_starting=(i < 11),
                    stamina=80,
                    technique=60,
                    speed=60,
                )
            )

    state = GameState(
        manager_name="Test Manager",
        manager_nationality="Testland",
        current_day=1,
        current_week=1,
        season_over=False,
        user_league_id=league.id,
        season_number=1,
        board_confidence=60,
    )
    db.session.add(state)
    db.session.flush()

    match = Match(
        home_team_id=player_team.id,
        away_team_id=opponent.id,
        week=1,
        played=False,
    )
    db.session.add(match)
    db.session.commit()
    return {
        "league": league,
        "player_team": player_team,
        "opponent": opponent,
        "state": state,
        "match": match,
    }


@pytest.fixture()
def app():
    application = create_app()
    application.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
    )
    with application.app_context():
        # Re-init engine against in-memory DB
        if "sqlalchemy" in application.extensions:
            del application.extensions["sqlalchemy"]
        db.init_app(application)
        db.create_all()
        seed_world()
        try:
            from services import invalidate_league_cache

            invalidate_league_cache()
        except Exception:
            pass
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["active_save"] = 1
        yield c


@pytest.fixture()
def ctx(app):
    """Plain app-context (no HTTP) for service/simulation unit tests."""
    with app.app_context():
        yield app
