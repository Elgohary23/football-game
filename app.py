import os
import random
from flask import Flask, render_template, redirect, flash, abort
from flask_wtf import CSRFProtect
from models import db, Team, Player, GameState, Match, Message, TransferOffer, League
from simulation import (
    simulate_day,
    is_match_day,
    match_director,
    get_current_match,
    get_recent_results,
    simulate_calendar,
    get_pending_actions,
    get_match_report,
    get_messages,
    get_unread_count,
    mark_message_read,
)
from procedural import generate_player_name, generate_player_stats

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///db.sqlite3"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.secret_key = os.environ.get("SECRET_KEY", "football_secret_key_prod")
csrf = CSRFProtect(app)
db.init_app(app)

def init_db():
    """Initialize database with a large-scale procedurally generated world."""
    db.create_all()

    if GameState.query.count() != 0:
        return

    # Create Leagues
    leagues_data = [
        {"name": "Premier League", "tier": 1, "reputation": 90},
        {"name": "Championship", "tier": 2, "reputation": 70},
        {"name": "League One", "tier": 3, "reputation": 50},
    ]
    
    leagues = []
    for l_data in leagues_data:
        league = League(**l_data)
        db.session.add(league)
        leagues.append(league)
    db.session.commit()

    # Create Teams
    teams_per_league = 10
    regions = ["Europe", "Middle_East", "South_America"]
    
    for league in leagues:
        for i in range(teams_per_league):
            is_player = (league.tier == 1 and i == 0)
            team = Team(
                name=f"{league.name} Team {i+1}",
                morale=random.randint(60, 80),
                budget=1000000 * (4 - league.tier),
                reputation=league.reputation + random.randint(-5, 5),
                is_player_team=is_player,
                league_id=league.id
            )
            db.session.add(team)
            db.session.flush() # Get team ID
            
            # Generate Players
            positions = ["GK"] * 2 + ["DEF"] * 6 + ["MID"] * 6 + ["FWD"] * 4
            region = random.choice(regions)
            for j, pos in enumerate(positions):
                stats = generate_player_stats(tier_level=league.tier, position=pos, region=region)
                player = Player(
                    name=generate_player_name(region),
                    position=pos,
                    attack=stats["attack"],
                    defense=stats["defense"],
                    potential=stats["potential"],
                    age=stats["age"],
                    height=stats["height"],
                    weight=stats["weight"],
                    personality=stats["personality"],
                    region=region,
                    market_value=stats["market_value"],
                    team_id=team.id,
                    is_starting=(j < 11)
                )
                db.session.add(player)
            
            # Generate Schedule for this league
            for week in range(1, 11):
                # Simple scheduling
                pass # Will implement below
    
    db.session.commit()

    # Generate Match Schedule (Round Robin simplified)
    for league in leagues:
        teams = Team.query.filter_by(league_id=league.id).all()
        for week in range(1, 11):
            random.shuffle(teams)
            for k in range(0, len(teams), 2):
                if k+1 < len(teams):
                    match = Match(
                        home_team_id=teams[k].id,
                        away_team_id=teams[k+1].id,
                        week=week,
                        played=False
                    )
                    db.session.add(match)

    db.session.commit()

    player_league = League.query.filter_by(tier=1).first()
    state = GameState(current_day=1, current_week=1, season_over=False, user_league_id=player_league.id)
    db.session.add(state)
    db.session.commit()


def get_safe_state():
    """Get game state with defensive check."""
    state = GameState.query.first()
    if not state:
        abort(500, description="Game state not initialized")
    return state


def get_safe_player_team():
    """Get player team with defensive check."""
    player_team = Team.query.filter_by(is_player_team=True).first()
    if not player_team:
        abort(500, description="Player team not found")
    return player_team


@app.route("/")
def index():
    """Redirect to dashboard."""
    return redirect("/dashboard")


@app.route("/dashboard")
def dashboard():
    """Main dashboard showing team status, league table, and squad."""
    state = get_safe_state()
    player_team = get_safe_player_team()

    all_teams = Team.query.all()
    sorted_teams = sorted(
        all_teams,
        key=lambda t: (t.points, t.goals_for - t.goals_against),
        reverse=True
    )
    team_rank = sorted_teams.index(player_team) + 1

    players = Player.query.filter_by(team_id=player_team.id).all()
    current_match = get_current_match(state)
    recent_results = get_recent_results(state)
    unread_count = get_unread_count()
    pending_actions = get_pending_actions(state)

    return render_template(
        "dashboard.html",
        state=state,
        player_team=player_team,
        players=players,
        team_rank=team_rank,
        current_match=current_match,
        league_table=sorted_teams,
        recent_results=recent_results,
        unread_count=unread_count,
        pending_actions=pending_actions
    )


@app.route("/toggle_starter/<int:player_id>", methods=["GET", "POST"])
def toggle_starter(player_id):
    """Toggle player between starting XI and bench."""
    player = db.session.get(Player, player_id)

    if not player:
        flash("اللاعب غير موجود!")
        return redirect("/")

    if player.injury_days > 0:
        flash("هذا اللاعب مصاب ولا يمكنه البدء!")
        return redirect("/")

    current_starters = Player.query.filter_by(
        team_id=player.team_id,
        is_starting=True
    ).count()

    if not player.is_starting and current_starters >= 11:
        flash("لديك بالفعل 11 لاعب في التشكيلة الأساسية!")
    else:
        player.is_starting = not player.is_starting
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash("حدث خطأ أثناء تحديث التشكيلة!")
            return redirect("/")

    return redirect("/")


@app.route("/calendar")
def calendar():
    """Calendar view showing all matches by week."""
    state = get_safe_state()
    matches = Match.query.order_by(Match.week, Match.id).all()

    matches_by_week = {}
    for match in matches:
        if match.week not in matches_by_week:
            matches_by_week[match.week] = []
        matches_by_week[match.week].append(match)

    return render_template(
        "calendar.html",
        state=state,
        matches_by_week=matches_by_week
    )


@app.route("/match")
def match_view():
    """Match center with scouting report for upcoming match."""
    state = get_safe_state()
    match = get_current_match(state)
    scouting = None

    if match:
        opp = match.home_team if not match.home_team.is_player_team else match.away_team

        if opp and opp.players:
            danger_man = max(opp.players, key=lambda p: p.attack)

            starters = [p for p in opp.players if p.is_starting]
            avg_defense = sum(p.defense for p in starters) / len(starters) if starters else 0

            scouting = {
                "name": opp.name,
                "danger_man_name": danger_man.name if danger_man else "N/A",
                "danger_man_goals": danger_man.goals if danger_man else 0,
                "avg_defense": avg_defense,
                "morale_desc": "مرتفع" if opp.morale > 80 else "مستقر" if opp.morale > 50 else "منخفض"
            }

    return render_template(
        "match.html",
        match=match,
        state=state,
        scouting=scouting
    )


@app.route("/advance_day", methods=["GET", "POST"])
def advance_day():
    """Advance game by one day."""
    state = get_safe_state()

    if state.season_over:
        flash("الموسم انتهى!")
        return redirect("/")

    simulate_day(state)

    if is_match_day(state):
        match = get_current_match(state)
        if match:
            match_director(match, state)
            if state.current_week >= 10:
                state.season_over = True
                flash("الموسم انتهى! تهانينا للفوز 🥇")

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("حدث خطأ في تقدم اليوم!")
        return redirect("/")

    return redirect("/")


@app.route("/messages")
def messages():
    """View all messages."""
    state = get_safe_state()
    all_messages = get_messages()
    pending_offers = TransferOffer.query.filter_by(status='pending').all()
    return render_template("messages.html", messages=all_messages, state=state, pending_offers=pending_offers)


@app.route("/message/read/<int:message_id>", methods=["GET", "POST"])
def message_read(message_id):
    """Mark a message as read and view details."""
    mark_message_read(message_id)
    return redirect("/messages")


@app.route("/continue", methods=["GET", "POST"])
def continue_game():
    """Continue calendar simulation (main game loop)."""
    state = get_safe_state()

    if state.season_over:
        flash("الموسم انتهى!")
        return redirect("/dashboard")

    result = simulate_calendar(7, state)

    if result["matches_played"] > 0:
        flash(f"تم محاكاة {result['matches_played']} مباريات")

    if state.season_over:
        flash("الموسم انتهى! تهانينا 🥇")

    return redirect("/dashboard")


@app.route("/match_report/<int:match_id>")
def match_report(match_id):
    """View detailed match report."""
    state = get_safe_state()
    report = get_match_report(match_id)
    if not report:
        flash("المباراة غير موجودة!")
        return redirect("/dashboard")
    return render_template("match_report.html", report=report, state=state)


@app.route("/transfer_offer/<int:offer_id>/accept", methods=["GET", "POST"])
def accept_transfer(offer_id):
    """Accept a transfer offer."""
    offer = db.session.get(TransferOffer, offer_id)
    if not offer:
        flash("العرض غير موجود!")
        return redirect("/messages")
    
    player = db.session.get(Player, offer.player_id)
    from_team = db.session.get(Team, offer.from_team_id)
    to_team = db.session.get(Team, offer.to_team_id)
    
    if from_team.is_player_team:
        from_team.budget += offer.amount
        offer.status = 'accepted'
        flash(f"تم بيع {player.name} مقابل {offer.amount} دولار")
    else:
        if to_team.budget >= offer.amount:
            to_team.budget -= offer.amount
            player.team_id = to_team.id
            offer.status = 'accepted'
            flash(f"تم شراء {player.name} مقابل {offer.amount} دولار")
        else:
            flash("لا يوجد ميزانية كافية!")
    
    db.session.commit()
    return redirect("/messages")


@app.route("/transfer_offer/<int:offer_id>/reject", methods=["GET", "POST"])
def reject_transfer(offer_id):
    """Reject a transfer offer."""
    offer = db.session.get(TransferOffer, offer_id)
    if offer:
        offer.status = 'rejected'
        db.session.commit()
    return redirect("/messages")


@app.route("/advance_week", methods=["GET", "POST"])
def advance_week():
    """Advance game to next match day."""
    state = get_safe_state()

    if state.season_over:
        flash("الموسم انتهى!")
        return redirect("/")

    while not is_match_day(state) and not state.season_over:
        simulate_day(state)
        if is_match_day(state):
            match = get_current_match(state)
            if match:
                match_director(match, state)
                if state.current_week >= 10:
                    state.season_over = True
                    flash("الموسم انتهى! تهانينا 🥇")
                    break

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("حدث خطأ في تقدم الأسبوع!")
        return redirect("/")

    return redirect("/")


@app.errorhandler(405)
def method_not_allowed(e):
    """Handle 405 errors gracefully."""
    flash("حدث خطأ في الطلب، تم إعادتك للرئيسية.")
    return redirect("/")


@app.errorhandler(404)
def not_found(e):
    """Handle 404 errors gracefully."""
    flash("الصفحة غير موجودة!")
    return redirect("/")


@app.errorhandler(500)
def server_error(e):
    """Handle 500 errors gracefully."""
    flash("حدث خطأ في الخادم. يرجى المحاولة مرة أخرى!")
    return redirect("/")


if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(debug=True, port=5000)