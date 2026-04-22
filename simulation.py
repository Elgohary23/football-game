import random
from models import db, Team, Player, GameState, Match


def simulate_day(game_state):
    game_state.current_day += 1

    for player in Player.query.all():
        player.fitness = max(0, player.fitness - 1)

    if random.random() < 0.1:
        team = random.choice(Team.query.all())
        team.morale = min(100, team.morale + 2)

    db.session.commit()


def is_match_day(game_state):
    return game_state.current_day % 7 == 0


def simulate_match(match):
    home = Team.query.get(match.home_team_id)
    away = Team.query.get(match.away_team_id)

    home_bonus = home.morale // 20
    away_bonus = away.morale // 20

    home_score = max(0, home.attack - away.defense + random.randint(-1, 2) + home_bonus)
    away_score = max(0, away.attack - home.defense + random.randint(-1, 2) + away_bonus)

    match.result = f"{home_score}-{away_score}"
    match.played = True

    if home_score > away_score:
        home.morale = min(100, home.morale + 5)
        away.morale = max(0, away.morale - 3)
    elif away_score > home_score:
        away.morale = min(100, away.morale + 5)
        home.morale = max(0, home.morale - 3)

    db.session.commit()
    return match


def get_current_match(game_state):
    match = Match.query.filter_by(week=game_state.current_week, played=False).first()
    return match


def get_recent_results(game_state):
    matches = Match.query.filter(
        Match.week < game_state.current_week,
        Match.played == True
    ).order_by(Match.week.desc()).all()
    return matches