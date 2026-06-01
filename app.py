import os
import json
import shutil
import random
from flask import Flask, render_template, redirect, flash, abort, session, request
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
    calculate_team_tier,
)
from procedural import generate_player_name, generate_player_stats

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///db.sqlite3"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.secret_key = os.environ.get("SECRET_KEY", "football_secret_key_prod")
csrf = CSRFProtect(app)
db.init_app(app)

SAVES_DIR = os.path.abspath("saves")
TEMPLATE_DB = os.path.abspath("template.db")
MAX_SLOTS = 5


@app.context_processor
def inject_globals():
    ctx = {"unread_count": 0, "manager_name": "", "manager_nationality": "",
           "player_team_name": "", "current_week": 0, "current_day": 0}
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


def ensure_saves_dir():
    os.makedirs(SAVES_DIR, exist_ok=True)


def get_slot_uri(slot_id: int) -> str:
    ensure_saves_dir()
    path = os.path.join(SAVES_DIR, f"save{slot_id}.sqlite3")
    return f"sqlite:///{path.replace('\\', '/')}"


def slot_exists(slot_id: int) -> bool:
    ensure_saves_dir()
    path = os.path.join(SAVES_DIR, f"save{slot_id}.sqlite3")
    return os.path.exists(path)


def get_active_slot() -> int:
    return session.get("active_save", 0)


def set_active_slot(slot_id: int) -> None:
    session["active_save"] = slot_id


def refresh_db_engine(uri: str) -> None:
    # Resolve relative SQLite URIs against the app's instance path
    if uri.startswith("sqlite:///"):
        path = uri[10:]
        if not (path.startswith("/") or (len(path) > 1 and path[1] == ":")):
            abs_path = os.path.abspath(os.path.join(app.instance_path, path))
            uri = f"sqlite:///{abs_path.replace('\\', '/')}"

    app.config["SQLALCHEMY_DATABASE_URI"] = uri
    
    if "sqlalchemy" in app.extensions:
        try:
            import sqlalchemy
            engines_dict = db._app_engines.get(app)
            if engines_dict is not None and None in engines_dict:
                engines_dict[None].dispose()
                engine_options = getattr(db, "_engine_options", {}).copy()
                new_engine = sqlalchemy.create_engine(uri, **engine_options)
                engines_dict[None] = new_engine
                db.session.remove()
                return
        except Exception:
            pass

    if "sqlalchemy" in app.extensions:
        del app.extensions["sqlalchemy"]
    db.init_app(app)



def init_template() -> None:
    uri = f"sqlite:///{TEMPLATE_DB.replace('\\', '/')}"
    refresh_db_engine(uri)
    db.create_all()
    
    if GameState.query.count() != 0:
        return

    leagues_data = [
        {"name": "Premier League", "tier": 1, "reputation": 90},
        {"name": "Championship", "tier": 2, "reputation": 70},
        {"name": "League One", "tier": 3, "reputation": 50},
        {"name": "League Two", "tier": 4, "reputation": 35},
    ]
    
    leagues = []
    for l_data in leagues_data:
        league = League(**l_data)
        db.session.add(league)
        leagues.append(league)
    db.session.commit()

    ENGLISH_TEAMS = {
        "Premier League": ["Arsenal", "Aston Villa", "Bournemouth", "Brentford", "Brighton & Hove Albion", "Chelsea", "Crystal Palace", "Everton", "Fulham", "Ipswich Town", "Leicester City", "Liverpool", "Manchester City", "Manchester United", "Newcastle United", "Nottingham Forest", "Southampton", "Tottenham Hotspur", "West Ham United", "Wolverhampton Wanderers"],
        "Championship": ["Blackburn Rovers", "Bristol City", "Burnley", "Cardiff City", "Coventry City", "Derby County", "Hull City", "Leeds United", "Luton Town", "Middlesbrough", "Millwall", "Norwich City", "Oxford United", "Plymouth Argyle", "Portsmouth", "Preston North End", "Queens Park Rangers", "Sheffield United", "Sheffield Wednesday", "Stoke City", "Sunderland", "Swansea City", "Watford", "West Bromwich Albion"],
        "League One": ["Barnsley", "Birmingham City", "Blackpool", "Bolton Wanderers", "Bristol Rovers", "Burton Albion", "Cambridge United", "Charlton Athletic", "Crawley Town", "Exeter City", "Huddersfield Town", "Leyton Orient", "Lincoln City", "Mansfield Town", "Northampton Town", "Peterborough United", "Reading", "Rotherham United", "Shrewsbury Town", "Stevenage", "Stockport County", "Wigan Athletic", "Wrexham", "Wycombe Wanderers"],
        "League Two": ["Accrington Stanley", "AFC Wimbledon", "Barrow", "Bradford City", "Bromley", "Carlisle United", "Cheltenham Town", "Chesterfield", "Colchester United", "Crewe Alexandra", "Doncaster Rovers", "Fleetwood Town", "Gillingham", "Grimsby Town", "Harrogate Town", "Milton Keynes Dons", "Morecambe", "Newport County", "Notts County", "Port Vale", "Salford City", "Swindon Town", "Tranmere Rovers", "Walsall"],
    }
    
    leagues_dict = {l.name: l for l in leagues}
    
    for league_name, team_names in ENGLISH_TEAMS.items():
        league = leagues_dict[league_name]
        for team_name in team_names:
            team = Team(
                name=team_name,
                morale=random.randint(60, 80),
                budget=1000000 * (5 - league.tier),
                reputation=league.reputation + random.randint(-5, 5),
                is_player_team=False,
                league_id=league.id
            )
            db.session.add(team)
            db.session.flush()
            
            squad_size = random.randint(18, 24)
            positions = ["GK"] * 2 + ["DEF"] * 4 + ["MID"] * 4 + ["FWD"] * 2
            remaining = squad_size - len(positions)
            positions += random.choices(["GK", "DEF", "MID", "FWD"], weights=[1, 3, 3, 2], k=remaining)
            random.shuffle(positions)
            for j, pos in enumerate(positions):
                region = random.choice(["Europe", "Middle_East", "South_America"])
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
    
    db.session.commit()

    for league in leagues:
        teams = Team.query.filter_by(league_id=league.id).all()
        for week in range(1, 39):
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
    state = GameState(current_day=1, current_week=1, season_over=False, user_league_id=player_league.id, manager_name="المدرب")
    db.session.add(state)
    db.session.commit()


def create_new_game(slot_id: int) -> bool:
    ensure_saves_dir()
    
    template_path = TEMPLATE_DB
    if not os.path.exists(template_path):
        # If missing, try to initialize it
        with app.app_context():
            init_template()
        if not os.path.exists(template_path):
            return False
    
    dest_path = os.path.join(SAVES_DIR, f"save{slot_id}.sqlite3")
    if os.path.exists(dest_path):
        return False
    
    shutil.copy2(template_path, dest_path)
    
    uri = get_slot_uri(slot_id)
    refresh_db_engine(uri)
    
    set_active_slot(slot_id)
    return True


def switch_to_slot(slot_id: int) -> bool:
    if not slot_exists(slot_id):
        return False
    
    uri = get_slot_uri(slot_id)
    refresh_db_engine(uri)
    db.create_all()
    
    set_active_slot(slot_id)
    return True


def delete_slot(slot_id: int) -> bool:
    path = os.path.join(SAVES_DIR, f"save{slot_id}.sqlite3")
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def get_safe_state():
    state = GameState.query.first()
    if not state:
        abort(500, description="Game state not initialized")
    return state


def get_safe_player_team():
    player_team = Team.query.filter_by(is_player_team=True).first()
    if not player_team:
        abort(500, description="Player team not found")
    return player_team


@app.route("/")
def index():
    slot = get_active_slot()
    if slot == 0:
        return redirect("/load")
    
    try:
        state = GameState.query.first()
        if not state:
            return redirect("/load")
        
        # Step 1: Coach setup
        if not state.manager_name or state.manager_name == "المدرب":
            return redirect(f"/setup/{slot}")
        
        # Step 2: Choose division/team
        player_team = Team.query.filter_by(is_player_team=True).first()
        if not player_team:
            return redirect(f"/select-division/{slot}")
            
    except Exception:
        return redirect("/load")
        
    return redirect("/dashboard")


@app.route("/dashboard")
def dashboard():
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
        pending_actions=pending_actions
    )


@app.route("/toggle_starter/<int:player_id>", methods=["GET", "POST"])
def toggle_starter(player_id):
    player = db.session.get(Player, player_id)
    if not player:
        flash("اللاعب غير موجود!")
        return redirect("/")
    if player.injury_days > 0:
        flash("هذا اللاعب مصاب ولا يمكنه البدء!")
        return redirect("/")

    current_starters = Player.query.filter_by(team_id=player.team_id, is_starting=True).count()

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
    state = get_safe_state()
    matches = Match.query.order_by(Match.week, Match.id).all()
    matches_by_week = {}
    for match in matches:
        if match.week not in matches_by_week:
            matches_by_week[match.week] = []
        matches_by_week[match.week].append(match)

    matches_json = {}
    for week, week_matches in matches_by_week.items():
        matches_json[str(week)] = []
        for m in week_matches:
            home = m.home_team
            away = m.away_team
            matches_json[str(week)].append({
                "home": home.name if home else "?",
                "away": away.name if away else "?",
                "home_is_player": home.is_player_team if home else False,
                "away_is_player": away.is_player_team if away else False,
                "score": m.result or "—",
                "played": m.played,
                "match_id": m.id
            })

    return render_template("calendar.html", state=state, matches_by_week=matches_by_week,
                          matches_json=json.dumps(matches_json), selected_week=state.current_week)


@app.route("/match")
def match_view():
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

    return render_template("match.html", match=match, state=state, scouting=scouting)


@app.route("/advance_day", methods=["GET", "POST"])
def advance_day():
    state = get_safe_state()
    if state.season_over:
        flash("الموسم انتهى!")
        return redirect("/")

    simulate_day(state)
    if is_match_day(state):
        match = get_current_match(state)
        if match:
            match_director(match, state)
            if state.current_week >= 38:
                state.season_over = True
                flash("الموسم انتهى! تهانينا")

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("حدث خطأ في تقدم اليوم!")
        return redirect("/")

    return redirect("/")


@app.route("/continue", methods=["GET", "POST"])
def continue_game():
    state = get_safe_state()
    if state.season_over:
        flash("الموسم انتهى!")
        return redirect("/dashboard")

    result = simulate_calendar(7, state)
    if result["matches_played"] > 0:
        flash(f"تم محاكاة {result['matches_played']} مباريات")

    if state.season_over:
        flash("الموسم انتهى! تهانينا")

    return redirect("/dashboard")


@app.route("/match_report/<int:match_id>")
def match_report(match_id):
    state = get_safe_state()
    report = get_match_report(match_id)
    if not report:
        flash("المباراة غير موجودة!")
        return redirect("/dashboard")
    return render_template("match_report.html", report=report, state=state)


@app.route("/transfer_offer/<int:offer_id>/accept", methods=["GET", "POST"])
def accept_transfer(offer_id):
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
    offer = db.session.get(TransferOffer, offer_id)
    if offer:
        offer.status = 'rejected'
        db.session.commit()
    return redirect("/messages")


@app.route("/advance_week", methods=["GET", "POST"])
def advance_week():
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
                if state.current_week >= 38:
                    state.season_over = True
                    flash("الموسم انتهى! تهانينا")
                    break

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("حدث خطأ في تقدم الأسبوع!")
        return redirect("/")

    return redirect("/")


@app.route("/load")
def load_game():
    ensure_saves_dir()
    active = get_active_slot()
    slots = []
    for i in range(1, MAX_SLOTS + 1):
        exists = slot_exists(i)
        meta = {}
        if exists:
            # Query metadata directly using sqlite3 to avoid interfering with Flask-SQLAlchemy's engine
            path = os.path.join(SAVES_DIR, f"save{i}.sqlite3")
            conn = None
            try:
                import sqlite3
                conn = sqlite3.connect(path)
                cursor = conn.cursor()
                
                # Check if game_state table exists
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='game_state'")
                if cursor.fetchone():
                    cursor.execute("SELECT current_day, current_week, manager_name, manager_nationality FROM game_state LIMIT 1")
                    state_row = cursor.fetchone()
                    
                    cursor.execute("SELECT name FROM team WHERE is_player_team = 1 LIMIT 1")
                    team_row = cursor.fetchone()
                    
                    if state_row and team_row:
                        meta = {
                            "day": state_row[0] or 1,
                            "week": state_row[1] or 1,
                            "manager": state_row[2] or "المدرب",
                            "nationality": state_row[3] or "",
                            "team": team_row[0]
                        }
            except Exception:
                pass
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
        slots.append({"id": i, "exists": exists, **meta})
    
    if active > 0:
        uri = get_slot_uri(active)
        refresh_db_engine(uri)
        state = GameState.query.first()
        player_team = Team.query.filter_by(is_player_team=True).first()
    else:
        # If no active slot is loaded, restore pointing to standard db.sqlite3
        refresh_db_engine("sqlite:///db.sqlite3")
        state = None
        player_team = None

    return render_template("load.html", slots=slots, active=active, state=state, player_team=player_team)


@app.route("/load/<int:slot_id>", methods=["GET", "POST"])
def load_slot(slot_id):
    if not slot_exists(slot_id):
        flash("الحفظ غير موجود!")
        return redirect("/load")
    
    if not switch_to_slot(slot_id):
        flash("فشل في تحميل الحفظ!")
        return redirect("/load")
    
    return redirect("/dashboard")


@app.route("/new/<int:slot_id>", methods=["GET", "POST"])
def new_game(slot_id):
    if slot_id < 1 or slot_id > MAX_SLOTS:
        flash("رقم Slot غير صالح!")
        return redirect("/load")
    
    if slot_exists(slot_id):
        flash("هذا الحفظ مشغول! احذفه أولاً.")
        return redirect("/load")
    
    if not create_new_game(slot_id):
        flash("فشل في إنشاء لعبة جديدة!")
        return redirect("/load")
    
    return redirect(f"/setup/{slot_id}")


@app.route("/setup/<int:slot_id>", methods=["GET", "POST"])
def setup_coach(slot_id):
    if slot_id < 1 or slot_id > MAX_SLOTS:
        flash("رقم Slot غير صالح!")
        return redirect("/load")

    if not slot_exists(slot_id):
        flash("الحفظ غير موجود!")
        return redirect("/load")

    if not switch_to_slot(slot_id):
        flash("فشل في تحميل الحفظ!")
        return redirect("/load")

    if request.method == "POST":
        manager_name = request.form.get("manager_name", "").strip()
        manager_nationality = request.form.get("manager_nationality", "").strip()
        if not manager_name:
            manager_name = "المدرب"
        if not manager_nationality:
            manager_nationality = "غير محدد"

        state = GameState.query.first()
        if state:
            try:
                state.manager_name = manager_name
                state.manager_nationality = manager_nationality
                db.session.commit()
            except Exception:
                db.session.rollback()

        return redirect(f"/select-division/{slot_id}")

    return render_template("setup.html", slot_id=slot_id)


@app.route("/select-division/<int:slot_id>")
def select_division(slot_id):
    if not slot_exists(slot_id):
        flash("الحفظ غير موجود!")
        return redirect("/load")

    if not switch_to_slot(slot_id):
        flash("فشل في تحميل الحفظ!")
        return redirect("/load")

    state = GameState.query.first()
    leagues = League.query.order_by(League.tier).all()
    return render_template("select_division.html", slot_id=slot_id, leagues=leagues, state=state)


@app.route("/select-team/<int:slot_id>/<int:league_id>")
def select_team(slot_id, league_id):
    if not slot_exists(slot_id):
        flash("الحفظ غير موجود!")
        return redirect("/load")

    if not switch_to_slot(slot_id):
        flash("فشل في تحميل الحفظ!")
        return redirect("/load")

    league = db.session.get(League, league_id)
    if not league:
        flash("الدوري غير موجود!")
        return redirect(f"/select-division/{slot_id}")

    teams = Team.query.filter_by(league_id=league_id).all()
    
    enriched = []
    for team in teams:
        tier_data = calculate_team_tier(team.id)
        enriched.append({
            "id": team.id,
            "name": team.name,
            "league": league.name,
            "tier": tier_data["tier"],
            "rating": tier_data["rating"],
            "attack": tier_data["attack"],
            "defense": tier_data["defense"],
            "budget": team.budget
        })

    state = GameState.query.first()
    return render_template("select_team.html", slot_id=slot_id, teams=enriched, league=league, state=state)


@app.route("/new-manager/<int:slot_id>", methods=["GET", "POST"])
def new_manager(slot_id):
    if slot_id < 1 or slot_id > MAX_SLOTS:
        flash("رقم Slot غير صالح!")
        return redirect("/load")
    
    if not slot_exists(slot_id):
        flash("الحفظ غير موجود!")
        return redirect("/load")
    
    if not switch_to_slot(slot_id):
        flash("فشل في تحميل الحفظ!")
        return redirect("/load")
    
    if request.method == "POST":
        manager_name = request.form.get("manager_name", "").strip()
        if not manager_name:
            manager_name = "المدرب"
        
        state = GameState.query.first()
        if state:
            try:
                state.manager_name = manager_name
                db.session.commit()
            except Exception:
                db.session.rollback()
        
        return redirect(f"/select-team/{slot_id}")
    
    return render_template("new_manager.html", slot_id=slot_id)


@app.route("/exit")
def exit_to_menu():
    session.pop("active_save", None)
    return redirect("/load")


@app.route("/delete/<int:slot_id>", methods=["GET", "POST"])
def delete_game(slot_id):
    active = get_active_slot()
    if active == slot_id:
        session.pop("active_save", None)
    
    # If the database currently loaded is the one being deleted, switch back to the default database
    target_uri = get_slot_uri(slot_id)
    if app.config.get("SQLALCHEMY_DATABASE_URI") == target_uri:
        db.session.remove()
        refresh_db_engine("sqlite:///db.sqlite3")
        
    if not delete_slot(slot_id):
        flash("فشل في حذف الحفظ!")
    else:
        flash("تم حذف الحفظ!")
    
    return redirect("/load")


@app.route("/manager-form/<int:slot_id>/<int:team_id>", methods=["GET", "POST"])
def manager_form(slot_id, team_id):
    if slot_id < 1 or slot_id > MAX_SLOTS:
        flash("رقم Slot غير صالح!")
        return redirect("/load")
    
    if not slot_exists(slot_id):
        flash("الحفظ غير موجود!")
        return redirect("/load")
    
    if not switch_to_slot(slot_id):
        flash("فشل في تحميل الحفظ!")
        return redirect("/load")
    
    team = db.session.get(Team, team_id)
    if not team:
        flash("الفريق غير موجود!")
        return redirect(f"/select-division/{slot_id}")
    
    for t in Team.query.all():
        t.is_player_team = (t.id == team_id)
    
    state = GameState.query.first()
    if state:
        state.user_league_id = team.league_id
    
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("فشل في حفظ البيانات!")
        return redirect(f"/select-division/{slot_id}")
    
    flash(f"مرحباً {state.manager_name}! تم اختيار فريق {team.name} بنجاح!")
    return redirect("/dashboard")


@app.route("/squad")
def squad():
    state = get_safe_state()
    player_team = get_safe_player_team()
    players = Player.query.filter_by(team_id=player_team.id).all()
    
    starters = [p for p in players if p.is_starting and p.injury_days == 0]
    bench = [p for p in players if not p.is_starting or p.injury_days > 0]
    
    tiers_data = calculate_team_tier(player_team.id)
    avg_attack = tiers_data["attack"]
    avg_defense = tiers_data["defense"]
    
    return render_template("squad.html", state=state, player_team=player_team,
                          players=players, starters=starters, bench=bench,
                          avg_attack=avg_attack, avg_defense=avg_defense,
                          tier=tiers_data["tier"], rating=tiers_data["rating"])


@app.route("/player_profile/<int:player_id>")
def player_profile(player_id):
    player = db.session.get(Player, player_id)
    if not player:
        return {"error": "not found"}, 404
    
    ratings = PlayerRating.query.filter_by(player_id=player_id).order_by(PlayerRating.id.desc()).limit(10).all()
    ratings_data = [{"rating": r.rating, "goals": r.goals, "assists": r.assists,
                     "match_id": r.match_id} for r in ratings]
    
    avg_rating = round(sum(r.rating for r in ratings) / len(ratings), 1) if ratings else 0
    
    return {
        "id": player.id,
        "name": player.name,
        "position": player.position,
        "attack": player.attack,
        "defense": player.defense,
        "potential": player.potential,
        "age": player.age,
        "height": player.height,
        "weight": player.weight,
        "personality": player.personality,
        "region": player.region,
        "market_value": player.market_value,
        "fitness": player.fitness,
        "injury_days": player.injury_days,
        "goals": player.goals,
        "assists": player.assists,
        "matches_played": player.matches_played,
        "avg_rating": avg_rating,
        "recent_ratings": ratings_data
    }


@app.route("/facilities")
def facilities():
    state = get_safe_state()
    player_team = get_safe_player_team()
    
    upgrade_cost_training = (player_team.training_facilities or 3) * 200000
    upgrade_cost_youth = (player_team.youth_facilities or 3) * 150000
    
    return render_template("facilities.html", state=state, player_team=player_team,
                          upgrade_cost_training=upgrade_cost_training,
                          upgrade_cost_youth=upgrade_cost_youth)


@app.route("/upgrade_facility/<facility_type>", methods=["POST"])
def upgrade_facility(facility_type):
    state = get_safe_state()
    player_team = get_safe_player_team()
    
    if facility_type == "training":
        level = player_team.training_facilities or 3
        cost = level * 200000
        if level >= 10:
            flash("مرافق التدريب في أقصى مستوى بالفعل!")
        elif player_team.budget >= cost:
            player_team.training_facilities = level + 1
            player_team.budget -= cost
            flash(f"تم ترقية مرافق التدريب إلى المستوى {level + 1}!")
        else:
            flash("الميزانية غير كافية!")
    elif facility_type == "youth":
        level = player_team.youth_facilities or 3
        cost = level * 150000
        if level >= 10:
            flash("أكاديمية الشباب في أقصى مستوى بالفعل!")
        elif player_team.budget >= cost:
            player_team.youth_facilities = level + 1
            player_team.budget -= cost
            flash(f"تم ترقية أكاديمية الشباب إلى المستوى {level + 1}!")
        else:
            flash("الميزانية غير كافية!")
    
    db.session.commit()
    return redirect("/facilities")


@app.route("/scouting")
def scouting():
    state = get_safe_state()
    player_team = get_safe_player_team()
    
    other_players = Player.query.filter(Player.team_id != player_team.id).order_by(Player.market_value.desc()).all()
    
    return render_template("scouting.html", state=state, player_team=player_team, players=other_players)


@app.route("/medical")
def medical():
    state = get_safe_state()
    player_team = get_safe_player_team()
    players = Player.query.filter_by(team_id=player_team.id).all()
    
    injured = [p for p in players if p.injury_days > 0]
    fit = [p for p in players if p.injury_days == 0]
    
    return render_template("medical.html", state=state, player_team=player_team,
                          injured=injured, fit=fit)


@app.route("/simulate_to_date", methods=["POST"])
def simulate_to_date():
    state = get_safe_state()
    if state.season_over:
        flash("الموسم انتهى!")
        return redirect("/dashboard")
    
    target_week = request.form.get("target_week", type=int)
    if not target_week or target_week <= state.current_week:
        flash("يرجى اختيار أسبوع قادم!")
        return redirect("/calendar")
    
    if target_week > 38:
        target_week = 38
    
    # Speed up SQLite bulk simulation by executing optimization pragmas outside transaction
    try:
        db.session.commit()
        raw_conn = db.engine.raw_connection()
        cursor = raw_conn.cursor()
        cursor.execute("PRAGMA synchronous = OFF;")
        cursor.execute("PRAGMA journal_mode = MEMORY;")
        cursor.close()
    except Exception:
        pass
        
    # Calculate days to simulate
    days_to_sim = ((target_week - 1) * 7) - state.current_day
    
    matches_played = 0
    if days_to_sim > 0:
        result = simulate_calendar(days_to_sim, state)
        matches_played = result.get("matches_played", 0)
        
    if state.current_week >= 38:
        state.season_over = True
        
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("حدث خطأ في المحاكاة!")
        return redirect("/calendar")
    
    flash(f"تمت المحاكاة! وصلت للأسبوع {state.current_week}، لعبت {matches_played} مباريات")
    return redirect("/dashboard")


@app.route("/match_history")
def match_history():
    state = get_safe_state()
    player_team = get_safe_player_team()
    
    player_matches = Match.query.filter(
        ((Match.home_team_id == player_team.id) | (Match.away_team_id == player_team.id)) &
        (Match.played == True)
    ).order_by(Match.week.desc()).limit(30).all()
    
    return render_template("match_history.html", state=state, player_team=player_team, matches=player_matches)


@app.route("/inbox_api/<int:message_id>")
def inbox_api(message_id):
    msg = db.session.get(Message, message_id)
    if not msg:
        return {"error": "not found"}, 404
    mark_message_read(message_id)
    return {
        "id": msg.id,
        "title": msg.title,
        "content": msg.content,
        "type": msg.type,
        "read": True,
        "urgent": msg.urgent,
        "created_day": msg.created_day
    }


@app.route("/messages")
def messages():
    state = None
    player_team = None
    try:
        state = get_safe_state()
        player_team = get_safe_player_team()
    except Exception:
        pass
    
    msgs = Message.query.order_by(Message.created_day.desc()).all()
    offers = TransferOffer.query.filter(
        (TransferOffer.status == 'pending') &
        ((TransferOffer.from_team_id == (player_team.id if player_team else 0)) |
         (TransferOffer.to_team_id == (player_team.id if player_team else 0)))
    ).all()
    
    first_msg = msgs[0] if msgs else None
    
    return render_template("messages.html", state=state, player_team=player_team,
                          messages=msgs, pending_offers=offers, first_msg=first_msg)


@app.route("/message_read/<int:message_id>")
def message_read(message_id):
    mark_message_read(message_id)
    return redirect("/messages")


@app.route("/buy_player/<int:player_id>", methods=["POST"])
def buy_player(player_id):
    state = get_safe_state()
    player_team = get_safe_player_team()
    
    target = db.session.get(Player, player_id)
    if not target:
        flash("اللاعب غير موجود!")
        return redirect("/scouting")
    
    if target.team_id == player_team.id:
        flash("هذا اللاعب في فريقك بالفعل!")
        return redirect("/scouting")
    
    value = target.market_value
    if player_team.budget >= value:
        player_team.budget -= value
        target.team_id = player_team.id
        target.is_starting = False
        
        msg = Message(
            title="انتقال جديد",
            content=f"تم شراء {target.name} مقابل {value:,} دولار!",
            type="transfer",
            created_day=state.current_day
        )
        db.session.add(msg)
        db.session.commit()
        flash(f"تم شراء {target.name} بنجاح!")
    else:
        flash(f"الميزانية غير كافية! تحتاج {value:,} دولار ولديك {player_team.budget:,} دولار")
    
    return redirect("/scouting")


@app.errorhandler(405)
def method_not_allowed(e):
    flash("حدث خطأ في الطلب، تم إعادتك للرئيسية.")
    return redirect("/")


@app.errorhandler(404)
def not_found(e):
    flash("الصفحة غير موجودة!")
    return redirect("/")


@app.errorhandler(500)
def server_error(e):
    # Avoid redirect loop if error occurs on load page
    if request.path == "/load":
        return "حدث خطأ جسيم في الخادم. يرجى المحاولة لاحقاً.", 500
    flash("حدث خطأ في الخادم. تم إعادتك لصفحة الحفظ.")
    return redirect("/load")


if __name__ == "__main__":
    ensure_saves_dir()
    if not os.path.exists(TEMPLATE_DB):
        with app.app_context():
            init_template()
    app.run(debug=True, port=5000)