import os
from flask import Flask, render_template, redirect
from models import db, Team, Player, GameState, Match
from simulation import simulate_day, is_match_day, simulate_match, get_current_match, get_recent_results

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///db.sqlite3'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)


def init_db():
    db.create_all()

    if GameState.query.count() == 0:
        teams_data = [
            {"name": "Arsenal", "attack": 75, "defense": 70, "morale": 80, "is_player_team": True},
            {"name": "Chelsea", "attack": 72, "defense": 72, "morale": 75, "is_player_team": False},
            {"name": "Liverpool", "attack": 78, "defense": 68, "morale": 78, "is_player_team": False},
            {"name": "ManCity", "attack": 80, "defense": 75, "morale": 82, "is_player_team": False},
            {"name": "ManUtd", "attack": 70, "defense": 65, "morale": 70, "is_player_team": False},
            {"name": "Tottenham", "attack": 68, "defense": 68, "morale": 72, "is_player_team": False},
        ]

        for t in teams_data:
            team = Team(**t)
            db.session.add(team)

        db.session.commit()

        player_names = [
            "Salah", "Saka", "Odegaard", "Havertz", "Martinelli",
            "Rice", "Saliba", "Gabriel", "Zinchenko", "Raya",
            "Saka", "Martinelli", "Havertz", "Odegaard", "Rice"
        ]

        player_team = Team.query.filter_by(is_player_team=True).first()
        for i, name in enumerate(player_names):
            player = Player(
                name=name,
                attack=60 + (i % 10),
                defense=55 + (i % 10),
                stamina=80 + (i % 15),
                fitness=100,
                team_id=player_team.id
            )
            db.session.add(player)

        db.session.commit()

        ai_teams = Team.query.filter_by(is_player_team=False).all()
        for team in ai_teams:
            for i in range(11):
                player = Player(
                    name=f"{team.name}_Player{i+1}",
                    attack=55 + (i % 10),
                    defense=50 + (i % 10),
                    stamina=75 + (i % 15),
                    fitness=100,
                    team_id=team.id
                )
                db.session.add(player)

        db.session.commit()

        team_list = Team.query.all()
        num_teams = len(team_list)
        for week in range(1, 11):
            for i in range(0, num_teams, 2):
                if i + 1 < num_teams:
                    match = Match(
                        home_team_id=team_list[i].id,
                        away_team_id=team_list[i + 1].id,
                        week=week,
                        played=False
                    )
                    db.session.add(match)

        db.session.commit()

        state = GameState(current_day=1, current_week=1, season_over=False)
        db.session.add(state)
        db.session.commit()


@app.route('/')
def dashboard():
    state = GameState.query.first()
    player_team = Team.query.filter_by(is_player_team=True).first()
    players = Player.query.filter_by(team_id=player_team.id).all() if player_team else []
    current_match = get_current_match(state) if state else None
    recent_results = get_recent_results(state) if state else []

    return render_template(
        'dashboard.html',
        state=state,
        player_team=player_team,
        players=players,
        current_match=current_match,
        recent_results=recent_results
    )


@app.route('/calendar')
def calendar():
    state = GameState.query.first()
    matches = Match.query.order_by(Match.week, Match.id).all()

    matches_by_week = {}
    for match in matches:
        if match.week not in matches_by_week:
            matches_by_week[match.week] = []
        matches_by_week[match.week].append(match)

    return render_template('calendar.html', state=state, matches_by_week=matches_by_week)


@app.route('/match')
def match_view():
    state = GameState.query.first()
    current_match = get_current_match(state)

    if not current_match:
        return render_template('match.html', match=None, state=state)

    return render_template('match.html', match=current_match, state=state)


@app.route('/advance_day')
def advance_day():
    state = GameState.query.first()
    if not state or state.season_over:
        return redirect('/')

    simulate_day(state)

    if is_match_day(state):
        match = get_current_match(state)
        if match:
            simulate_match(match)
            if state.current_week >= 10:
                state.season_over = True

    db.session.commit()
    return redirect('/')


@app.route('/advance_week')
def advance_week():
    state = GameState.query.first()
    if not state or state.season_over:
        return redirect('/')

    while not is_match_day(state) and not state.season_over:
        simulate_day(state)
        if is_match_day(state):
            match = get_current_match(state)
            if match:
                simulate_match(match)
                if state.current_week >= 10:
                    state.season_over = True
                    break

    db.session.commit()
    return redirect('/')


if __name__ == '__main__':
    with app.app_context():
        init_db()
    app.run(debug=True)