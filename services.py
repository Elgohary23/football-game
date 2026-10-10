"""Service layer (Tip 3).

All non-trivial business rules (save slots, template building, formation
validation, staff/transfer actions, league-table caching) live here so route
handlers only glue HTTP to services and templates.
"""

import logging
import os
import random
import shutil
import sqlite3
from collections import namedtuple
from datetime import datetime
from functools import lru_cache

from flask import abort, current_app, session

from config import (
    DB_FILENAME,
    DEFAULT_BUDGET,
    BUDGET_JITTER,
    BUDGET_REPUTATION_STEP,
    LOG_FILE,
    LOG_FORMAT,
    MAX_FACILITY_LEVEL,
    MAX_SAVE_SLOTS,
    PROMOTION_BONUS_PER_TIER,
    RIVALS,
    SCOUT_KNOWLEDGE_GAIN,
    SCOUT_KNOWLEDGE_THRESHOLD,
    SCOUT_REGIONS,
    SELL_ON_CLAUSE_DISCOUNT_RATIO,
    SELL_ON_CLAUSE_RANGE,
    STAFF_FIRE_COMPENSATION_RATIO,
    STAFF_MARKET_POOL_LIMIT,
    STARTERS_LIMIT,
    TEMPLATE_DB_FILENAME,
    TEMPLATE_SCHEMA_VERSION,
    TIER_BUDGETS,
    TOP_LEAGUE_ROUNDS,
    TRAINING_COST_PER_LEVEL,
    YOUTH_COST_PER_LEVEL,
)
from models import (
    db,
    Team,
    Player,
    GameState,
    Match,
    League,
    Staff,
    Message,
    TransferOffer,
    SellOnClause,
)
from procedural import (
    generate_player_name,
    generate_player_stats,
    generate_player_traits,
)

logger = logging.getLogger(__name__)

TeamRow = namedtuple(
    "TeamRow",
    "id name morale budget points goals_for goals_against wins losses draws reputation",
)


def setup_logging() -> None:
    """Configure file logging so background errors persist (Tip 30)."""
    root = logging.getLogger()
    if any(isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", "").endswith(LOG_FILE) for h in root.handlers):
        return
    root.setLevel(logging.INFO)
    handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(handler)


# --------------------------------------------------------------------------
# Save-slot + database engine management
# --------------------------------------------------------------------------
def ensure_saves_dir() -> None:
    os.makedirs(saves_base(), exist_ok=True)


def saves_base() -> str:
    return os.path.abspath("saves")


def template_db_path() -> str:
    return os.path.join(saves_base(), TEMPLATE_DB_FILENAME)


def instance_db_path() -> str:
    return os.path.join(current_app.instance_path, DB_FILENAME)


def get_slot_uri(slot_id: int) -> str:
    ensure_saves_dir()
    path = os.path.join(saves_base(), f"save{slot_id}.sqlite3")
    return f"sqlite:///{path.replace('\\', '/')}"


def slot_exists(slot_id: int) -> bool:
    ensure_saves_dir()
    path = os.path.join(saves_base(), f"save{slot_id}.sqlite3")
    return os.path.exists(path)


def get_active_slot() -> int:
    return session.get("active_save", 0)


def set_active_slot(slot_id: int) -> None:
    session["active_save"] = slot_id


def delete_save_file(slot_id: int) -> bool:
    path = os.path.join(saves_base(), f"save{slot_id}.sqlite3")
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def ensure_indexes() -> None:
    """Add indexes that ``create_all`` cannot apply to existing DBs (Tip 12)."""
    from sqlalchemy import text as sql_text

    statements = [
        "CREATE INDEX IF NOT EXISTS idx_match_week_played ON match (week, played)",
        "CREATE INDEX IF NOT EXISTS idx_match_home_team ON match (home_team_id)",
        "CREATE INDEX IF NOT EXISTS idx_match_away_team ON match (away_team_id)",
        "CREATE INDEX IF NOT EXISTS idx_team_id ON player (team_id)",
        "CREATE INDEX IF NOT EXISTS idx_position_attack_age ON player (position, attack, age)",
    ]
    for stmt in statements:
        try:
            db.session.execute(sql_text(stmt))
        except Exception:
            logger.debug("Skipping index: %s", stmt)
    try:
        db.session.commit()
    except Exception:
        pass


def refresh_db_engine(uri: str) -> None:
    if uri.startswith("sqlite:///"):
        path = uri[10:]
        if not (path.startswith("/") or (len(path) > 1 and path[1] == ":")):
            abs_path = os.path.abspath(os.path.join(current_app.instance_path, path))
            uri = f"sqlite:///{abs_path.replace('\\', '/')}"

    current_app.config["SQLALCHEMY_DATABASE_URI"] = uri

    if "sqlalchemy" in current_app.extensions:
        try:
            import sqlalchemy

            engines_dict = db._app_engines.get(current_app._get_current_object())
            if engines_dict is not None and None in engines_dict:
                engines_dict[None].dispose()
                engine_options = getattr(db, "_engine_options", {}).copy()
                new_engine = sqlalchemy.create_engine(uri, **engine_options)
                engines_dict[None] = new_engine
                db.session.remove()
                ensure_indexes()
                return
        except Exception:
            pass

    if "sqlalchemy" in current_app.extensions:
        del current_app.extensions["sqlalchemy"]
    db.init_app(current_app._get_current_object())
    ensure_indexes()


def switch_to_slot(slot_id: int) -> bool:
    if not slot_exists(slot_id):
        return False
    refresh_db_engine(get_slot_uri(slot_id))
    db.create_all()
    ensure_indexes()
    set_active_slot(slot_id)
    return True


def get_slot_metadata(slot_id: int) -> dict:
    path = os.path.join(saves_base(), f"save{slot_id}.sqlite3")
    meta: dict = {}
    if not os.path.exists(path):
        return meta
    conn = None
    try:
        conn = sqlite3.connect(path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='game_state'")
        if cursor.fetchone():
            cursor.execute(
                "SELECT current_day, current_week, manager_name, manager_nationality FROM game_state LIMIT 1"
            )
            state_row = cursor.fetchone()
            cursor.execute("SELECT name FROM team WHERE is_player_team = 1 LIMIT 1")
            team_row = cursor.fetchone()
            if state_row and team_row:
                meta = {
                    "day": state_row[0] or 1,
                    "week": state_row[1] or 1,
                    "manager": state_row[2] or "المدرب",
                    "nationality": state_row[3] or "",
                    "team": team_row[0],
                }
    except Exception:
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return meta


# --------------------------------------------------------------------------
# Safe query helpers
# --------------------------------------------------------------------------
def safe_state() -> GameState:
    state = GameState.query.first()
    if not state:
        abort(500, description="Game state not initialized")
    return state


def safe_player_team() -> Team:
    player_team = Team.query.filter_by(is_player_team=True).first()
    if not player_team:
        abort(500, description="Player team not found")
    return player_team


def get_season_rounds(league_id: int | None = None) -> int:
    if league_id:
        league = db.session.get(League, league_id)
        if league:
            return league.rounds()
    return TOP_LEAGUE_ROUNDS


def get_budget_for_tier(tier: int | None) -> int:
    """Starting budget for a club based on its league tier.

    Single source of truth for all tier-based budgets (template build,
    team selection, tests). Falls back to DEFAULT_BUDGET for unknown tiers.
    """
    try:
        tier_key = int(tier) if tier is not None else 0
    except (TypeError, ValueError):
        tier_key = 0
    return TIER_BUDGETS.get(tier_key, DEFAULT_BUDGET)


def calculate_club_budget(tier: int | None, reputation: int | None,
                          league_reputation: int | None) -> int:
    """Starting budget for one club: tier base + reputation spread + jitter.

    The old template gave every club in a division the identical flat tier
    base. This keeps the tier base (divisions stay ordered) but differentiates
    clubs: each reputation point above/below the league's base reputation
    shifts the budget by ``BUDGET_REPUTATION_STEP``, with a small random
    jitter on top. Used by the template build; team selection keeps the
    stored value so the variation survives into saves.
    """
    base = get_budget_for_tier(tier)
    try:
        diff = int(reputation or 0) - int(league_reputation or 0)
    except (TypeError, ValueError):
        diff = 0
    jitter = random.uniform(1 - BUDGET_JITTER, 1 + BUDGET_JITTER)
    return max(0, int((base + diff * BUDGET_REPUTATION_STEP) * jitter))


def calculate_team_tier(team_id: int) -> dict:
    """Display ratings + tier for a team (select_team / squad pages).

    Attack/defence come from ``Team.get_strength()`` (best-XI). The tier is the
    team's league tier (PL=1 .. League Two=4).
    """
    team = db.session.get(Team, team_id)
    if not team:
        return {"tier": 4, "rating": 0.0, "attack": 0.0, "defense": 0.0}

    attack, defense = team.get_strength()
    rating = round((attack + defense) / 2.0, 1)
    tier = team.league.tier if team.league else 1
    return {
        "tier": tier,
        "rating": rating,
        "attack": round(attack, 1),
        "defense": round(defense, 1),
    }


# --------------------------------------------------------------------------
# League table with per-day cache (Tip 11)
# --------------------------------------------------------------------------
def _active_db_key() -> str:
    try:
        engine_url = db.session.get_bind().engine.url
        return f"{engine_url.drivername}://{engine_url.database}"
    except Exception:
        return "default"


def _build_league_rows(league_id: int | None) -> list[TeamRow]:
    query = Team.query
    if league_id:
        query = query.filter_by(league_id=league_id)
    teams = query.all()
    sorted_teams = Team.table_sort_pipeline(teams)
    return [
        TeamRow(
            id=t.id,
            name=t.name,
            morale=t.morale,
            budget=t.budget,
            points=t.points,
            goals_for=t.goals_for,
            goals_against=t.goals_against,
            wins=t.wins,
            losses=t.losses,
            draws=t.draws,
            reputation=t.reputation,
        )
        for t in sorted_teams
    ]


@lru_cache(maxsize=128)
def _league_table_cached(league_id: int | None, db_key: str, day: int) -> list[TeamRow]:
    return _build_league_rows(league_id)


def get_league_table(league_id: int | None = None) -> list[TeamRow]:
    """Sorted league table; cached per (save-file, day) so it is only rebuilt
    when the underlying data actually changes (Tip 11)."""
    state = GameState.query.first()
    day = state.current_day if state else 0
    return _league_table_cached(league_id, _active_db_key(), day)


def get_team_rank(league_id: int | None, player_team: Team) -> int | None:
    rows = get_league_table(league_id)
    for index, row in enumerate(rows, start=1):
        if row.id == player_team.id:
            return index
    return None


def invalidate_league_cache() -> None:
    _league_table_cached.cache_clear()


def get_team_rows(league_id: int | None = None) -> list[TeamRow]:
    return get_league_table(league_id)


# --------------------------------------------------------------------------
# Formation (strict backend validation, Tips 27 & 10)
# --------------------------------------------------------------------------
def validate_formation(player_team: Team, positions) -> tuple[bool, str]:
    if not isinstance(positions, dict) or not positions:
        return False, "بيانات غير صالحة"

    try:
        player_ids = [int(k) for k in positions.keys()]
    except (TypeError, ValueError):
        return False, "بيانات غير صالحة"

    if len(player_ids) > STARTERS_LIMIT:
        return False, "لا يمكن وضع أكثر من 11 لاعباً في الملعب"

    if len(player_ids) == STARTERS_LIMIT:
        team_ids = {p.id for p in player_team.players}
        if not all(pid in team_ids for pid in player_ids):
            return False, "يحتوي التشكيل على لاعبين غير صالحين"

        gk_ids = {p.id for p in player_team.players if p.position == "GK"}
        gk_count = sum(1 for pid in player_ids if pid in gk_ids)
        if gk_count != 1:
            return False, "يجب أن يكون هناك حارس مرمى واحد بالضبط في التشكيل الأساسي"

    return True, ""


def apply_formation(player_team: Team, positions) -> None:
    """Persist formation; the model column is JSON so no manual dumps (Tip 10)."""
    player_team.formation_data = positions
    ids = {int(k) for k in positions.keys()}
    for p in player_team.players:
        p.is_starting = p.id in ids


# --------------------------------------------------------------------------
# Staff actions (authorized, Tips 23 & 28)
# --------------------------------------------------------------------------
STAFF_DEFS = [
    {"role": "fitness", "specialty": "مدرب لياقة بدنية", "level": 2, "salary": 60000,
     "fitness_bonus": 0.5, "injury_reduction": 0.4},
    {"role": "goalkeeper", "specialty": "مدرب حراس", "level": 2, "salary": 55000,
     "defense_bonus": 0.4},
    {"role": "analyst", "specialty": "محلل أداء", "level": 2, "salary": 50000,
     "attack_bonus": 0.3, "defense_bonus": 0.3},
    {"role": "fitness", "specialty": "مدرب لياقة", "level": 1, "salary": 40000,
     "fitness_bonus": 0.3, "injury_reduction": 0.3},
    {"role": "goalkeeper", "specialty": "مدرب حراس", "level": 1, "salary": 35000,
     "defense_bonus": 0.3},
    {"role": "analyst", "specialty": "محلل أداء", "level": 3, "salary": 80000,
     "attack_bonus": 0.5, "defense_bonus": 0.5},
    {"role": "youth", "specialty": "مدرب شباب", "level": 2, "salary": 45000,
     "youth_bonus": 0.5},
    {"role": "youth", "specialty": "مدرب شباب", "level": 1, "salary": 30000,
     "youth_bonus": 0.3},
    {"role": "scout", "specialty": "كشاف أوروبا", "level": 2, "salary": 40000,
     "scouting_hint": "Europe"},
    {"role": "scout", "specialty": "كشاف شاب واعد", "level": 1, "salary": 25000,
     "scouting_hint": "South_America"},
    {"role": "scout", "specialty": "كشاف الأقاليم", "level": 3, "salary": 60000,
     "scouting_hint": None},
]


def generate_staff_pool() -> tuple[bool, str]:
    existing = Staff.query.filter_by(team_id=None).count()
    if existing > STAFF_MARKET_POOL_LIMIT:
        return False, "توجد بالفعل طاقم تدريبي متاح!"

    for sd in STAFF_DEFS:
        s = Staff(
            name=generate_player_name("Europe"),
            role=sd["role"],
            specialty=sd["specialty"],
            level=sd["level"],
            salary=sd["salary"] * sd["level"],
            attack_bonus=sd.get("attack_bonus", 0.0),
            defense_bonus=sd.get("defense_bonus", 0.0),
            fitness_bonus=sd.get("fitness_bonus", 0.0),
            youth_bonus=sd.get("youth_bonus", 0.0),
            injury_reduction=sd.get("injury_reduction", 0.0),
            region=sd.get("scouting_hint"),
            team_id=None,
        )
        db.session.add(s)
    return True, "تم توليد طاقم تدريبي جديد متاح للتعاقد!"


def hire_staff_member(player_team: Team, staff: Staff) -> tuple[bool, str]:
    if not staff:
        return False, "الموظف غير موجود!"

    if staff.team_id and staff.team_id != player_team.id:
        return False, "هذا الموظف يعمل لدى فريق آخر!"

    if staff.team_id is not None:
        return False, "هذا الموظف يعمل بالفعل في فريقك!"

    if player_team.budget < staff.salary:
        return False, f"الميزانية غير كافية! راتب {staff.name} هو {staff.salary:,} دولار"

    player_team.budget -= staff.salary
    staff.team_id = player_team.id
    return True, f"تم تعيين {staff.name} في الطاقم التدريبي!"


def fire_staff_member(player_team: Team, staff: Staff) -> tuple[bool, str]:
    if not staff:
        return False, "الموظف غير موجود!"

    if staff.team_id != player_team.id:
        return False, "هذا الموظف لا يعمل في فريقك!"

    compensation = int(staff.salary * STAFF_FIRE_COMPENSATION_RATIO)
    player_team.budget = max(0, player_team.budget - compensation)
    staff.team_id = None
    return True, f"تمت إقالة {staff.name}. تم دفع شرط جزائي قدره {compensation:,} دولار"


# --------------------------------------------------------------------------
# Transfers (authorization enforced, Tip 28)
# --------------------------------------------------------------------------
def purchase_player(
    player_team: Team,
    target: Player,
    day: int,
    sell_on_clause: float = 0.0,
) -> tuple[bool, str]:
    if not target:
        return False, "اللاعب غير موجود!"

    if target.team_id == player_team.id:
        return False, "هذا اللاعب في فريقك بالفعل!"

    # FM: rivalry rejection — loyal players refuse to betray their club.
    rival = RIVALS.get(target.team.name if target.team else "")
    if rival and rival == player_team.name and target.personality == "Loyal":
        return False, f"اللاعب {target.name} يرفض الانتقال إلى الغريم التقليدي {player_team.name}!"

    value = target.market_value
    if sell_on_clause:
        value = int(value * (1.0 - SELL_ON_CLAUSE_DISCOUNT_RATIO * sell_on_clause))

    if player_team.budget < value:
        return False, f"الميزانية غير كافية! تحتاج {value:,} دولار ولديك {player_team.budget:,} دولار"

    player_team.budget -= value
    old_club_id = target.team_id
    seller = db.session.get(Team, old_club_id) if old_club_id else None
    if seller is not None:
        seller.budget = (seller.budget or 0) + value
    target.team_id = player_team.id
    target.is_starting = False

    # FM: sell-on clause keeps a cut for the selling club on future sales.
    if sell_on_clause and old_club_id:
        clause = SellOnClause(
            player_id=target.id,
            club_id=old_club_id,
            percentage=min(0.3, max(0.0, sell_on_clause)),
        )
        db.session.add(clause)

    msg = Message(
        title="انتقال جديد",
        content=f"تم شراء {target.name} مقابل {value:,} دولار"
                + (f" مع بند بيع مستقبلي {int(sell_on_clause * 100)}%" if sell_on_clause else "") + "!",
        type="transfer",
        created_day=day,
    )
    db.session.add(msg)
    return True, f"تم شراء {target.name} بنجاح!"


def _offer_involves_player_team(offer: TransferOffer) -> bool:
    player_team = Team.query.filter_by(is_player_team=True).first()
    if not player_team:
        return False
    return (
        offer.from_team_id == player_team.id or offer.to_team_id == player_team.id
    )


def _pay_sell_on_clauses(player: Player, amount: int, seller_budget_holder: Team) -> None:
    """FM: previous clubs get a percentage of every future sale."""
    clauses = SellOnClause.query.filter_by(player_id=player.id).all()
    for clause in clauses:
        club = db.session.get(Team, clause.club_id)
        if club and clause.percentage:
            cut = int(amount * min(0.3, max(0.0, clause.percentage)))
            club.budget = (club.budget or 0) + cut
        db.session.delete(clause)
    # remove any clause that referenced the current seller so it is not
    # paid to ourselves twice
    SellOnClause.query.filter_by(
        player_id=player.id, club_id=seller_budget_holder.id
    ).update({"percentage": 0.0})


def swap_offer(
    player_team: Team,
    target: Player,
    offered_player: Player,
    cash: float,
    day: int,
) -> tuple[bool, str]:
    """FM: player-swap deals — our player + cash for another club's star."""
    if not target or not offered_player:
        return False, "اللاعب غير موجود!"
    if target.team_id == player_team.id:
        return False, "هذا اللاعب في فريقك بالفعل!"
    if offered_player.team_id != player_team.id:
        return False, "اللاعب المقترح ليس من فريقك!"
    if target.team_id is None:
        return False, "لا يمكن مقايضة وكيل حر!"

    cash = max(0.0, cash or 0.0)
    totals = cash + (offered_player.market_value or 0)
    if totals < target.market_value * 0.8:
        return False, "قيمة العرض (نقداً + اللاعب) أقل من 80% من قيمة الهدف!"

    old_club_id = target.team_id
    old_club = db.session.get(Team, old_club_id) if old_club_id else None
    player_team.budget = max(0, (player_team.budget or 0) - cash)
    if old_club:
        old_club.budget = (old_club.budget or 0) + cash

    target.team_id = player_team.id
    target.is_starting = False
    offered_player.team_id = old_club_id
    offered_player.is_starting = False

    db.session.add(Message(
        title="صفقة مبادلة",
        content=f"تبادل: {offered_player.name} + {cash:,} دولار مقابل {target.name}",
        type="transfer", created_day=day,
    ))
    return True, f"تمت المبادلة! حصلت على {target.name} مقابل {offered_player.name} + {cash:,}$"


def process_offer_accept(offer_id: int) -> tuple[bool, str]:
    offer = db.session.get(TransferOffer, offer_id)
    if not offer:
        return False, "العرض غير موجود!"

    if not _offer_involves_player_team(offer):
        return False, "لا يمكنك التصرف في هذا العرض!"

    if offer.status != 'pending':
        return False, "هذا العرض لم يعد قيد الانتظار!"

    player = db.session.get(Player, offer.player_id)
    from_team = db.session.get(Team, offer.from_team_id)
    to_team = db.session.get(Team, offer.to_team_id)
    if not from_team or not to_team:
        return False, "العرض غير صالح!"

    if from_team.is_player_team:
        # Outgoing sale (player team sells). Honour sell-on clauses.
        if not player:
            return False, "اللاعب غير موجود!"
        from_team.budget = (from_team.budget or 0) + offer.amount
        _pay_sell_on_clauses(player, offer.amount, from_team)
        # FM: player swap — an AI player may come the other way.
        if offer.offered_player_id:
            swap_player = db.session.get(Player, offer.offered_player_id)
            if swap_player:
                swap_player.team_id = from_team.id
                swap_player.is_starting = False
        player.team_id = to_team.id
        player.is_starting = False
        offer.status = 'accepted'
        return True, f"تم بيع {player.name} مقابل {offer.amount} دولار"

    # Incoming purchase (player team buys): credit the AI seller, move the
    # swap player out if any, and honour sell-on clauses like outgoing sales.
    if to_team.budget >= offer.amount:
        to_team.budget -= offer.amount
        from_team.budget = (from_team.budget or 0) + offer.amount
        if player:
            if offer.offered_player_id:
                swap_player = db.session.get(Player, offer.offered_player_id)
                if swap_player and swap_player.team_id == to_team.id:
                    swap_player.team_id = from_team.id
                    swap_player.is_starting = False
            _pay_sell_on_clauses(player, offer.amount, from_team)
            player.team_id = to_team.id
            player.is_starting = False
        offer.status = 'accepted'
        return True, f"تم شراء {player.name if player else 'اللاعب'} مقابل {offer.amount} دولار"

    return False, "لا يوجد ميزانية كافية!"


def process_offer_reject(offer_id: int) -> tuple[bool, str]:
    offer = db.session.get(TransferOffer, offer_id)
    if not offer:
        return False, "العرض غير موجود!"

    if not _offer_involves_player_team(offer):
        return False, "لا يمكنك التصرف في هذا العرض!"

    offer.status = 'rejected'
    return True, "تم رفض العرض"


# --------------------------------------------------------------------------
# Scouting (FM: masked ratings + regional assignments)
# --------------------------------------------------------------------------
SCOUT_ROLE = "scout"


def get_scout_report(player_team: Team, player: Player) -> int:
    from models import ScoutReport

    report = ScoutReport.query.filter_by(
        team_id=player_team.id, player_id=player.id
    ).first()
    return report.knowledge_level if report else 0


def get_display_rating(player: Player, player_team: Team) -> str:
    """Mask a player's true ability until he has been properly scouted."""
    knowledge = get_scout_report(player_team, player)
    if knowledge < SCOUT_KNOWLEDGE_THRESHOLD:
        lo = max(30, (player.attack or 50) - 10)
        hi = min(99, (player.attack or 50) + 10)
        return f"{lo} - {hi}"
    return str(player.attack)


def assign_scout_to_region(player_team: Team, staff: Staff, region: str) -> tuple[bool, str]:
    if staff.team_id != player_team.id:
        return False, "هذا الكشاف لا يعمل في فريقك!"
    if staff.role != SCOUT_ROLE:
        return False, "الموظف المحدد ليس كشافاً!"
    if player_team.budget < 25000:
        return False, "لا تكفي ميزانية لتغطية نفقات الكشافة!"
    player_team.budget -= 25000
    staff.region = region
    staff.on_assignment = True
    return True, f"تم إرسال {staff.name} للكشف في {region}"


def unassign_scout(staff: Staff) -> None:
    staff.on_assignment = False
    staff.region = None


def process_scouting_weekly(state: GameState) -> None:
    """Weekly scouting tick: grow knowledge + discover regional wonderkids."""
    from models import ScoutReport

    for staff in Staff.query.filter_by(role=SCOUT_ROLE, on_assignment=True).all():
        region = staff.region
        squad = Team.query.filter_by(is_player_team=True).first()
        if not squad or not region:
            continue

        # Knowledge grows for every player from the assigned region.
        candidates = Player.query.filter(Player.region == region).all()
        for p in candidates:
            report = ScoutReport.query.filter_by(team_id=squad.id, player_id=p.id).first()
            if report is None:
                report = ScoutReport(team_id=squad.id, player_id=p.id, knowledge_level=0)
                db.session.add(report)
            report.knowledge_level = min(100, report.knowledge_level + SCOUT_KNOWLEDGE_GAIN)

        # FM: chance to discover a young talent from the region.
        if random.random() < (staff.level * 0.1):
            talent = _discover_region_talent(region, staff.level, state)
            if talent:
                talent.team_id = None  # goes on the free-agent market
                db.session.add(talent)


def _discover_region_talent(region: str, scout_level: int, state: GameState) -> Player | None:
    from procedural import generate_region_talent

    found = generate_region_talent(region, scout_level)
    stats = found["stats"]
    return Player(
        name=found["name"],
        position=found["position"],
        attack=stats["attack"],
        defense=stats["defense"],
        potential=stats["potential"],
        age=stats["age"],
        height=stats["height"],
        weight=stats["weight"],
        personality=stats["personality"],
        region=region,
        market_value=stats["market_value"],
        team_id=None,
        is_starting=False,
        traits=found["traits"],
    )


# --------------------------------------------------------------------------
# Template database construction
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Round-robin schedule generation (circle method)
# --------------------------------------------------------------------------
def generate_round_robin_fixtures(team_ids: list[int]) -> list[tuple[int, int, int]]:
    """Build a perfect double round-robin: ``[(week, home_id, away_id)]``.

    Guarantees: every team plays exactly once per week, every pair meets
    twice (once home, once away), total weeks == (n - 1) * 2 for even n.
    An odd squad gets a BYE (``None``) slot so the maths still holds.
    """
    ids = list(dict.fromkeys(team_ids))  # de-dup, keep order
    if len(ids) < 2:
        return []
    if len(ids) % 2 == 1:
        ids.append(None)  # type: ignore[list-item]  # BYE sentinel
    n = len(ids)
    single_rounds = n - 1
    first_leg: list[tuple[int, int, int]] = []
    circle = list(ids)
    for rnd in range(single_rounds):
        week = rnd + 1
        for i in range(n // 2):
            home, away = circle[i], circle[n - 1 - i]
            if home is None or away is None:
                continue
            if i == 0 and rnd % 2 == 1:  # balance home/away on anchor pairing
                home, away = away, home
            first_leg.append((week, home, away))
        circle = [circle[0]] + [circle[-1]] + circle[1:-1]  # rotate, fix first
    second_leg = [(w + single_rounds, a, h) for (w, h, a) in first_leg]
    return sorted(first_leg + second_leg, key=lambda f: (f[0], f[1], f[2]))


def validate_schedule(fixtures: list[tuple[int, int, int]]) -> list[str]:
    """Return human-readable violations; empty list means a perfect schedule."""
    errors: list[str] = []
    by_week: dict[int, list[int]] = {}
    for week, home, away in fixtures:
        if home == away:
            errors.append(f"week {week}: team {home} plays itself")
        by_week.setdefault(week, []).extend([home, away])
    for week, ids in sorted(by_week.items()):
        if len(ids) != len(set(ids)):
            errors.append(f"week {week}: a team plays twice -> {sorted(ids)}")
    seen: dict[tuple[int, int], int] = {}
    for _week, home, away in fixtures:
        key = (min(home, away), max(home, away))
        seen[key] = seen.get(key, 0) + 1
    for key, count in seen.items():
        if count != 2:
            errors.append(f"pair {key}: meets {count}x (expected 2)")
    return errors


def start_new_season(state: GameState) -> int:
    """Build a fresh fixture list for the season starting now.

    Called by the ``/season_end`` route AFTER promotion/relegation moved
    clubs between leagues. Without this the new season had zero fixtures
    and ``get_current_match()`` returned ``None`` forever.

    - Zeroes EVERY club's standings (promotion only reset moved clubs).
    - Deletes leftover unplayed fixtures, keeps played history.
    - Generates a validated double round-robin per league + match dates.
    - Advances the calendar past the off-season break.

    The caller owns commit. Returns the number of new fixtures.
    """
    import datetime as _dt

    for team in Team.query.all():
        team.points = 0
        team.wins = 0
        team.losses = 0
        team.draws = 0
        team.goals_for = 0
        team.goals_against = 0

    Match.query.filter_by(played=False).delete(synchronize_session=False)

    new_matches: list[Match] = []
    for league in League.query.order_by(League.tier).all():
        team_ids = [t.id for t in Team.query.filter_by(league_id=league.id).all()]
        fixtures = generate_round_robin_fixtures(team_ids)
        problems = validate_schedule(fixtures)
        if problems:
            logger.error("Invalid new-season schedule for %s: %s", league.name, problems)
            continue
        for week, home_id, away_id in fixtures:
            m = Match(
                home_team_id=home_id,
                away_team_id=away_id,
                week=week,
                played=False,
            )
            db.session.add(m)
            new_matches.append(m)
    db.session.flush()

    try:
        base = datetime.strptime(state.current_date, "%Y-%m-%d").date()
    except Exception:
        base = _dt.date.today()
    # Off-season break: new campaign kicks off ~10 weeks later.
    new_start = base + _dt.timedelta(weeks=10)
    state.start_date = new_start.isoformat()
    state.current_date = new_start.isoformat()

    if new_matches:
        adjust_match_dates(new_matches, new_start)

    invalidate_league_cache()
    logger.info("New season: %d fixtures from %s", len(new_matches), new_start)
    return len(new_matches)


def recalculate_standings_from_played() -> int:
    """Zero every team's standings and re-apply ONLY played matches.

    Returns the number of played matches applied. The caller owns commit.
    Use after any schedule repair so the table can never drift from results.
    """
    from models import Match as _Match

    for team in Team.query.all():
        team.points = 0
        team.wins = 0
        team.losses = 0
        team.draws = 0
        team.goals_for = 0
        team.goals_against = 0
    played = _Match.query.filter_by(played=True).all()
    for m in played:
        home = db.session.get(Team, m.home_team_id)
        away = db.session.get(Team, m.away_team_id)
        if not home or not away:
            continue  # orphan fixture; cleaned by fix_db.py / validation
        hs, aws = m.home_score or 0, m.away_score or 0
        home.goals_for += hs
        home.goals_against += aws
        away.goals_for += aws
        away.goals_against += hs
        if hs > aws:
            home.points += 3
            home.wins += 1
            away.losses += 1
        elif aws > hs:
            away.points += 3
            away.wins += 1
            home.losses += 1
        else:
            home.points += 1
            away.points += 1
            home.draws += 1
            away.draws += 1
    invalidate_league_cache()
    return len(played)


def generate_match_dates(start_date):
    import datetime

    days_ahead = 5 - start_date.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    first_sat = start_date + datetime.timedelta(days=days_ahead)

    pl_dates, champ_dates, l1_dates, l2_dates = {}, {}, {}, {}
    pl_round, champ_round, l1_round, l2_round = 1, 1, 1, 1

    for i in range(52):
        week_sat = first_sat + datetime.timedelta(weeks=i)
        is_intl_break = i in [4, 9, 14, 30]

        if not is_intl_break and pl_round <= 38:
            pl_dates[pl_round] = week_sat.isoformat()
            pl_round += 1

        if not is_intl_break and champ_round <= 46:
            champ_dates[champ_round] = week_sat.isoformat()
            champ_round += 1
            if i in [2, 6, 11, 16, 21, 26, 31, 36] and champ_round <= 46:
                week_tue = week_sat + datetime.timedelta(days=3)
                champ_dates[champ_round] = week_tue.isoformat()
                champ_round += 1

        if l1_round <= 46:
            l1_dates[l1_round] = week_sat.isoformat()
            l1_round += 1
            if i in [2, 6, 11, 16, 21, 26, 31, 36] and l1_round <= 46:
                week_tue = week_sat + datetime.timedelta(days=3)
                l1_dates[l1_round] = week_tue.isoformat()
                l1_round += 1

        if l2_round <= 46:
            l2_dates[l2_round] = week_sat.isoformat()
            l2_round += 1
            if i in [2, 6, 11, 16, 21, 26, 31, 36] and l2_round <= 46:
                week_tue = week_sat + datetime.timedelta(days=3)
                l2_dates[l2_round] = week_tue.isoformat()
                l2_round += 1

    return {1: pl_dates, 2: champ_dates, 3: l1_dates, 4: l2_dates}


def adjust_match_dates(matches, start_date):
    import datetime

    dates_by_tier = generate_match_dates(start_date)
    home_schedule = {}

    CITY_MAPPING = {
        "Manchester United": "Manchester",
        "Manchester City": "Manchester",
        "Liverpool": "Liverpool",
        "Everton": "Liverpool",
        "Arsenal": "London",
        "Tottenham Hotspur": "London",
        "Chelsea": "London",
        "West Ham United": "London",
        "Crystal Palace": "London",
        "Fulham": "London",
        "Queens Park Rangers": "London",
        "Millwall": "London",
        "Charlton Athletic": "London",
        "Leyton Orient": "London",
        "AFC Wimbledon": "London",
        "Sheffield United": "Sheffield",
        "Sheffield Wednesday": "Sheffield",
        "Bristol City": "Bristol",
        "Bristol Rovers": "Bristol",
    }

    for m in matches:
        home_team = m.home_team
        league = home_team.league if home_team else None
        tier = league.tier if league else 1
        base_date_str = dates_by_tier.get(tier, {}).get(m.week)
        if not base_date_str:
            base_date_str = (start_date + datetime.timedelta(weeks=m.week)).isoformat()
        m.date = base_date_str

    for m in matches:
        home_team = m.home_team
        if not home_team:
            continue
        city = CITY_MAPPING.get(home_team.name)
        if not city:
            continue
        date_obj = datetime.datetime.strptime(m.date, "%Y-%m-%d").date()
        clash_key = (city, m.date)
        if clash_key in home_schedule:
            new_date = date_obj + datetime.timedelta(days=1)
            m.date = new_date.isoformat()
            clash_key = (city, m.date)
        home_schedule[clash_key] = home_team.name

    for m in matches:
        home_team = m.home_team
        if not home_team:
            continue
        league = home_team.league
        if league and league.tier == 1:
            date_obj = datetime.datetime.strptime(m.date, "%Y-%m-%d").date()
            if date_obj.weekday() == 5:
                rand = random.random()
                if rand < 0.10:
                    m.date = (date_obj - datetime.timedelta(days=1)).isoformat()
                elif rand < 0.30:
                    m.date = (date_obj + datetime.timedelta(days=1)).isoformat()
                elif rand < 0.35:
                    m.date = (date_obj + datetime.timedelta(days=2)).isoformat()


def init_template() -> None:
    ensure_saves_dir()
    refresh_db_engine(f"sqlite:///{template_db_path().replace('\\', '/')}")

    if not _needs_template_build():
        return

    _build_template_database()


def _template_schema_current() -> bool:
    """Compare stored schema version against the current build version."""
    try:
        conn = sqlite3.connect(template_db_path())
        cursor = conn.cursor()
        cursor.execute(
            "SELECT value FROM app_meta WHERE key='schema_version'"
        )
        row = cursor.fetchone()
        conn.close()
        return row is not None and int(row[0]) >= TEMPLATE_SCHEMA_VERSION
    except Exception:
        return False


def _needs_template_build() -> bool:
    try:
        if GameState.query.count() != 0:
            # Only trust an existing template when its schema is current.
            return not _template_schema_current()
        return True
    except Exception:
        return True


def _write_schema_version() -> None:
    db.session.execute(db.text(
        "CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT)"
    ))
    db.session.execute(db.text(
        "INSERT INTO app_meta (key, value) VALUES ('schema_version', :v) "
        "ON CONFLICT(key) DO UPDATE SET value = :v"
    ), {"v": str(TEMPLATE_SCHEMA_VERSION)})
    db.session.commit()


def _build_template_database() -> None:
    db.create_all()
    ensure_indexes()

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
            reputation = league.reputation + random.randint(-5, 5)
            team = Team(
                name=team_name,
                morale=random.randint(60, 80),
                budget=calculate_club_budget(league.tier, reputation, league.reputation),
                reputation=reputation,
                is_player_team=False,
                league_id=league.id,
                training_facilities=random.randint(1, 5),
                youth_facilities=random.randint(1, 5),
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
                    is_starting=(j < 11),
                    stamina=random.randint(60, 100),
                    technique=random.randint(30, 90),
                    speed=random.randint(30, 90),
                    traits=generate_player_traits(),
                    sharpness=random.randint(60, 100),
                    morale=random.randint(50, 80),
                )
                db.session.add(player)

    db.session.commit()

    for league in leagues:
        team_ids = [t.id for t in Team.query.filter_by(league_id=league.id).all()]
        fixtures = generate_round_robin_fixtures(team_ids)
        problems = validate_schedule(fixtures)
        if problems:
            logger.error("Invalid generated schedule for %s: %s", league.name, problems)
            raise RuntimeError(f"Schedule generation failed for {league.name}")
        for week, home_id, away_id in fixtures:
            db.session.add(Match(
                home_team_id=home_id,
                away_team_id=away_id,
                week=week,
                played=False,
            ))

    db.session.commit()

    player_league = League.query.filter_by(tier=1).first()
    state = GameState(
        current_day=1,
        current_week=1,
        season_over=False,
        user_league_id=player_league.id,
        manager_name="المدرب",
    )
    db.session.add(state)

    # FM: set-piece takers and default tactics will be assigned per save start.
    _assign_template_set_pieces()
    _write_schema_version()

    db.session.commit()


def _assign_template_set_pieces() -> None:
    """Pick a penalty taker (best attacker) and a freekick taker (best technique)."""
    for team in Team.query.all():
        squad = Player.query.filter_by(team_id=team.id).all()
        if not squad:
            continue
        fwd = [p for p in squad if p.position == "FWD"]
        penalty = max(fwd or squad, key=lambda p: (p.attack or 0))
        freekick = max(squad, key=lambda p: (p.technique or 0))
        team.penalty_taker_id = penalty.id
        team.freekick_taker_id = freekick.id
        team.match_tactic = "balanced"


def create_new_game(slot_id: int) -> bool:
    ensure_saves_dir()

    template_path = template_db_path()
    if not os.path.exists(template_path):
        init_template()

    dest_path = os.path.join(saves_base(), f"save{slot_id}.sqlite3")
    if os.path.exists(dest_path):
        return False

    shutil.copy2(template_path, dest_path)

    refresh_db_engine(get_slot_uri(slot_id))

    import datetime
    start_dt = datetime.date.today()

    state = GameState.query.first()
    if state:
        state.start_date = start_dt.isoformat()
        state.current_date = start_dt.isoformat()

    matches = Match.query.all()
    adjust_match_dates(matches, start_dt)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return False

    set_active_slot(slot_id)
    return True