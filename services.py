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
from sqlalchemy.exc import OperationalError

from config import (
    ABILITY_FILL_ANY_WEIGHT,
    ABILITY_FILL_EXACT_WEIGHT,
    ABILITY_FILL_FITNESS_WEIGHT,
    ABILITY_FILL_GROUP_WEIGHT,
    AGENT_FEE_RATIO,
    AGENT_FEE_STAR_RATIO,
    AGENT_FEE_STAR_THRESHOLD,
    ATTACK_ATTRS,
    BUY_BACK_PRICE_CAP_MULT,
    CONTRACT_DAYS_PER_YEAR,
    CONTRACT_MAX_YEARS,
    CONTRACT_MIN_YEARS,
    COUNTER_OFFER_STEP,
    DB_FILENAME,
    DEFAULT_BUDGET,
    BUDGET_JITTER,
    BUDGET_REPUTATION_STEP,
    CLUB_REPUTATIONS,
    DEFENSE_ATTRS,
    EUROPEAN_TEAMS,
    FFP_BASE_ALLOWANCE,
    FFP_SPEND_RATIO,
    FIRST_REFUSAL_NOTICE_DAYS,
    FREE_AGENT_SIGN_FEE_RATIO,
    INSTALLMENT_INTERVAL_DAYS,
    INSTALLMENTS_MAX,
    LEAGUE_BUDGET_MULTIPLIER,
    LEAGUE_NAME_TO_KEY,
    LEAGUE_RATING_BANDS,
    LEAGUE_REPUTATIONS,
    LOAN_FEE_RATIO,
    LOAN_MAX_DAYS,
    LOAN_MIN_DAYS,
    LOG_FILE,
    LOG_FORMAT,
    PHILOSOPHY_BAND_SHIFT_CHANCE,
    PHILOSOPHY_BANDS,
    PHILOSOPHY_PRESSING,
    PHILOSOPHY_PROFILES,
    PHILOSOPHY_SHIFT_CHANCE,
    PHILOSOPHY_TACTIC,
    PHILOSOPHY_TRAINING_FOCUS,
    POSITION_OVERALL_WEIGHTS,
    SQUAD_CORE_DETAILED_POSITIONS,
    SQUAD_EXTRA_POSITION_WEIGHTS,
    SQUAD_STARTING_XI,
    MARKET_VALUE_MIN,
    MARKET_VALUE_MAX,
    MAX_FACILITY_LEVEL,
    MAX_SAVE_SLOTS,
    OFFER_MAX_ROUNDS,
    PERF_BONUS_MAX_RATIO,
    PLAYER_PREF_REPUTATION_GAP,
    PRECONTRACT_DAYS,
    PROMOTION_BONUS_PER_TIER,
    RELEASE_CLAUSE_DEFAULT_MULT,
    RELEASE_CLAUSE_MIN_MULT,
    RIVALS,
    RIVAL_BID_CHANCE,
    RIVAL_REPUTATION_GAP,
    SCOUT_KNOWLEDGE_GAIN,
    SCOUT_KNOWLEDGE_THRESHOLD,
    SCOUT_REGIONS,
    SEED_STAR_PLAYERS,
    SELL_ON_CLAUSE_DISCOUNT_RATIO,
    SELL_ON_CLAUSE_MAX,
    SELL_ON_CLAUSE_RANGE,
    SIGNING_BONUS_MAX_RATIO,
    STAFF_FIRE_COMPENSATION_RATIO,
    STAFF_MARKET_POOL_LIMIT,
    STARTERS_LIMIT,
    TEMPLATE_DB_FILENAME,
    TEMPLATE_SCHEMA_VERSION,
    TIER_BUDGETS,
    TOP_LEAGUE_ROUNDS,
    LOWER_LEAGUE_ROUNDS,
    TRAINING_COST_PER_LEVEL,
    TRANSFER_DEADLINE_DAYS,
    TRANSFER_DEADLINE_EXPIRY_DAYS,
    TRANSFER_DEADLINE_SURGE,
    TRANSFER_LIST_DISCOUNT,
    TRANSFER_REQUEST_MORALE_LOSS,
    TRANSFER_WINDOW_SUMMER_MONTHS,
    TRANSFER_WINDOW_WINTER_MONTHS,
    VALUATION_ASSIST_WEIGHT,
    VALUATION_GOAL_WEIGHT,
    VALUATION_MORALE_BONUS_STEP,
    VALUATION_VETERAN_MALUS,
    VALUATION_YOUTH_BONUS,
    WAGE_YEARLY_MAX_RATIO,
    WAGE_YEARLY_MIN_RATIO,
    YOUTH_COST_PER_LEVEL,
)
from config import philosophy_band_for
from models import (
    db,
    BuyBackClause,
    FirstRefusalClause,
    InstallmentDebt,
    PerformanceBonus,
    Team,
    TeamPhilosophy,
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
    generate_player_stats_v2,
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
    _ensure_schema_columns()


def _ensure_schema_columns() -> None:
    """ALTER old save files forward: ``create_all`` adds tables but never
    new columns, so pre-v6 saves would miss the transfer-system fields.
    Each ALTER is best-effort — missing tables / existing columns just skip."""
    from sqlalchemy import text as sql_text

    alters = [
        # Player contracts / loans / list flags
        ("player", "wage", "INTEGER DEFAULT 0"),
        ("player", "contract_days", "INTEGER DEFAULT 730"),
        ("player", "signing_bonus", "INTEGER DEFAULT 0"),
        ("player", "release_clause", "INTEGER"),
        ("player", "transfer_listed", "BOOLEAN DEFAULT 0"),
        ("player", "transfer_requested", "BOOLEAN DEFAULT 0"),
        ("player", "loan_from_team_id", "INTEGER"),
        ("player", "loan_to_team_id", "INTEGER"),
        ("player", "loan_until_day", "INTEGER"),
        ("player", "loan_fee", "INTEGER DEFAULT 0"),
        ("player", "loan_buy_option", "INTEGER"),
        # Team FFP ledgers
        ("team", "season_spend", "INTEGER DEFAULT 0"),
        ("team", "season_income", "INTEGER DEFAULT 0"),
        # TransferOffer rounds / terms / clauses / loans / precontracts
        ("transfer_offer", "offer_round", "INTEGER DEFAULT 1"),
        ("transfer_offer", "counter_amount", "INTEGER"),
        ("transfer_offer", "wage_offer", "INTEGER DEFAULT 0"),
        ("transfer_offer", "contract_years", "INTEGER DEFAULT 3"),
        ("transfer_offer", "signing_bonus", "INTEGER DEFAULT 0"),
        ("transfer_offer", "agent_fee", "INTEGER DEFAULT 0"),
        ("transfer_offer", "installments", "INTEGER DEFAULT 1"),
        ("transfer_offer", "upfront_amount", "INTEGER"),
        ("transfer_offer", "buy_back_price", "INTEGER"),
        ("transfer_offer", "first_refusal", "BOOLEAN DEFAULT 0"),
        ("transfer_offer", "performance_bonus", "INTEGER DEFAULT 0"),
        ("transfer_offer", "performance_target", "INTEGER DEFAULT 0"),
        ("transfer_offer", "performance_paid", "BOOLEAN DEFAULT 0"),
        ("transfer_offer", "is_loan", "BOOLEAN DEFAULT 0"),
        ("transfer_offer", "loan_days", "INTEGER"),
        ("transfer_offer", "loan_fee", "INTEGER DEFAULT 0"),
        ("transfer_offer", "buy_option", "INTEGER"),
        ("transfer_offer", "is_precontract", "BOOLEAN DEFAULT 0"),
        ("transfer_offer", "effective_day", "INTEGER"),
        # GameState window override
        ("game_state", "transfer_window_override", "VARCHAR(10)"),
        ("game_state", "ai_difficulty", "VARCHAR(10) DEFAULT 'عادي'"),
        # World simulation hub (fix.txt): cooldown guard + persisted summary
        ("game_state", "world_sim_last_week", "INTEGER DEFAULT 0"),
        ("game_state", "world_last_summary", "TEXT"),
        # European expansion: per-country pyramids + rating bands
        ("league", "country", "VARCHAR(30) DEFAULT 'England'"),
        ("league", "rating_floor", "INTEGER"),
        ("league", "rating_ceiling", "INTEGER"),
        ("league", "season_start_week", "INTEGER DEFAULT 1"),
        ("league", "is_continental", "BOOLEAN DEFAULT 0"),
        ("league", "region", "VARCHAR(30)"),
        ("league", "rules_json", "JSON"),
        # Deeper sim: tactics + AI profile + budgets
        ("team", "tactics_data", "JSON"),
        ("team", "tactic_fingerprint", "VARCHAR(64)"),
        ("team", "transfer_budget", "INTEGER"),
        ("team", "wage_budget", "INTEGER"),
        ("team", "ai_profile", "JSON"),
        ("team", "transfer_embargo", "BOOLEAN DEFAULT 0"),
        ("match", "engine_level", "INTEGER"),
        ("match", "sim_seed", "INTEGER"),
        # Deeper sim: registration / discipline / loans
        ("player", "nationality", "VARCHAR(30)"),
        ("player", "is_homegrown", "BOOLEAN DEFAULT 0"),
        ("player", "registration_status", "VARCHAR(20) DEFAULT 'registered'"),
        ("player", "contract_until", "VARCHAR(10)"),
        ("player", "discipline_data", "JSON"),
        ("player", "loan_info", "JSON"),
        ("player", "cup_appearances", "JSON"),
        # Position-specific overalls + detailed attributes
        ("player", "overall", "INTEGER DEFAULT 50"),
        ("player", "detailed_position", "VARCHAR(10)"),
        ("player", "off_the_ball", "INTEGER DEFAULT 50"),
        ("player", "first_touch", "INTEGER DEFAULT 50"),
        ("player", "finishing", "INTEGER DEFAULT 50"),
        ("player", "shot_power", "INTEGER DEFAULT 50"),
        ("player", "long_shots", "INTEGER DEFAULT 50"),
        ("player", "volleys", "INTEGER DEFAULT 50"),
        ("player", "penalties", "INTEGER DEFAULT 50"),
        ("player", "free_kick_accuracy", "INTEGER DEFAULT 50"),
        ("player", "curve", "INTEGER DEFAULT 50"),
        ("player", "passing", "INTEGER DEFAULT 50"),
        ("player", "vision", "INTEGER DEFAULT 50"),
        ("player", "crossing", "INTEGER DEFAULT 50"),
        ("player", "ball_control", "INTEGER DEFAULT 50"),
        ("player", "dribbling", "INTEGER DEFAULT 50"),
        ("player", "heading", "INTEGER DEFAULT 50"),
        ("player", "acceleration", "INTEGER DEFAULT 50"),
        ("player", "sprint_speed", "INTEGER DEFAULT 50"),
        ("player", "agility", "INTEGER DEFAULT 50"),
        ("player", "balance", "INTEGER DEFAULT 50"),
        ("player", "jumping", "INTEGER DEFAULT 50"),
        ("player", "strength", "INTEGER DEFAULT 50"),
        ("player", "standing_tackle", "INTEGER DEFAULT 50"),
        ("player", "sliding_tackle", "INTEGER DEFAULT 50"),
        ("player", "marking", "INTEGER DEFAULT 50"),
        ("player", "positioning", "INTEGER DEFAULT 50"),
        ("player", "interceptions", "INTEGER DEFAULT 50"),
        ("player", "defensive_awareness", "INTEGER DEFAULT 50"),
        ("player", "aggression", "INTEGER DEFAULT 50"),
        ("player", "bravery", "INTEGER DEFAULT 50"),
        ("player", "composure", "INTEGER DEFAULT 50"),
        ("player", "concentration", "INTEGER DEFAULT 50"),
        ("player", "decision_making", "INTEGER DEFAULT 50"),
        ("player", "teamwork", "INTEGER DEFAULT 50"),
        ("player", "work_rate", "INTEGER DEFAULT 50"),
        ("player", "flair", "INTEGER DEFAULT 50"),
        ("player", "reactions", "INTEGER DEFAULT 50"),
        ("player", "technique", "INTEGER DEFAULT 50"),
        ("player", "gk_diving", "INTEGER DEFAULT 50"),
        ("player", "gk_handling", "INTEGER DEFAULT 50"),
        ("player", "gk_kicking", "INTEGER DEFAULT 50"),
        ("player", "gk_positioning", "INTEGER DEFAULT 50"),
        ("player", "gk_reflexes", "INTEGER DEFAULT 50"),
        ("player", "gk_one_on_ones", "INTEGER DEFAULT 50"),
        ("player", "gk_communication", "INTEGER DEFAULT 50"),
    ]
    # New table for old saves: create_all adds missing tables without wiping data.
    try:
        db.create_all()
    except Exception:
        pass
    for table, column, ddl in alters:
        try:
            db.session.execute(sql_text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        except Exception:
            db.session.rollback()
    try:
        db.session.commit()
    except Exception:
        pass


_ensure_transfer_columns = _ensure_schema_columns


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
    try:
        state = GameState.query.first()
    except OperationalError:
        # Engine points at a missing/empty/locked file (e.g. a rebuild
        # script swapped the DB mid-request). Abort cleanly so the 500
        # handler redirects instead of showing a debugger page.
        abort(500, description="Database file unavailable")
    if not state:
        abort(500, description="Game state not initialized")
    return state


def safe_player_team() -> Team:
    try:
        player_team = Team.query.filter_by(is_player_team=True).first()
    except OperationalError:
        abort(500, description="Database file unavailable")
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


def calculate_club_budget(league_key=None, reputation=None,
                          league_reputation=None, tier=None):
    """Starting budget for one club: reputation-driven exponential curve.

    Budget scales exponentially with reputation: a 60-reputation club gets
    ~5M (times the league prestige multiplier) while a 90-reputation club
    gets ~250M. The league prestige multiplier (TV money) is applied on
    top, plus a small jitter so two clubs with the same reputation are not
    identical.

    ``league_key`` is a ``(country, tier)`` tuple. The legacy positional
    form ``calculate_club_budget(tier, reputation, league_reputation)`` is
    still accepted: an int first argument is treated as an English tier.
    """
    # --- legacy shim: calculate_club_budget(tier, rep, league_rep) ---
    if isinstance(league_key, (int, float)) or (
        league_key is None and tier is None and league_reputation is not None
    ):
        tier = league_key if isinstance(league_key, (int, float)) else tier
        league_key = ("England", int(tier)) if tier in (1, 2, 3, 4) else None
    try:
        rep = int(reputation if reputation is not None else 60)
    except (TypeError, ValueError):
        rep = 60
    # Base curve: 5M at rep 60, doubles roughly every 8 rep points.
    base = 5_000_000 * (2 ** ((rep - 60) / 8))
    # League prestige multiplier (TV money).
    multiplier = 1.0
    if isinstance(league_key, (tuple, list)) and len(league_key) == 2:
        try:
            multiplier = float(LEAGUE_BUDGET_MULTIPLIER.get(
                (str(league_key[0]), int(league_key[1])), 1.0))
        except (TypeError, ValueError):
            multiplier = 1.0
    # Small jitter (+-8%) so two clubs with the same rep are not identical.
    jitter = random.uniform(0.92, 1.08)
    return max(500_000, int(base * multiplier * jitter))


def calculate_market_value(attack, defense, potential, age, reputation):
    """Realistic market value in USD.

    Anchors:
      - Mbappe (91/60, age 25, pot 93, rep 95) -> ~180-200M
      - Lamine Yamal (88/55, age 17, pot 95, rep 90) -> ~200-230M
      - Average La Liga starter (70/65, age 27, pot 72, rep 70) -> ~5M
      - Serie C starter (52/50, age 25, pot 55, rep 40) -> ~200-500K
    """
    try:
        primary = max(int(attack or 0), int(defense or 0))
    except (TypeError, ValueError):
        primary = 50
    try:
        pot = int(potential or primary)
    except (TypeError, ValueError):
        pot = primary
    try:
        player_age = int(age or 25)
    except (TypeError, ValueError):
        player_age = 25
    try:
        rep = int(reputation or 50)
    except (TypeError, ValueError):
        rep = 50

    # Base curve: exponential in primary ability.
    base = 150_000 * (1.18 ** (primary - 50))
    # Potential premium (young high-potential players cost more).
    potential_gap = max(0, pot - primary)
    base *= (1 + potential_gap * 0.03)
    # Age curve: peak value at 24-27, youth premium below 21.
    if player_age <= 21:
        base *= 1.75
    elif player_age <= 24:
        base *= 1.30
    elif player_age <= 27:
        base *= 1.0
    elif player_age <= 30:
        base *= 0.75
    elif player_age <= 33:
        base *= 0.5
    else:
        base *= 0.25
    # Reputation multiplier (playing for a big club inflates value).
    base *= (0.8 + rep / 200)
    return max(100_000, min(300_000_000, int(base)))


def get_asking_price(player):
    """Negotiation premium for top players (clubs do not sell stars cheap)."""
    value = int(player.market_value or 0)
    if value >= 100_000_000:
        return int(value * 1.15)
    if value >= 50_000_000:
        return int(value * 1.10)
    return value


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
# Position-specific overalls + tier-bounded philosophies
# --------------------------------------------------------------------------
_COARSE_TO_DETAILED = {"GK": "GK", "DEF": "CB", "MID": "CM", "FWD": "ST"}
_BAND_ORDER = ["survivor", "pragmatic", "ambitious", "elite"]


def infer_detailed_position(player) -> str:
    """Best-effort detailed position for legacy rows."""
    det = (getattr(player, "detailed_position", None) or "").strip() if getattr(player, "detailed_position", None) else ""
    if det in POSITION_OVERALL_WEIGHTS:
        return det
    pos = (getattr(player, "position", None) or "").strip() if getattr(player, "position", None) else ""
    if pos in POSITION_OVERALL_WEIGHTS:
        return pos
    if pos in _COARSE_TO_DETAILED:
        return _COARSE_TO_DETAILED[pos]
    return "CM"


def recompute_aggregates(player: Player) -> None:
    """Compute attack/defense/overall from detailed attributes using the
    position-specific weight tables in POSITION_OVERALL_WEIGHTS."""
    det = infer_detailed_position(player)
    weights = POSITION_OVERALL_WEIGHTS.get(det or "CM")
    if not weights:
        return
    total = 0.0
    for attr, w in weights.items():
        try:
            val = getattr(player, attr, 50) or 50
        except Exception:
            val = 50
        total += float(val) * float(w)
    player.overall = max(1, min(99, int(round(total))))

    # attack/defense split — derived from the same weights but bucketed
    # into ATTACK_ATTRS / DEFENSE_ATTRS.
    att_w = sum(w for a, w in weights.items() if a in ATTACK_ATTRS)
    def_w = sum(w for a, w in weights.items() if a in DEFENSE_ATTRS)
    att_raw = sum((getattr(player, a, 50) or 50) * w for a, w in weights.items() if a in ATTACK_ATTRS)
    def_raw = sum((getattr(player, a, 50) or 50) * w for a, w in weights.items() if a in DEFENSE_ATTRS)
    player.attack = max(1, min(99, int(round(att_raw / att_w)))) if att_w > 0 else player.overall
    player.defense = max(1, min(99, int(round(def_raw / def_w)))) if def_w > 0 else player.overall


def recompute_all_aggregates() -> int:
    """Recompute overalls for every player. Returns count. Caller owns commit."""
    count = 0
    try:
        players = Player.query.all()
    except Exception:
        return 0
    for p in players:
        try:
            if not getattr(p, "detailed_position", None):
                # Backfill legacy rows so the UI badges stay meaningful.
                pos = (p.position or "").strip()
                if pos in POSITION_OVERALL_WEIGHTS:
                    p.detailed_position = pos
                elif pos in _COARSE_TO_DETAILED:
                    p.detailed_position = _COARSE_TO_DETAILED[pos]
                else:
                    p.detailed_position = "CM"
            recompute_aggregates(p)
            count += 1
        except Exception:
            continue
    return count


def _jitter_profile(primary: str) -> dict:
    base = PHILOSOPHY_PROFILES.get(primary, PHILOSOPHY_PROFILES["balanced"])
    out = {}
    for k, v in base.items():
        try:
            j = random.uniform(-0.15, 0.15)
            out[k] = max(0.0, min(1.0, float(v) + j))
        except Exception:
            out[k] = float(v)
    return out


def _band_defaults(band: str) -> tuple[str, int, int]:
    if band == "elite":
        return (random.choice(["title", "top4"]), random.randint(30, 50), random.randint(88, 99))
    if band == "ambitious":
        return (random.choice(["top4", "europa"]), random.randint(50, 70), random.randint(70, 86))
    if band == "pragmatic":
        return (random.choice(["mid_table", "europa"]), random.randint(60, 80), random.randint(50, 70))
    return (random.choice(["survival", "mid_table"]), random.randint(70, 90), random.randint(30, 50))


def get_team_philosophy(team: Team):
    """Lazy idempotent accessor: assigns a band-correct philosophy on first use."""
    if team is None:
        return None
    try:
        phil = TeamPhilosophy.query.filter_by(team_id=team.id).first()
    except Exception:
        phil = getattr(team, "philosophy", None)
    if phil is not None:
        return phil
    try:
        return assign_team_philosophy(team)
    except Exception:
        return getattr(team, "philosophy", None)


def assign_team_philosophy(team: Team):
    """Randomise a club's identity WITHIN its reputation band.

    Uniformly random within the band — no canonical philosophy per club name.
    """
    band = philosophy_band_for(getattr(team, "reputation", 50) or 50)
    allowed = list(PHILOSOPHY_BANDS[band]["allowed"])
    primary = random.choice(allowed) if allowed else "balanced"
    prof = _jitter_profile(primary)
    season_goal, patience, ambition = _band_defaults(band)
    try:
        state = GameState.query.first()
        season = state.season_number or 1 if state else 1
    except Exception:
        season = 1
    phil = None
    try:
        phil = TeamPhilosophy.query.filter_by(team_id=team.id).first()
    except Exception:
        phil = None
    if phil is None:
        try:
            phil = getattr(team, "philosophy", None)
            if phil is not None and getattr(phil, "team_id", team.id) != team.id:
                phil = None
        except Exception:
            phil = None
    if phil is None:
        phil = TeamPhilosophy(team_id=team.id)
        db.session.add(phil)
    phil.primary = primary
    phil.band = band
    phil.attacking_intent = prof.get("attacking_intent", 0.5)
    phil.pressing_intensity = prof.get("pressing_intensity", 0.5)
    phil.possession_focus = prof.get("possession_focus", 0.5)
    phil.width = prof.get("width", 0.5)
    phil.tempo = prof.get("tempo", 0.5)
    phil.defensive_line = prof.get("defensive_line", 0.5)
    phil.build_up_style = prof.get("build_up_style", 0.5)
    phil.transition_style = prof.get("transition_style", 0.5)
    phil.season_goal = season_goal
    phil.patience = patience
    phil.board_ambition = ambition
    phil.last_shift_season = season
    return phil


def effective_pressing(team: Team) -> str:
    """Pressing style implied by the club's philosophy (fallback: stored value)."""
    try:
        phil = get_team_philosophy(team) if team else None
        key = (phil.primary if phil and getattr(phil, "primary", None) else "").strip()
        if key in PHILOSOPHY_PRESSING:
            return PHILOSOPHY_PRESSING[key]
    except Exception:
        pass
    return (getattr(team, "pressing_style", None) or "normal")


def effective_match_tactic(team: Team) -> str:
    """Match tactic implied by the club's philosophy (fallback: stored value)."""
    try:
        phil = get_team_philosophy(team) if team else None
        key = (phil.primary if phil and getattr(phil, "primary", None) else "").strip()
        if key in PHILOSOPHY_TACTIC:
            return PHILOSOPHY_TACTIC[key]
    except Exception:
        pass
    return (getattr(team, "match_tactic", None) or "balanced")


def effective_training_focus(team: Team) -> str:
    """Training focus implied by the club's philosophy (fallback: stored value)."""
    try:
        phil = get_team_philosophy(team) if team else None
        key = (phil.primary if phil and getattr(phil, "primary", None) else "").strip()
        if key in PHILOSOPHY_TRAINING_FOCUS:
            return PHILOSOPHY_TRAINING_FOCUS[key]
    except Exception:
        pass
    return (getattr(team, "training_focus", None) or "balanced")


def apply_philosophy_to_coaching(team: Team) -> dict:
    """Sync one AI club's coaching decisions to its philosophy identity.

    Sets ``pressing_style`` / ``match_tactic`` / ``training_focus`` from
    the philosophy mappings. No commit — the caller owns the transaction.
    Returns the fields that changed (for tests / logging).
    """
    changed = {}
    try:
        want_pressing = effective_pressing(team)
        want_tactic = effective_match_tactic(team)
        want_training = effective_training_focus(team)
    except Exception:
        return changed
    try:
        if (team.pressing_style or None) != want_pressing:
            changed["pressing_style"] = ((team.pressing_style or None), want_pressing)
            team.pressing_style = want_pressing
        if (team.match_tactic or None) != want_tactic:
            changed["match_tactic"] = ((team.match_tactic or None), want_tactic)
            team.match_tactic = want_tactic
        if (getattr(team, "training_focus", None) or None) != want_training:
            changed["training_focus"] = (getattr(team, "training_focus", None), want_training)
            team.training_focus = want_training
    except Exception:
        pass
    return changed


def ai_weekly_coaching() -> dict:
    """Weekly AI planner hook: every AI club coaches to its philosophy.

    Called from the weekly tick so AI identities never go stale (e.g.
    after a season philosophy shift). Player teams are never touched.
    No commit — the caller owns the transaction. Returns
    ``{team_id: changed_fields}`` for the clubs that changed.
    """
    report: dict = {}
    try:
        teams = Team.query.filter_by(is_player_team=False).all()
    except Exception:
        return report
    for team in teams:
        try:
            changed = apply_philosophy_to_coaching(team)
        except Exception:
            continue
        if changed:
            report[team.id] = changed
    # Deeper sim (Feature D): philosophy-driven planner (mentality targets,
    # AI profiles). Never touches player teams; failures never break the tick.
    try:
        from ai_coaching import weekly_planner_report as _planner
        extra = _planner()
        for k, v in (extra or {}).items():
            report.setdefault(k, v)
    except Exception:
        pass
    # Backfill split budgets from the legacy single budget (lazy migration).
    try:
        for team in teams:
            if getattr(team, "transfer_budget", None) is None:
                team.transfer_budget = int(getattr(team, "budget", 0) or 0)
            if getattr(team, "wage_budget", None) is None:
                team.wage_budget = int((getattr(team, "budget", 0) or 0) // 52)
    except Exception:
        pass
    return report


def ensure_rules_and_tactics_columns() -> None:
    """Deeper-sim migration entry point (ALTER-only, idempotent)."""
    _ensure_schema_columns()


def team_decision_profile(team: Team) -> dict:
    """Decision profile for the AI transfer engine.

    The profile's ``priority_positions`` steer which line the AI shops
    for (see ``simulation._ai_transfer_round``) and
    ``evaluate_youngster_surprise_potential`` boosts high-potential
    youngsters for youth/moneyball identities — this is how organic
    surprises (e.g. an unexpected 2nd place) emerge from rational
    decisions rather than budgets alone.
    """
    phil = None
    try:
        phil = get_team_philosophy(team)
    except Exception:
        phil = None
    primary = getattr(phil, "primary", "balanced") if phil else "balanced"
    weights = dict(POSITION_OVERALL_WEIGHTS.get("CM", {}))
    priority = ["CM", "ST", "CB"]
    if primary in ("galacticos", "fluid_attacking"):
        priority = ["ST", "RW", "LW", "CAM"]
    elif primary in ("tiki_taka", "possession_domination"):
        priority = ["CM", "CAM", "CB"]
    elif primary == "gegenpress":
        priority = ["CDM", "CM", "ST"]
    elif primary == "wing_play":
        priority = ["LW", "RW", "LB", "RB"]
    elif primary in ("counter_attack", "direct_football", "route_one"):
        priority = ["ST", "CB", "CDM"]
    elif primary == "park_the_bus":
        priority = ["CB", "CDM", "GK"]
    elif primary in ("youth_development", "moneyball"):
        priority = ["CAM", "CM", "ST", "LW", "RW"]
    elif primary == "high_line_defense":
        priority = ["CB", "LB", "RB", "CDM"]
    elif primary == "total_football":
        priority = ["CM", "LWB", "RWB", "CF"]
    return {
        "primary": primary,
        "band": getattr(phil, "band", None) if phil else None,
        "priority_positions": priority,
        "attribute_weights": weights,
        "season_goal": getattr(phil, "season_goal", None) if phil else None,
    }


def _philosophy_primary_str(club_philosophy) -> str:
    if isinstance(club_philosophy, str):
        return club_philosophy
    if isinstance(club_philosophy, dict):
        return str(club_philosophy.get("primary", "balanced"))
    return str(getattr(club_philosophy, "primary", club_philosophy or "balanced"))


def evaluate_youngster_surprise_potential(player: Player, club_philosophy) -> float:
    """Return a 0-1 score of how much this youngster could overperform
    his market value given the club's philosophy. Used by the AI
    transfer engine to sign undervalued youth that fits its identity."""
    try:
        overall = getattr(player, "overall", None) or 0
        if not overall:
            overall = max(getattr(player, "attack", 50) or 50, getattr(player, "defense", 50) or 50)
    except Exception:
        overall = 50
    try:
        potential = int(getattr(player, "potential", overall) or overall)
    except Exception:
        potential = int(overall or 50)
    try:
        age = int(getattr(player, "age", 25) or 25)
    except Exception:
        age = 25
    potential_gap = potential - int(overall or 50)
    youth_bonus = 1.0 if age <= 21 else (0.6 if age <= 24 else 0.2)
    key = _philosophy_primary_str(club_philosophy)
    philosophy_bonus = 0.4 if key in ("youth_development", "moneyball") else 0.0
    score = (potential_gap / 20.0) * youth_bonus + philosophy_bonus
    return max(0.0, min(1.0, float(score)))


def _league_finish_rank(team: Team) -> tuple[int | None, int | None]:
    """(rank 1-based, league size) from current points; None if no league."""
    try:
        if not team or not team.league_id:
            return (None, None)
        mates = Team.query.filter_by(league_id=team.league_id).all()
        if not mates:
            return (None, None)
        ordered = Team.table_sort_pipeline(mates)
        for i, t in enumerate(ordered, start=1):
            if t.id == team.id:
                return (i, len(ordered))
        return (None, len(ordered))
    except Exception:
        return (None, None)


def _band_shift_direction(team: Team) -> int:
    """+1 band up, -1 band down, 0 no justification this season."""
    rank, size = _league_finish_rank(team)
    if rank is None or not size:
        return 0
    # Top 3 -> up (covers lower-division promotion), bottom 3 -> down.
    if rank <= 3:
        # Already elite-top? Still allow up only if not at ceiling.
        try:
            cur = philosophy_band_for(team.reputation or 50)
            if cur == "elite":
                return 0
        except Exception:
            pass
        # A bottom-table elite club must NOT shift up: if the team is
        # simultaneously in the bottom 3 (tiny leagues), down wins.
        if size > 6 and rank > size - 3:
            return -1
        return 1
    if size >= 3 and rank > size - 3:
        try:
            cur = philosophy_band_for(team.reputation or 50)
            if cur == "survivor":
                return 0
        except Exception:
            pass
        return -1
    return 0


def _apply_philosophy_row(team: Team, primary: str, band: str, season: int):
    prof = _jitter_profile(primary)
    season_goal, patience, ambition = _band_defaults(band)
    phil = None
    try:
        phil = TeamPhilosophy.query.filter_by(team_id=team.id).first()
    except Exception:
        phil = None
    if phil is None:
        phil = TeamPhilosophy(team_id=team.id)
        db.session.add(phil)
    phil.primary = primary
    phil.band = band
    phil.attacking_intent = prof.get("attacking_intent", 0.5)
    phil.pressing_intensity = prof.get("pressing_intensity", 0.5)
    phil.possession_focus = prof.get("possession_focus", 0.5)
    phil.width = prof.get("width", 0.5)
    phil.tempo = prof.get("tempo", 0.5)
    phil.defensive_line = prof.get("defensive_line", 0.5)
    phil.build_up_style = prof.get("build_up_style", 0.5)
    phil.transition_style = prof.get("transition_style", 0.5)
    phil.season_goal = season_goal
    phil.patience = patience
    phil.board_ambition = ambition
    phil.last_shift_season = season
    return phil


def _do_within_band_shift(team: Team, season: int):
    band = philosophy_band_for(getattr(team, "reputation", 50) or 50)
    allowed = list(PHILOSOPHY_BANDS[band]["allowed"])
    cur = None
    try:
        row = TeamPhilosophy.query.filter_by(team_id=team.id).first()
        cur = row.primary if row else None
    except Exception:
        cur = None
    pool = [p for p in allowed if p != cur] or allowed
    new_primary = random.choice(pool) if pool else (cur or "balanced")
    phil = _apply_philosophy_row(team, new_primary, band, season)
    msg = Message(
        title="تحول تكتيكي!",
        content=f"غيّر {team.name} هويته إلى {new_primary} ضمن نفس الفئة.",
        type="board", created_day=getattr(GameState.query.first(), "current_day", 0) or 0,
    )
    db.session.add(msg)
    return phil, msg


def _do_band_shift(team: Team, direction: int, season: int):
    cur_band = philosophy_band_for(getattr(team, "reputation", 50) or 50)
    try:
        idx = _BAND_ORDER.index(cur_band)
    except ValueError:
        idx = 0
    new_idx = max(0, min(len(_BAND_ORDER) - 1, idx + direction))
    if new_idx == idx:
        return _do_within_band_shift(team, season)
    new_band = _BAND_ORDER[new_idx]
    allowed = list(PHILOSOPHY_BANDS[new_band]["allowed"])
    cur = None
    try:
        row = TeamPhilosophy.query.filter_by(team_id=team.id).first()
        cur = row.primary if row else None
    except Exception:
        cur = None
    pool = [p for p in allowed if p != cur] or allowed
    new_primary = random.choice(pool) if pool else (cur or "balanced")
    try:
        team.reputation = max(30, min(99, int((team.reputation or 50) + (5 if direction > 0 else -5))))
    except Exception:
        pass
    phil = _apply_philosophy_row(team, new_primary, new_band, season)
    arrow = "صعود" if direction > 0 else "هبوط"
    msg = Message(
        title="تحول هوية النادي!",
        content=f"{arrow} هوية {team.name}: أصبح {new_primary} (فئة {new_band}).",
        type="board", created_day=getattr(GameState.query.first(), "current_day", 0) or 0,
        urgent=True,
    )
    db.session.add(msg)
    return phil, msg


def roll_season_philosophy_shifts(state: GameState) -> list[Message]:
    """Called once per season end. With a very low probability, a club
    shifts its philosophy (within its band) or shifts bands entirely.
    Returns the messages generated (for the inbox)."""
    msgs: list[Message] = []
    try:
        teams = Team.query.all()
    except Exception:
        return msgs
    try:
        season = state.season_number or 1 if state else 1
    except Exception:
        season = 1
    for team in teams:
        try:
            # Lazily ensure every club has a band-correct identity first.
            has = TeamPhilosophy.query.filter_by(team_id=team.id).first()
            if has is None:
                assign_team_philosophy(team)
        except Exception:
            pass
        roll = random.random()
        if roll >= PHILOSOPHY_SHIFT_CHANCE:
            continue
        try:
            if roll < PHILOSOPHY_BAND_SHIFT_CHANCE:
                direction = _band_shift_direction(team)
                if direction == 0:
                    _, m = _do_within_band_shift(team, season)
                else:
                    _, m = _do_band_shift(team, direction, season)
                msgs.append(m)
            else:
                _, m = _do_within_band_shift(team, season)
                msgs.append(m)
        except Exception:
            continue
    return msgs


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

    # Numeric keys are players; the reserved FORMATION_LAYOUT_KEY (the chosen
    # preset formation) may ride along and is never counted as a player.
    def _as_id(key):
        try:
            return int(key)
        except (TypeError, ValueError):
            return None

    player_ids = [i for i in (_as_id(k) for k in positions.keys()) if i is not None]
    if not player_ids:
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
    ids = set()
    for key in positions.keys():
        try:
            ids.add(int(key))
        except (TypeError, ValueError):
            pass  # reserved keys like FORMATION_LAYOUT_KEY are not players
    for p in player_team.players:
        p.is_starting = p.id in ids


def slot_compatibility(player_detailed: str, slot_pos: str) -> float:
    """How well a player fits a preset slot: exact detailed > band > any.

    Goalkeepers only ever fill GK slots (0 otherwise) so the exactly-one-GK
    rule can never be broken by the by-ability fill. Players carrying a
    coarse-only position (GK/DEF/MID/FWD, older data) map to themselves.
    """
    detail = (player_detailed or "").upper()
    slot_pos = (slot_pos or "").upper()
    if detail == slot_pos:
        return ABILITY_FILL_EXACT_WEIGHT
    det_coarse = _DETAILED_TO_COARSE.get(detail, detail)
    slot_coarse = _DETAILED_TO_COARSE.get(slot_pos, slot_pos)
    if "GK" in (det_coarse, slot_coarse):
        return 0.0
    if det_coarse == slot_coarse:
        return ABILITY_FILL_GROUP_WEIGHT
    return ABILITY_FILL_ANY_WEIGHT


def effective_slot_ability(player) -> float:
    """Fitness-adjusted current ability used for the by-ability slot fill."""
    overall = player.overall or 0
    if not overall:
        overall = max(player.attack or 0, player.defense or 0)
    fitness = max(0, min(100, player.fitness if player.fitness is not None else 100))
    return float(overall) * (
        (1.0 - ABILITY_FILL_FITNESS_WEIGHT)
        + ABILITY_FILL_FITNESS_WEIGHT * fitness / 100.0
    )


def assign_players_to_slots(players, slots, *, exclude_ids=None) -> tuple[list, list]:
    """By-ability assignment of players into a preset formation's slots.

    Every slot is scored as position fit × fitness-weighted ability; injured
    players and excluded ids are never picked. Slots are processed
    most-constrained-first so dedicated positions are not swallowed early.
    A slot whose compatible candidate pool is exhausted stays empty and is
    reported by index (goalkeepers are never slotted outfield or vice versa).

    Returns (assignments, unfilled_indexes): assignments is a list of
    {"slot_index", "pos", "x", "y", "player_id"} sorted by slot order.
    """
    excluded = set()
    for i in (exclude_ids or []):
        try:
            excluded.add(int(i))
        except (TypeError, ValueError):
            pass
    available = [
        p for p in (players or [])
        if (p.injury_days or 0) == 0 and p.id not in excluded
    ]

    ranked: list[tuple[int, dict, list]] = []
    for idx, slot in enumerate(slots or []):
        candidates = [
            (
                slot_compatibility(p.detailed_position or p.position, slot["pos"])
                * effective_slot_ability(p),
                p.id,
            )
            for p in available
        ]
        candidates = sorted(
            (c for c in candidates if c[0] > 0), key=lambda t: t[0], reverse=True
        )
        ranked.append((idx, slot, candidates))
    ranked.sort(key=lambda t: len(t[2]))

    assignments: dict[int, int] = {}
    used: set = set()
    unfilled: list[int] = []
    for idx, _slot, candidates in ranked:
        choice = next((pid for _score, pid in candidates if pid not in used), None)
        if choice is None:
            unfilled.append(idx)
        else:
            assignments[idx] = choice
            used.add(choice)

    filled_slots = slots or []
    result = [
        {
            "slot_index": idx,
            "pos": filled_slots[idx]["pos"],
            "x": filled_slots[idx]["x"],
            "y": filled_slots[idx]["y"],
            "player_id": pid,
        }
        for idx, pid in sorted(assignments.items())
    ]
    return result, sorted(unfilled)


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
def transfer_window_state(state: GameState | None = None) -> str:
    """Phase 1 (item 6): 'open' or 'closed'.

    Manual override wins; otherwise derived from the calendar date
    (summer Jun-Aug, winter Jan). Unparseable/missing date defaults to
    open so tests and fresh saves never lock the market by accident.
    """
    state = state or GameState.query.first()
    override = (getattr(state, "transfer_window_override", None) or "").strip().lower() if state else ""
    if override in ("open", "closed"):
        return override
    date_str = getattr(state, "current_date", None) if state else None
    try:
        month = datetime.strptime(date_str, "%Y-%m-%d").month if date_str else 0
    except Exception:
        return "open"
    if month in TRANSFER_WINDOW_SUMMER_MONTHS or month in TRANSFER_WINDOW_WINTER_MONTHS:
        return "open"
    return "closed"


def is_deadline_day(state: GameState | None = None) -> bool:
    """Phase 1 (item 7): last days of a window — auctions, fast decisions."""
    state = state or GameState.query.first()
    if transfer_window_state(state) != "open":
        return False
    date_str = getattr(state, "current_date", None) if state else None
    try:
        from datetime import date as _date
        d = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else None
    except Exception:
        return False
    if not d:
        return False
    if d.month == 8 and d.day > 31 - TRANSFER_DEADLINE_DAYS:
        return True
    if d.month == 1 and d.day > 31 - TRANSFER_DEADLINE_DAYS:
        return True
    return False


def set_transfer_window(state: GameState, mode: str) -> str:
    """Force the window 'open' / 'closed', or 'auto' to follow the calendar."""
    mode = (mode or "").strip().lower()
    if mode == "auto":
        state.transfer_window_override = None
    elif mode in ("open", "closed"):
        state.transfer_window_override = mode
    else:
        return "وضع غير صالح! استخدم open أو closed أو auto"
    return f"نافذة الانتقالات: {'تلقائي' if mode == 'auto' else ('مفتوحة' if mode == 'open' else 'مغلقة')}"


def get_transfer_status(state: GameState | None = None) -> dict:
    state = state or GameState.query.first()
    window = transfer_window_state(state)
    return {
        "window": window,
        "window_open": window == "open",
        "deadline": is_deadline_day(state),
    }


def get_player_valuation(player: Player) -> int:
    """Phase 4 (item 20): clubs price form, age and morale — not a fixed number."""
    base = player.market_value or MARKET_VALUE_MIN
    value = float(base)
    value += (player.goals or 0) * VALUATION_GOAL_WEIGHT
    value += (player.assists or 0) * VALUATION_ASSIST_WEIGHT
    age = player.age or 24
    if age <= 23:
        value *= VALUATION_YOUTH_BONUS
    elif age >= 32:
        value *= VALUATION_VETERAN_MALUS
    morale = player.morale or 50
    if morale > 60:
        value += ((morale - 60) // 20) * VALUATION_MORALE_BONUS_STEP
    if player.transfer_listed:
        value *= TRANSFER_LIST_DISCOUNT
    return max(MARKET_VALUE_MIN, int(value))


def calc_agent_fee(amount: int, player: Player | None = None) -> int:
    """Phase 4 (item 17): the agent's cut, paid by the buyer on top."""
    value = (player.market_value if player and player.market_value else 0) or amount or 0
    ratio = AGENT_FEE_STAR_RATIO if value >= AGENT_FEE_STAR_THRESHOLD else AGENT_FEE_RATIO
    return max(0, int((amount or 0) * ratio))


def ffp_check(team: Team, extra_spend: int = 0) -> tuple[bool, str]:
    """Phase 5 (item 23): spending capped by income (financial fair play)."""
    allowed = (team.season_income or 0) + FFP_BASE_ALLOWANCE
    projected = (team.season_spend or 0) + (extra_spend or 0)
    if projected * FFP_SPEND_RATIO > allowed:
        return False, (
            f"الرقابة المالية (FFP) تمنع الصفقة! إنفاقك الموسمي {projected:,}$ "
            f"يتجاوز المسموح {int(allowed / (FFP_SPEND_RATIO or 1)):,}$"
        )
    return True, ""


def _record_spend(team: Team, amount: int) -> None:
    team.season_spend = (team.season_spend or 0) + max(0, amount or 0)


def _record_income(team: Team, amount: int) -> None:
    team.season_income = (team.season_income or 0) + max(0, amount or 0)


def _apply_contract(
    player: Player,
    wage: int = 0,
    years: int = 3,
    signing_bonus: int = 0,
    release_clause: int | None = None,
) -> None:
    """Phase 2 (items 1-3): wage talks, duration, signing bonus, release clause."""
    years = max(CONTRACT_MIN_YEARS, min(CONTRACT_MAX_YEARS, int(years or 3)))
    value = player.market_value or MARKET_VALUE_MIN
    min_wage = int(value * WAGE_YEARLY_MIN_RATIO)
    max_wage = int(value * WAGE_YEARLY_MAX_RATIO)
    wage = max(0, int(wage or min_wage))
    wage = min(wage, max_wage * 3)  # superstar premium allowed, capped
    player.wage = wage
    player.contract_days = years * CONTRACT_DAYS_PER_YEAR
    player.signing_bonus = max(0, int(signing_bonus or 0))
    if release_clause:
        floor = int(value * RELEASE_CLAUSE_MIN_MULT)
        player.release_clause = max(floor, int(release_clause))
    elif player.release_clause is None:
        player.release_clause = int(value * RELEASE_CLAUSE_DEFAULT_MULT)


def purchase_player(
    player_team: Team,
    target: Player,
    day: int,
    sell_on_clause: float = 0.0,
    wage: int = 0,
    years: int = 3,
    signing_bonus: int = 0,
    release_clause: int | None = None,
    installments: int = 1,
    buy_back_price: int | None = None,
    first_refusal: bool = False,
    performance_bonus: int = 0,
    performance_target: int = 0,
    use_release_clause: bool = False,
) -> tuple[bool, str]:
    if not target:
        return False, "اللاعب غير موجود!"

    if target.team_id == player_team.id:
        return False, "هذا اللاعب في فريقك بالفعل!"

    # Phase 1 (item 6): no permanent deals outside the window — except
    # free agents, who can be signed at any time (item 8).
    if target.team_id is not None and transfer_window_state() == "closed":
        return False, "نافذة الانتقالات مغلقة! الصفقات الدائمة متاحة فقط في الصيف والشتاء"

    # FM: rivalry rejection — loyal players refuse to betray their club.
    rival = RIVALS.get(target.team.name if target.team else "")
    if rival and rival == player_team.name and target.personality == "Loyal":
        return False, f"اللاعب {target.name} يرفض الانتقال إلى الغريم التقليدي {player_team.name}!"

    # Phase 2 (item 3): triggering the release clause forces the sale.
    if use_release_clause:
        if not target.release_clause:
            return False, "لا يوجد شرط جزائي في عقد اللاعب!"
        value = int(target.release_clause)
        sell_on_clause = 0.0
    else:
        value = get_asking_price(target)
        if sell_on_clause:
            value = int(value * (1.0 - SELL_ON_CLAUSE_DISCOUNT_RATIO * sell_on_clause))

    installments = max(1, min(INSTALLMENTS_MAX, int(installments or 1)))
    agent_fee = calc_agent_fee(value, target)
    signing_bonus = max(0, int(signing_bonus or 0))
    max_bonus = int((target.market_value or 0) * SIGNING_BONUS_MAX_RATIO)
    if signing_bonus > max_bonus:
        return False, f"بونص التوقيع يتجاوز الحد ({max_bonus:,}$)!"
    upfront = int(value / installments) if installments > 1 else value
    total_now = upfront + agent_fee + signing_bonus

    if player_team.budget < total_now:
        return False, f"الميزانية غير كافية! تحتاج {total_now:,} دولار ولديك {player_team.budget:,} دولار"

    ok, ffp_msg = ffp_check(player_team, total_now)
    if not ok:
        return False, ffp_msg

    # Phase 5 (item 22): the player may snub you for a much bigger club
    # that is also bidding — even if your bid is higher.
    snub = _player_snub_check(target, player_team)
    if snub:
        return False, snub

    player_team.budget -= total_now
    _record_spend(player_team, total_now)
    old_club_id = target.team_id
    seller = db.session.get(Team, old_club_id) if old_club_id else None
    if seller is not None:
        seller.budget = (seller.budget or 0) + upfront
        _record_income(seller, upfront)
        # Phase 5 (item 13): first refusal — the old club hears first next time.
        if first_refusal:
            db.session.add(FirstRefusalClause(player_id=target.id, club_id=old_club_id))
    else:
        # Free agent: signing fee instead of a transfer fee.
        from config import FREE_AGENT_SIGN_FEE_RATIO
        fee = int((target.market_value or 0) * FREE_AGENT_SIGN_FEE_RATIO)
        if player_team.budget < fee:
            player_team.budget += total_now  # roll back the deduction
            return False, f"رسوم توقيع الوكيل الحر {fee:,} تتجاوز ميزانيتك!"
        player_team.budget -= fee
        _record_spend(player_team, fee)
    if installments > 1 and seller is not None:
        _create_installments(target, player_team, seller, value - upfront, installments - 1, day)
    target.team_id = player_team.id
    target.is_starting = False
    target.transfer_listed = False
    target.transfer_requested = False
    _apply_contract(target, wage=wage, years=years,
                    signing_bonus=signing_bonus, release_clause=release_clause)

    # FM: sell-on clause keeps a cut for the selling club on future sales.
    if sell_on_clause and old_club_id:
        clause = SellOnClause(
            player_id=target.id,
            club_id=old_club_id,
            percentage=min(SELL_ON_CLAUSE_MAX, max(0.0, sell_on_clause)),
        )
        db.session.add(clause)

    # Phase 3 (item 12): buy-back — sell now, reclaim later at a fixed price.
    if buy_back_price and old_club_id:
        cap = int(value * BUY_BACK_PRICE_CAP_MULT)
        if buy_back_price > cap:
            db.session.rollback()
            return False, f"سعر إعادة الشراء يتجاوز الحد ({cap:,}$)!"
        db.session.add(BuyBackClause(
            player_id=target.id, club_id=old_club_id, price=int(buy_back_price)))

    # Phase 3 (item 15): performance bonus — extra fee if targets are hit.
    if performance_bonus and old_club_id:
        cap = int(value * PERF_BONUS_MAX_RATIO)
        if performance_bonus > cap:
            db.session.rollback()
            return False, f"مكافأة الأداء تتجاوز الحد ({cap:,}$)!"
        state = GameState.query.first()
        db.session.add(PerformanceBonus(
            player_id=target.id,
            payer_team_id=player_team.id,
            payee_team_id=old_club_id,
            amount=int(performance_bonus),
            target_goals=int(performance_target or 20),
            season=(state.season_number or 1) if state else 1,
        ))

    msg = Message(
        title="انتقال جديد",
        content=f"تم شراء {target.name} مقابل {value:,} دولار"
                + (f" مع بند بيع مستقبلي {int(sell_on_clause * 100)}%" if sell_on_clause else "")
                + (f" بالتقسيط على {installments} دفعات" if installments > 1 else "")
                + (f" بشرط جزائي {target.release_clause:,}$" if target.release_clause else "")
                + (f" + أتعاب وكيل {agent_fee:,}$" if agent_fee else "") + "!",
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

    if transfer_window_state() == "closed":
        return False, "نافذة الانتقالات مغلقة!"

    fee = calc_agent_fee(int(cash), target)
    if (player_team.budget or 0) < cash + fee:
        return False, f"الميزانية لا تغطي النقد + أتعاب الوكيل ({int(cash + fee):,}$)!"
    ok, ffp_msg = ffp_check(player_team, int(cash + fee))
    if not ok:
        return False, ffp_msg

    old_club_id = target.team_id
    old_club = db.session.get(Team, old_club_id) if old_club_id else None
    player_team.budget = max(0, (player_team.budget or 0) - cash - fee)
    _record_spend(player_team, int(cash + fee))
    if old_club:
        old_club.budget = (old_club.budget or 0) + cash
        _record_income(old_club, int(cash))

    target.team_id = player_team.id
    target.is_starting = False
    target.transfer_listed = False
    target.transfer_requested = False
    _apply_contract(target)
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

    if offer.status not in ('pending', 'counter'):
        return False, "هذا العرض لم يعد قيد الانتظار!"

    player = db.session.get(Player, offer.player_id)
    from_team = db.session.get(Team, offer.from_team_id)
    to_team = db.session.get(Team, offer.to_team_id)
    if not from_team or not to_team:
        return False, "العرض غير صالح!"
    state = GameState.query.first()
    day = state.current_day if state else 0
    season = state.season_number if state and state.season_number else 1

    # Latest haggled price wins (phase 4, item 16).
    price = int(offer.counter_amount) if offer.status == 'counter' and offer.counter_amount else int(offer.amount or 0)

    # Phase 5 (item 19/18): loan offers move the player temporarily.
    if offer.is_loan:
        if not player:
            return False, "اللاعب غير موجود!"
        if from_team.is_player_team or to_team.is_player_team:
            return _accept_loan(offer, player, to_team, day)
        return False, "لا يمكنك التصرف في هذا العرض!"

    # Phase 2 (item 5): accepting a pre-contract just records the agreement;
    # the player moves at activate_precontracts() when the deal matures.
    if offer.is_precontract:
        offer.status = 'accepted'
        return True, f"تم قبول العقد المسبق! ينضم {player.name if player else 'اللاعب'} مجاناً في اليوم {offer.effective_day}"

    # Phase 1 (item 6): permanent moves need an open window.
    if transfer_window_state(state) == "closed":
        return False, "نافذة الانتقالات مغلقة! لا صفقات دائمة خارج الصيف والشتاء"

    agent_fee = calc_agent_fee(price, player)
    signing = max(0, int(offer.signing_bonus or 0))
    parts = max(1, min(INSTALLMENTS_MAX, int(offer.installments or 1)))
    upfront = int(offer.upfront_amount) if offer.upfront_amount else (int(price / parts) if parts > 1 else price)
    total_now = upfront + agent_fee + signing

    if from_team.is_player_team:
        # Outgoing sale (player team sells). Honour sell-on clauses.
        if not player:
            return False, "اللاعب غير موجود!"
        # First-refusal notice (phase 3, item 13): old club gets first word.
        _notify_first_refusal(player, price, day)
        from_team.budget = (from_team.budget or 0) + upfront
        _record_income(from_team, upfront)
        _pay_sell_on_clauses(player, price, from_team)
        if parts > 1:
            _create_installments(player, to_team, from_team, price - upfront, parts - 1, day)
        _settle_offer_clauses(offer, to_team, from_team, price, day, season)
        # FM: player swap — an AI player may come the other way.
        if offer.offered_player_id:
            swap_player = db.session.get(Player, offer.offered_player_id)
            if swap_player:
                swap_player.team_id = from_team.id
                swap_player.is_starting = False
        player.team_id = to_team.id
        player.is_starting = False
        player.transfer_listed = False
        player.transfer_requested = False
        _apply_contract(player, wage=offer.wage_offer or 0,
                        years=offer.contract_years or 3,
                        signing_bonus=signing)
        offer.status = 'accepted'
        return True, f"تم بيع {player.name} مقابل {price} دولار"

    # Incoming purchase (player team buys): credit the AI seller, move the
    # swap player out if any, and honour sell-on clauses like outgoing sales.
    if to_team.budget >= total_now:
        # Phase 5 (item 22): the player may still snub you for a bigger club.
        snub = _player_snub_check(player, to_team) if player else None
        if snub:
            return False, snub
        ok, ffp_msg = ffp_check(to_team, total_now)
        if not ok:
            return False, ffp_msg
        to_team.budget -= total_now
        _record_spend(to_team, total_now)
        from_team.budget = (from_team.budget or 0) + upfront
        _record_income(from_team, upfront)
        if player:
            if offer.offered_player_id:
                swap_player = db.session.get(Player, offer.offered_player_id)
                if swap_player and swap_player.team_id == to_team.id:
                    swap_player.team_id = from_team.id
                    swap_player.is_starting = False
            _pay_sell_on_clauses(player, price, from_team)
            if parts > 1:
                _create_installments(player, to_team, from_team, price - upfront, parts - 1, day)
            _settle_offer_clauses(offer, to_team, from_team, price, day, season)
            player.team_id = to_team.id
            player.is_starting = False
            player.transfer_listed = False
            player.transfer_requested = False
            _apply_contract(player, wage=offer.wage_offer or 0,
                            years=offer.contract_years or 3,
                            signing_bonus=signing)
        offer.status = 'accepted'
        return True, f"تم شراء {player.name if player else 'اللاعب'} مقابل {price} دولار"

    return False, "لا يوجد ميزانية كافية!"


def _notify_first_refusal(player: Player, price: int, day: int) -> None:
    """Phase 3 (item 13): holders of first refusal hear about the sale first."""
    holders = FirstRefusalClause.query.filter_by(player_id=player.id).all()
    player_team = Team.query.filter_by(is_player_team=True).first()
    for clause in holders:
        holder = db.session.get(Team, clause.club_id)
        if holder and player_team and holder.id == player_team.id:
            db.session.add(Message(
                title="حق الأولوية!",
                content=f"تنبيه أولوية: {player.name} يُباع مقابل {price:,}$ — لديك {FIRST_REFUSAL_NOTICE_DAYS} أيام لمطابقة العرض!",
                type="transfer", created_day=day, urgent=True,
            ))


def process_offer_reject(offer_id: int) -> tuple[bool, str]:
    offer = db.session.get(TransferOffer, offer_id)
    if not offer:
        return False, "العرض غير موجود!"

    if not _offer_involves_player_team(offer):
        return False, "لا يمكنك التصرف في هذا العرض!"

    offer.status = 'rejected'
    return True, "تم رفض العرض"


# --------------------------------------------------------------------------
# Full transfer system (phases 1-5): windows, contracts, clauses, loans
# --------------------------------------------------------------------------
def _create_installments(
    player: Player,
    payer: Team,
    payee: Team,
    remainder: int,
    parts: int,
    day: int,
) -> None:
    """Phase 3 (item 11): split the rest of the fee into future debts."""
    if remainder <= 0 or parts <= 0:
        return
    per = max(1, int(remainder / parts))
    for i in range(1, parts + 1):
        amount = per if i < parts else (remainder - per * (parts - 1))
        db.session.add(InstallmentDebt(
            player_id=player.id,
            from_team_id=payer.id,
            to_team_id=payee.id,
            amount=max(1, amount),
            due_day=day + INSTALLMENT_INTERVAL_DAYS * i,
        ))


def _player_snub_check(player: Player, bidder: Team) -> str | None:
    """Phase 5 (item 22): the player prefers a much bigger club already bidding."""
    rivals = (
        TransferOffer.query.filter(
            TransferOffer.player_id == player.id,
            TransferOffer.status.in_(["pending", "counter"]),
            TransferOffer.to_team_id != bidder.id,
        ).all()
    )
    for offer in rivals:
        other = db.session.get(Team, offer.to_team_id)
        if not other:
            continue
        if (other.reputation or 0) >= (bidder.reputation or 0) + PLAYER_PREF_REPUTATION_GAP:
            return (
                f"اللاعب {player.name} يفضل {other.name} الأعرق "
                f"ورفض عرضك رغم قيمته!"
            )
    return None


def sign_free_agent(
    player_team: Team,
    target: Player,
    day: int,
    wage: int = 0,
    years: int = 3,
    signing_bonus: int = 0,
) -> tuple[bool, str]:
    """Phase 1 (item 8): free agents sign at any time — no fee, just wages."""
    if not target:
        return False, "اللاعب غير موجود!"
    if target.team_id is not None:
        return False, "هذا اللاعب مرتبط بنادٍ! استخدم الشراء المباشر"
    if target.team_id == player_team.id:
        return False, "هذا اللاعب في فريقك بالفعل!"
    from config import FREE_AGENT_SIGN_FEE_RATIO
    fee = int((target.market_value or MARKET_VALUE_MIN) * FREE_AGENT_SIGN_FEE_RATIO)
    signing_bonus = max(0, int(signing_bonus or 0))
    total = fee + signing_bonus
    if player_team.budget < total:
        return False, f"الميزانية غير كافية! تحتاج {total:,} دولار"
    ok, ffp_msg = ffp_check(player_team, total)
    if not ok:
        return False, ffp_msg
    player_team.budget -= total
    _record_spend(player_team, total)
    target.team_id = player_team.id
    target.is_starting = False
    _apply_contract(target, wage=wage, years=years, signing_bonus=signing_bonus)
    db.session.add(Message(
        title="وكيل حر جديد",
        content=f"انضم {target.name} مجاناً! رسوم توقيع {fee:,}$ + راتب {target.wage:,}$",
        type="transfer", created_day=day,
    ))
    return True, f"تم التعاقد مع {target.name} كوكيل حر!"


def expire_contracts(state: GameState) -> int:
    """Phase 2 (item 4): contracts tick down daily; expired players go free."""
    count = 0
    for p in Player.query.filter(Player.team_id.isnot(None)).all():
        if (p.contract_days or 0) <= 0:
            continue
        p.contract_days = (p.contract_days or 0) - 1
        if p.contract_days <= 0:
            old = db.session.get(Team, p.team_id)
            p.team_id = None
            p.wage = 0
            p.is_starting = False
            p.transfer_listed = False
            p.transfer_requested = False
            count += 1
            if old and old.is_player_team:
                db.session.add(Message(
                    title="انتهاء عقد!",
                    content=f"انتهى عقد {p.name} وأصبح وكيلاً حراً!",
                    type="transfer", created_day=state.current_day, urgent=True,
                ))
    return count


def make_precontract(
    player_team: Team,
    target: Player,
    day: int,
    wage: int = 0,
    years: int = 3,
) -> tuple[bool, str]:
    """Phase 2 (item 5): Bosman — agree now with a player in his last 6 months."""
    if not target or target.team_id is None:
        return False, "العقد المسبق للاعبين المرتبطين بأندية فقط!"
    if target.team_id == player_team.id:
        return False, "هذا اللاعب في فريقك بالفعل!"
    if (target.contract_days or 0) > PRECONTRACT_DAYS:
        left = (target.contract_days or 0)
        return False, f"العقد المسبق متاح فقط في آخر 6 شهور! المتبقي {left} يوم"
    existing = TransferOffer.query.filter_by(
        player_id=target.id, to_team_id=player_team.id,
        is_precontract=True, status="pending",
    ).first()
    if existing:
        return False, "لديك عقد مسبق معلق مع هذا اللاعب بالفعل!"
    offer = TransferOffer(
        player_id=target.id,
        from_team_id=target.team_id,
        to_team_id=player_team.id,
        amount=0,
        status="pending",
        created_day=day,
        deadline_day=day + (target.contract_days or 0),
        wage_offer=max(0, int(wage or 0)),
        contract_years=max(CONTRACT_MIN_YEARS, min(CONTRACT_MAX_YEARS, int(years or 3))),
        is_precontract=True,
        effective_day=day + (target.contract_days or 0),
    )
    db.session.add(offer)
    return True, f"تم توقيع عقد مسبق مع {target.name}! ينضم مجاناً عند انتهاء عقده"


def activate_precontracts(state: GameState) -> int:
    """Players whose pre-contract matured join their new club for free."""
    current = state.current_day or 0
    pending = TransferOffer.query.filter_by(is_precontract=True, status="pending").all()
    count = 0
    for offer in pending:
        if (offer.effective_day or 0) > current:
            continue
        player = db.session.get(Player, offer.player_id)
        new_team = db.session.get(Team, offer.to_team_id)
        if not player or not new_team:
            offer.status = "rejected"
            continue
        player.team_id = new_team.id
        player.is_starting = False
        _apply_contract(player, wage=offer.wage_offer or 0,
                        years=offer.contract_years or 3)
        offer.status = "accepted"
        count += 1
        if new_team.is_player_team:
            db.session.add(Message(
                title="انضمام بعقد مسبق!",
                content=f"انضم {player.name} مجاناً بموجب العقد المسبق!",
                type="transfer", created_day=current, urgent=True,
            ))
    return count


def list_transfer(player_team: Team, player: Player) -> tuple[bool, str]:
    """Phase 1 (item 9): put your player up for sale — offers arrive automatically."""
    if not player or player.team_id != player_team.id:
        return False, "اللاعب ليس في فريقك!"
    if player.transfer_listed:
        return False, "اللاعب معروض للبيع بالفعل!"
    player.transfer_listed = True
    return True, f"تم عرض {player.name} للبيع! الأندية المهتمة ستقدم عروضاً"


def unlist_transfer(player_team: Team, player: Player) -> tuple[bool, str]:
    if not player or player.team_id != player_team.id:
        return False, "اللاعب ليس في فريقك!"
    player.transfer_listed = False
    return True, f"تم سحب {player.name} من قائمة الانتقال"


def request_transfer(player_team: Team, player: Player, day: int) -> tuple[bool, str]:
    """Phase 1 (item 10): an unhappy player formally asks to leave."""
    if not player or player.team_id != player_team.id:
        return False, "اللاعب ليس في فريقك!"
    if player.transfer_requested:
        return False, "طلب الانتقال مقدم بالفعل!"
    player.transfer_requested = True
    player.morale = max(0, (player.morale or 50) - TRANSFER_REQUEST_MORALE_LOSS)
    db.session.add(Message(
        title="طلب انتقال!",
        content=f"قدم {player.name} طلب انتقال رسمياً! معنوياته انخفضت والأندية تراقبه",
        type="transfer", created_day=day, urgent=True,
    ))
    return True, f"قدم {player.name} طلب انتقال رسمياً!"


def withdraw_transfer_request(player_team: Team, player: Player) -> tuple[bool, str]:
    if not player or player.team_id != player_team.id:
        return False, "اللاعب ليس في فريقك!"
    player.transfer_requested = False
    return True, f"سحب {player.name} طلب الانتقال!"


def create_bid(
    bidder: Team,
    target: Player,
    amount: int,
    day: int,
    wage: int = 0,
    years: int = 3,
    signing_bonus: int = 0,
    installments: int = 1,
    sell_on: float = 0.0,
    buy_back_price: int | None = None,
    first_refusal: bool = False,
    performance_bonus: int = 0,
    performance_target: int = 0,
    is_loan: bool = False,
    loan_days: int = 0,
    loan_fee: int = 0,
    buy_option: int | None = None,
) -> tuple[bool, str]:
    """Build a full FIFA-style bid (fee + contract + clauses + loan terms)."""
    if not target or not bidder:
        return False, "بيانات العرض غير صالحة!"
    if target.team_id == bidder.id:
        return False, "اللاعب في فريقك بالفعل!"
    if target.team_id is None and not is_loan:
        return False, "الوكلاء الأحرار يُتعاقد معهم مباشرة بدون عروض!"
    if not is_loan and target.team_id is not None and transfer_window_state() == "closed":
        return False, "نافذة الانتقالات مغلقة!"
    if is_loan:
        loan_days = max(LOAN_MIN_DAYS, min(LOAN_MAX_DAYS, int(loan_days or LOAN_MIN_DAYS)))
    state = GameState.query.first()
    deadline = TRANSFER_DEADLINE_EXPIRY_DAYS if is_deadline_day(state) else 3
    try:
        from config import TRANSFER_OFFER_DEADLINE_DAYS
        deadline = TRANSFER_DEADLINE_EXPIRY_DAYS if is_deadline_day(state) else TRANSFER_OFFER_DEADLINE_DAYS
    except Exception:
        pass
    offer = TransferOffer(
        player_id=target.id,
        from_team_id=target.team_id,
        to_team_id=bidder.id,
        amount=max(0, int(amount or 0)),
        status="pending",
        created_day=day,
        deadline_day=day + deadline,
        offer_round=1,
        wage_offer=max(0, int(wage or 0)),
        contract_years=max(CONTRACT_MIN_YEARS, min(CONTRACT_MAX_YEARS, int(years or 3))),
        signing_bonus=max(0, int(signing_bonus or 0)),
        agent_fee=calc_agent_fee(amount, target),
        installments=max(1, min(INSTALLMENTS_MAX, int(installments or 1))),
        sell_on_clause=max(0.0, min(SELL_ON_CLAUSE_MAX, float(sell_on or 0.0))),
        buy_back_price=int(buy_back_price) if buy_back_price else None,
        first_refusal=bool(first_refusal),
        performance_bonus=max(0, int(performance_bonus or 0)),
        performance_target=max(0, int(performance_target or 0)),
        is_loan=bool(is_loan),
        loan_days=int(loan_days or 0) if is_loan else None,
        loan_fee=max(0, int(loan_fee or 0)),
        buy_option=int(buy_option) if buy_option else None,
    )
    if installments and int(installments) > 1:
        offer.upfront_amount = int(int(amount or 0) / int(installments))
    db.session.add(offer)
    kind = "إعارة" if is_loan else "شراء"
    return True, f"تم إرسال عرض {kind} بقيمة {(amount or 0):,}$ من أجل {target.name}!"


def counter_offer(offer_id: int, new_amount: int, day: int) -> tuple[bool, str]:
    """Phase 4 (item 16): reject-and-counter — haggle for up to N rounds."""
    offer = db.session.get(TransferOffer, offer_id)
    if not offer:
        return False, "العرض غير موجود!"
    if not _offer_involves_player_team(offer):
        return False, "لا يمكنك التفاوض على هذا العرض!"
    if offer.status not in ("pending", "counter"):
        return False, "هذا العرض لم يعد قابلاً للتفاوض!"
    if (offer.offer_round or 1) >= OFFER_MAX_ROUNDS:
        offer.status = "rejected"
        return False, "انهارت المفاوضات بعد عدة جولات! تم رفض العرض"
    new_amount = max(0, int(new_amount or 0))
    if new_amount <= 0:
        return False, "مبلغ التفاوض غير صالح!"
    offer.counter_amount = new_amount
    offer.status = "counter"
    offer.offer_round = (offer.offer_round or 1) + 1
    offer.deadline_day = day + 3
    player = db.session.get(Player, offer.player_id)
    name = player.name if player else "اللاعب"
    return True, f"تم الرد بمقابل {new_amount:,}$ من أجل {name} (جولة {offer.offer_round})"


def _settle_offer_clauses(offer: TransferOffer, buyer: Team, seller: Team | None,
                          price: int, day: int, season: int) -> None:
    """Persist sell-on / buy-back / first-refusal / performance terms."""
    if offer.sell_on_clause and seller is not None:
        db.session.add(SellOnClause(
            player_id=offer.player_id, club_id=seller.id,
            percentage=min(SELL_ON_CLAUSE_MAX, max(0.0, offer.sell_on_clause))))
    if offer.buy_back_price and seller is not None:
        cap = int(price * BUY_BACK_PRICE_CAP_MULT)
        db.session.add(BuyBackClause(
            player_id=offer.player_id, club_id=seller.id,
            price=min(int(offer.buy_back_price), cap)))
    if offer.first_refusal and seller is not None:
        db.session.add(FirstRefusalClause(player_id=offer.player_id, club_id=seller.id))
    if offer.performance_bonus and seller is not None:
        cap = int(price * PERF_BONUS_MAX_RATIO)
        db.session.add(PerformanceBonus(
            player_id=offer.player_id,
            payer_team_id=buyer.id, payee_team_id=seller.id,
            amount=min(int(offer.performance_bonus), cap),
            target_goals=int(offer.performance_target or 20),
            season=season,
        ))


def _accept_loan(offer: TransferOffer, player: Player, borrower: Team,
                 day: int) -> tuple[bool, str]:
    """Phase 5 (items 18-19): loan, optionally with a pre-set buy price."""
    fee = max(0, int(offer.loan_fee or 0))
    if borrower.budget < fee:
        return False, "ميزانية المستعير لا تغطي رسوم الإعارة!"
    ok, ffp_msg = ffp_check(borrower, fee)
    if not ok:
        return False, ffp_msg
    owner = db.session.get(Team, offer.from_team_id)
    borrower.budget -= fee
    _record_spend(borrower, fee)
    if owner is not None:
        owner.budget = (owner.budget or 0) + fee
        _record_income(owner, fee)
    player.loan_from_team_id = offer.from_team_id
    player.loan_to_team_id = borrower.id
    player.loan_until_day = day + (offer.loan_days or LOAN_MIN_DAYS)
    player.loan_fee = fee
    player.loan_buy_option = offer.buy_option
    player.team_id = borrower.id
    player.is_starting = False
    offer.status = "accepted"
    extra = f" مع بند شراء {offer.buy_option:,}$" if offer.buy_option else ""
    return True, f"تمت الإعارة! انضم {player.name} حتى اليوم {player.loan_until_day}{extra}"


def return_expired_loans(state: GameState) -> int:
    """Loan spells end — players go home unless the buy clause was used."""
    current = state.current_day or 0
    count = 0
    for p in Player.query.filter(Player.loan_until_day.isnot(None)).all():
        if (p.loan_until_day or 0) > current:
            continue
        home_id = p.loan_from_team_id or p.team_id
        home = db.session.get(Team, home_id) if home_id else None
        p.team_id = home_id
        p.loan_from_team_id = None
        p.loan_to_team_id = None
        p.loan_until_day = None
        p.loan_buy_option = None
        p.is_starting = False
        count += 1
        if home and home.is_player_team:
            db.session.add(Message(
                title="عودة من الإعارة",
                content=f"عاد {p.name} إلى فريقك بعد انتهاء إعارته",
                type="transfer", created_day=current,
            ))
    return count


def exercise_loan_buy(player_team: Team, player: Player, day: int) -> tuple[bool, str]:
    """Phase 5 (item 19): turn a loan-to-buy into a permanent signing."""
    if not player or player.team_id != player_team.id:
        return False, "اللاعب المعار ليس في فريقك!"
    if not player.loan_buy_option:
        return False, "لا يوجد بند شراء في هذه الإعارة!"
    if transfer_window_state() == "closed":
        return False, "نافذة الانتقالات مغلقة!"
    price = int(player.loan_buy_option)
    if player_team.budget < price:
        return False, f"الميزانية غير كافية! بند الشراء {price:,}$"
    ok, ffp_msg = ffp_check(player_team, price)
    if not ok:
        return False, ffp_msg
    owner = db.session.get(Team, player.loan_from_team_id) if player.loan_from_team_id else None
    player_team.budget -= price
    _record_spend(player_team, price)
    if owner is not None:
        owner.budget = (owner.budget or 0) + price
        _record_income(owner, price)
    player.loan_from_team_id = None
    player.loan_to_team_id = None
    player.loan_until_day = None
    player.loan_buy_option = None
    player.loan_fee = 0
    _apply_contract(player, wage=player.wage or 0, years=3)
    db.session.add(Message(
        title="تفعيل بند الشراء!",
        content=f"فعلت بند شراء {player.name} مقابل {price:,}$ وأصبح لاعباً دائماً!",
        type="transfer", created_day=day, urgent=True,
    ))
    return True, f"تم شراء {player.name} نهائياً مقابل {price:,}$!"


def exercise_buy_back(player_team: Team, player: Player, day: int) -> tuple[bool, str]:
    """Phase 3 (item 12): reclaim a sold player at the agreed buy-back price."""
    clause = BuyBackClause.query.filter_by(
        player_id=player.id, club_id=player_team.id).first() if player else None
    if not clause:
        return False, "لا تملك بند إعادة شراء لهذا اللاعب!"
    if player.team_id == player_team.id:
        return False, "اللاعب في فريقك بالفعل!"
    if transfer_window_state() == "closed":
        return False, "نافذة الانتقالات مغلقة!"
    price = int(clause.price)
    if player_team.budget < price:
        return False, f"الميزانية غير كافية! إعادة الشراء {price:,}$"
    ok, ffp_msg = ffp_check(player_team, price)
    if not ok:
        return False, ffp_msg
    seller = db.session.get(Team, player.team_id) if player.team_id else None
    player_team.budget -= price
    _record_spend(player_team, price)
    if seller is not None:
        seller.budget = (seller.budget or 0) + price
        _record_income(seller, price)
    player.team_id = player_team.id
    player.is_starting = False
    db.session.delete(clause)
    db.session.add(Message(
        title="إعادة شراء!",
        content=f"استعدت {player.name} ببند إعادة الشراء مقابل {price:,}$!",
        type="transfer", created_day=day, urgent=True,
    ))
    return True, f"تمت إعادة شراء {player.name} مقابل {price:,}$!"


def trigger_release_clause(player_team: Team, target: Player, day: int,
                           wage: int = 0, years: int = 3) -> tuple[bool, str]:
    """Phase 2 (item 3): pay the clause — the selling club cannot refuse."""
    if not target or not target.release_clause:
        return False, "لا يوجد شرط جزائي في عقد اللاعب!"
    return purchase_player(player_team, target, day, wage=wage, years=years,
                           use_release_clause=True)


def pay_installments_due(state: GameState) -> int:
    """Phase 3 (item 11): settle deferred fee slices when they mature."""
    current = state.current_day or 0
    due = InstallmentDebt.query.filter_by(paid=False).filter(
        InstallmentDebt.due_day <= current).all()
    for debt in due:
        payer = db.session.get(Team, debt.from_team_id)
        payee = db.session.get(Team, debt.to_team_id)
        if payer is not None:
            payer.budget = max(0, (payer.budget or 0) - debt.amount)
        if payee is not None:
            payee.budget = (payee.budget or 0) + debt.amount
        debt.paid = True
        if payer and payer.is_player_team:
            db.session.add(Message(
                title="قسط انتقال مستحق",
                content=f"تم دفع قسط {debt.amount:,}$ من صفقة {(debt.player.name if debt.player else '')}",
                type="transfer", created_day=current,
            ))
    return len(due)


def check_performance_bonuses(state: GameState) -> int:
    """Phase 3 (item 15): star seasons trigger the bonus fee to the old club."""
    season = state.season_number or 1
    pending = PerformanceBonus.query.filter_by(season=season, paid=False).all()
    count = 0
    for bonus in pending:
        player = db.session.get(Player, bonus.player_id)
        if not player or (player.goals or 0) < (bonus.target_goals or 20):
            continue
        payer = db.session.get(Team, bonus.payer_team_id)
        payee = db.session.get(Team, bonus.payee_team_id)
        if payer is not None:
            payer.budget = max(0, (payer.budget or 0) - bonus.amount)
        if payee is not None:
            payee.budget = (payee.budget or 0) + bonus.amount
        bonus.paid = True
        count += 1
        if payer and payer.is_player_team:
            db.session.add(Message(
                title="مكافأة أداء مستحقة!",
                content=f"سجل {player.name} {player.goals} هدفاً! دُفعت مكافأة {bonus.amount:,}$ لناديه السابق",
                type="transfer", created_day=state.current_day, urgent=True,
            ))
    return count


def auto_transfer_list_offers(state: GameState) -> int:
    """Phase 1 (item 9): listed players attract automatic bids every week."""
    import random as _random

    day = state.current_day or 0
    listed = Player.query.filter_by(transfer_listed=True).filter(
        Player.team_id.isnot(None)).all()
    if not listed:
        return 0
    ai_teams = Team.query.filter_by(is_player_team=False).all()
    if not ai_teams:
        return 0
    made = 0
    mult = TRANSFER_DEADLINE_SURGE if is_deadline_day(state) else 1.0
    for player in listed:
        chance = 0.35 * mult
        if player.transfer_requested:
            from config import TRANSFER_REQUEST_AI_INTEREST_MULT
            chance *= TRANSFER_REQUEST_AI_INTEREST_MULT
        if _random.random() > min(0.9, chance):
            continue
        bidder = _random.choice(ai_teams)
        if bidder.id == player.team_id:
            continue
        valuation = get_player_valuation(player)
        amount = int(valuation * _random.uniform(0.9, 1.1))
        if amount > (bidder.budget or 0):
            continue
        db.session.add(TransferOffer(
            player_id=player.id,
            from_team_id=player.team_id,
            to_team_id=bidder.id,
            amount=amount,
            status="pending",
            created_day=day,
            deadline_day=day + 3,
            wage_offer=int((player.wage or 0) * 1.1),
            contract_years=3,
        ))
        made += 1
        owner = db.session.get(Team, player.team_id)
        if owner and owner.is_player_team:
            db.session.add(Message(
                title="عرض لنجمك المعروض!",
                content=f"قدم {bidder.name} عرضاً {amount:,}$ لشراء {player.name} المعروض للبيع",
                type="transfer", created_day=day, urgent=True,
            ))
    return made


def emit_world_week_messages(state: GameState, summary: dict) -> list[Message]:
    """Convert a world-sim summary into 1-3 Arabic inbox messages (type="world").

    Sections: rival/upset alerts, AI-transfer rumours (scouted players get a
    special call-out), board-confidence nudges. Always emits at least one
    message so the world feels alive, at most WORLD_SIM_MAX_INBOX.
    """
    from config import WORLD_SIM_MAX_INBOX
    from models import ScoutReport

    try:
        day = int(getattr(state, "current_day", 1) or 1)
    except Exception:
        day = 1
    msgs: list[Message] = []

    def _add(title, content, urgent=False):
        if len(msgs) >= WORLD_SIM_MAX_INBOX:
            return
        m = Message(title=title, content=content, type="world",
                    created_day=day, urgent=bool(urgent))
        db.session.add(m)
        msgs.append(m)

    upsets = (summary or {}).get("upsets") or []
    transfers = (summary or {}).get("ai_transfers") or []
    sacked = (summary or {}).get("managers_sacked") or []
    drift = int((summary or {}).get("board_drift") or 0)
    played = int((summary or {}).get("played") or 0)

    if upsets:
        top = upsets[0]
        try:
            _add("مفاجآت الجولة في العالم",
                 f"مفاجأة كبرى: {top.get('winner')} يهزم {top.get('loser')} "
                 f"بنتيجة {top.get('score')} — صراع اللقب يشتعل!",
                 urgent=False)
        except Exception:
            pass
    if transfers:
        first = transfers[0]
        try:
            pname = first.get("player") or "لاعب"
            scouted = False
            try:
                pt = Team.query.filter_by(is_player_team=True).first()
                if pt is not None:
                    pl = Player.query.filter_by(name=pname).first()
                    if pl is not None:
                        rep = ScoutReport.query.filter_by(
                            team_id=pt.id, player_id=pl.id).first()
                        scouted = rep is not None and (rep.knowledge_level or 0) > 0
            except Exception:
                scouted = False
            note = " — لاعب كنت تكشفه! راقب السوق" if scouted else ""
            _add("حركة انتقالات في العالم",
                 f"انتقال: {pname} من {first.get('from')} إلى {first.get('to')} "
                 f"مقابل {first.get('amount', 0):,}${note}")
        except Exception:
            pass
    if sacked:
        try:
            _add("إقالة مدرب في العالم",
                 f"أقيل مدرب {sacked[0].get('team')} بعد النتائج المخيبة "
                 f"({sacked[0].get('reason', 'سوء النتائج')})")
        except Exception:
            pass
    if drift:
        try:
            direction = "يرتفع" if drift > 0 else "ينخفض"
            _add("ثقة المجلس تهتز",
                 f"ثقة المجلس {direction} ({drift:+d}) — منافسوك يحققون نتائج "
                 f"تضغط عليك. حافظ على مركزك!",
                 urgent=abs(drift) >= 2)
        except Exception:
            pass
    if not msgs:
        try:
            _add("أخبار العالم",
                 f"جولة هادئة في العالم: لُعبت {played} مباراة دون مفاجآت كبرى. "
                 f"الكشافون يواصلون العمل.")
        except Exception:
            pass
    return msgs


def maybe_rival_bid(state: GameState) -> int:
    """Phase 5 (item 21): a richer club hijacks a deal you are chasing."""
    import random as _random

    player_team = Team.query.filter_by(is_player_team=True).first()
    if not player_team:
        return 0
    mine = TransferOffer.query.filter_by(
        to_team_id=player_team.id, status="pending").all()
    made = 0
    for offer in mine:
        if _random.random() > RIVAL_BID_CHANCE:
            continue
        player = db.session.get(Player, offer.player_id)
        if not player:
            continue
        richer = [t for t in Team.query.filter_by(is_player_team=False).all()
                  if (t.reputation or 0) >= (player_team.reputation or 0) + RIVAL_REPUTATION_GAP
                  and (t.budget or 0) > (offer.amount or 0)]
        if not richer:
            continue
        hijacker = max(richer, key=lambda t: t.reputation or 0)
        amount = int((offer.amount or 0) * _random.uniform(1.05, 1.25))
        if amount > (hijacker.budget or 0):
            continue
        db.session.add(TransferOffer(
            player_id=player.id,
            from_team_id=offer.from_team_id,
            to_team_id=hijacker.id,
            amount=amount,
            status="pending",
            created_day=state.current_day,
            deadline_day=(state.current_day or 0) + 3,
            wage_offer=int((offer.wage_offer or 0) * 1.15),
            contract_years=offer.contract_years or 3,
        ))
        made += 1
        db.session.add(Message(
            title="منافسة على الصفقة!",
            content=f"دخل {hijacker.name} السباق على {player.name} بعرض {amount:,}$ أعلى من عرضك!",
            type="transfer", created_day=state.current_day, urgent=True,
        ))
    return made


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
    p = Player(
        name=found["name"],
        position=found["position"],
        detailed_position=_pick_detailed_position(found["position"]),
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
    try:
        recompute_aggregates(p)
    except Exception:
        pass
    return p


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

    # Cup rollover first: crown decided champions BEFORE the wipe of
    # leftover unplayed fixtures below (which includes old cup ties).
    try:
        from cup_services import close_season_tournaments
        close_season_tournaments(state.season_number or 1, state)
    except Exception:
        logger.exception("cup season close failed")

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

    # New season's cup & continental editions (qualification uses the final
    # tables / reputations; cup ties are week-0 so they never disturb the
    # league schedule above).
    try:
        from cup_services import seed_initial_cups
        seed_initial_cups(season=(state.season_number or 1) + 1,
                          base_date=new_start.isoformat())
    except Exception:
        logger.exception("new season cup seeding failed")

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
    """Saturday-based dates per league: ``{league_id: {week: iso_date}}``.

    Each league uses its own ``League.rounds()`` (fixture weeks) and its
    own ``season_start_week`` offset (default 1), so pyramids with different
    sizes (e.g. 18-team Bundesliga vs 24-team Championship) each get a
    correct calendar. Falls back to the legacy tier-keyed map when no
    leagues exist yet (fresh DB before the template build).
    """
    import datetime

    try:
        leagues = League.query.order_by(League.id).all()
    except Exception:
        leagues = []
    if not leagues:
        return _legacy_tier_dates(start_date)

    days_ahead = 5 - start_date.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    first_sat = start_date + datetime.timedelta(days=days_ahead)

    out = {}
    for league in leagues:
        try:
            total_rounds = int(league.rounds())
        except Exception:
            total_rounds = TOP_LEAGUE_ROUNDS if (league.tier or 1) == 1 else LOWER_LEAGUE_ROUNDS
        try:
            start_offset = max(0, int(league.season_start_week or 1) - 1)
        except (TypeError, ValueError):
            start_offset = 0
        dates = {}
        rnd = 1
        i = start_offset
        guard = 0
        midweek_slots = {2, 6, 11, 16, 21, 26, 31, 36}
        while rnd <= total_rounds and guard < 120:
            guard += 1
            week_sat = first_sat + datetime.timedelta(weeks=i)
            rel = i - start_offset
            is_intl_break = rel in (4, 9, 14, 30)
            if not is_intl_break:
                dates[rnd] = week_sat.isoformat()
                rnd += 1
                if total_rounds > 38 and rel in midweek_slots and rnd <= total_rounds:
                    week_tue = week_sat + datetime.timedelta(days=3)
                    dates[rnd] = week_tue.isoformat()
                    rnd += 1
            i += 1
        out[league.id] = dates
    return out


def _legacy_tier_dates(start_date):
    """Original 4-tier English calendar (used only before leagues exist)."""
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

    dates_by_league = generate_match_dates(start_date)
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
        "Real Madrid": "Madrid",
        "Atlético Madrid": "Madrid",
        "Rayo Vallecano": "Madrid",
        "Leganés": "Madrid",
        "Barcelona": "Barcelona",
        "Espanyol": "Barcelona",
        "Sevilla": "Seville",
        "Real Betis": "Seville",
        "Bayern München": "Munich",
        "1860 München": "Munich",
        "Inter": "Milan",
        "AC Milan": "Milan",
        "Roma": "Rome",
        "Lazio": "Rome",
        "Paris Saint-Germain": "Paris",
        "Paris FC": "Paris",
        "Red Star": "Paris",
        "Benfica": "Lisbon",
        "Sporting CP": "Lisbon",
        "Casa Pia": "Lisbon",
        "Porto": "Porto",
        "Boavista": "Porto",
        "Celtic": "Glasgow",
        "Rangers": "Glasgow",
        "Ajax": "Amsterdam",
        "Galatasaray": "Istanbul",
        "Fenerbahçe": "Istanbul",
        "Beşiktaş": "Istanbul",
        "Başakşehir": "Istanbul",
    }

    for m in matches:
        home_team = m.home_team
        league = home_team.league if home_team else None
        tier = league.tier if league else 1
        base_date_str = None
        if league is not None:
            per_league = dates_by_league.get(league.id)
            if isinstance(per_league, dict):
                base_date_str = per_league.get(m.week)
            if base_date_str is None:
                tier_dates = dates_by_league.get(tier)
                if isinstance(tier_dates, dict):
                    base_date_str = tier_dates.get(m.week)
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


def _league_name_for(country, tier):
    """Reverse lookup: (country, tier) -> league name."""
    for name, key in LEAGUE_NAME_TO_KEY.items():
        if key == (country, tier):
            return name
    return f"{country} League {tier}"


def _pick_detailed_position(coarse: str) -> str:
    mapping = {
        "GK": ["GK"],
        "DEF": ["CB", "CB", "CB", "LB", "RB", "LWB", "RWB"],
        "MID": ["CDM", "CM", "CM", "CAM", "LM", "RM"],
        "FWD": ["LW", "RW", "CF", "ST", "ST"],
    }
    pool = mapping.get(coarse, ["CM"])
    return random.choice(pool)


_DETAILED_TO_COARSE = {
    "GK": "GK",
    "CB": "DEF", "LB": "DEF", "RB": "DEF", "LWB": "DEF", "RWB": "DEF",
    "CDM": "MID", "CM": "MID", "CAM": "MID", "LM": "MID", "RM": "MID",
    "LW": "FWD", "RW": "FWD", "CF": "FWD", "ST": "FWD",
}


def coarse_position_of_detailed(det: str) -> str:
    """Inverse of _pick_detailed_position for roster-driven generation."""
    return _DETAILED_TO_COARSE.get(det, "MID")


def build_squad_roster(squad_size: int) -> list:
    """Detailed-position roster for a generated squad.

    Starts from SQUAD_CORE_DETAILED_POSITIONS (guarantees a full backline:
    LB, CB, CB, RB plus wing-back covers — and exactly 2 GKs) and fills any
    remaining slots from the weighted SQUAD_EXTRA_POSITION_WEIGHTS pool.
    """
    roster = list(SQUAD_CORE_DETAILED_POSITIONS)
    remaining = squad_size - len(roster)
    if remaining > 0:
        roster += random.choices(
            list(SQUAD_EXTRA_POSITION_WEIGHTS.keys()),
            weights=list(SQUAD_EXTRA_POSITION_WEIGHTS.values()),
            k=remaining,
        )
    return roster


def apply_starting_xi(players: list) -> None:
    """Mark one starter per SQUAD_STARTING_XI role: best potential wins.

    Produces a classic 4-4-2 (exactly one GK, a LB/CB/CB/RB backline)
    instead of legacy "first 11 after shuffling", which fielded two
    goalkeepers or none. XI slots are consumed in order: a pre-flagged
    starter fills its role's slot without re-flagging another player, and
    roles without any matching player stay unfilled (never happens with
    the 18-man core roster).
    """
    if not players:
        return
    flagged: dict = {}
    for p in players:
        if p.is_starting:
            det = (p.detailed_position or "")
            flagged[det] = flagged.get(det, 0) + 1
    filled: dict = {}
    for role in SQUAD_STARTING_XI:
        filled[role] = filled.get(role, 0)
        if flagged.get(role, 0) > filled[role]:
            filled[role] += 1
            continue
        candidates = [
            p for p in players
            if not p.is_starting
            and (p.detailed_position or "") == role
        ]
        if not candidates:
            continue
        max(candidates, key=lambda p: p.potential or 0).is_starting = True
        filled[role] += 1


def _roll_detailed_attrs(floor: int, ceiling: int) -> dict:
    keys: set = set()
    try:
        for w in POSITION_OVERALL_WEIGHTS.values():
            keys.update(w.keys())
        keys.update(ATTACK_ATTRS)
        keys.update(DEFENSE_ATTRS)
    except Exception:
        keys = {"passing", "vision"}
    out = {}
    for k in keys:
        try:
            out[k] = random.randint(int(floor), int(ceiling))
        except Exception:
            out[k] = 50
    return out


def _seed_star_players() -> None:
    """Override one generated squad member per entry in SEED_STAR_PLAYERS.

    Star swaps are like-for-like: the victim prefers XI starters of the same
    coarse group, and same detailed role where possible, so the squad keeps
    a complete backline (a CB star takes a CB, Hakimi takes the RB, ...
    never the club's only LB/RB)."""

    def _primary(p):
        return p.attack if p.position in ("FWD", "MID") else p.defense

    seeded_ids: set = set()
    for star in SEED_STAR_PLAYERS:
        team = Team.query.filter_by(name=star.get("club")).first()
        if team is None:
            logger.warning("Star club %s not found, skipping %s",
                           star.get("club"), star.get("name"))
            continue
        squad = Player.query.filter_by(team_id=team.id).all()
        if not squad:
            continue
        target = star.get("detailed") or _pick_detailed_position(
            star.get("position", "MID"))
        coarse = star.get("position", "MID")
        # A privilege of the back four: never strand a squad without LB/RB.
        scarce = {"LB", "RB"} if coarse == "DEF" else set()
        same_pos = [p for p in squad if p.position == coarse]

        def _tier_list():
            tiers = [
                [p for p in same_pos if p.is_starting],
                [p for p in same_pos],
                [p for p in squad],
            ]
            safe = [t for t in tiers
                    if [p for p in t
                        if (p.detailed_position or "") not in scarce]]
            return safe or tiers

        def _pools(tier):
            free = [p for p in tier if p.id not in seeded_ids]
            matching = [p for p in free
                        if p.is_starting and (p.detailed_position or "") == target]
            started = [p for p in free if p.is_starting]
            return [matching, started, free]

        victim = None
        for tier in _tier_list():
            for pool in _pools(tier):
                if pool:
                    victim = max(pool, key=_primary)
                    break
            if victim:
                break
        if victim is None:
            logger.warning("No free squad slot for %s at %s",
                           star.get("name"), star.get("club"))
            continue
        seeded_ids.add(victim.id)
        victim.name = star["name"]
        victim.position = coarse
        victim.detailed_position = target
        victim.attack = star["attack"]
        victim.defense = star["defense"]
        victim.potential = star["potential"]
        victim.age = star["age"]
        victim.region = star.get("region", "Europe")
        victim.is_starting = True
        victim.market_value = calculate_market_value(
            victim.attack, victim.defense, victim.potential,
            victim.age, team.reputation or 60)


def _ensure_global_rating_floor() -> None:
    """Pin one bench player's primary ability to 48 in a floor-47 league.

    The Gaussian generator clamps every attribute into its league band, so
    nothing can fall below 47; this pins the global minimum at 48 (inside
    the required 47-50 window) deterministically instead of relying on
    sampling luck across ~13k players.
    """
    low_keys = [k for k, (fl, _cl) in LEAGUE_RATING_BANDS.items() if fl == 47]
    for key in low_keys:
        country, tier = key
        league = League.query.filter_by(country=country, tier=tier).first()
        if league is None:
            continue
        teams = Team.query.filter_by(league_id=league.id).all()
        for team in teams:
            bench = [p for p in team.players if not p.is_starting]
            fwds = [p for p in bench if p.position in ("FWD", "MID")]
            if fwds:
                anchor = fwds[0]
                anchor.attack = 48
                anchor.defense = max(47, min(anchor.defense or 48, 62))
                anchor.market_value = calculate_market_value(
                    anchor.attack, anchor.defense, anchor.potential or 55,
                    anchor.age or 25, team.reputation or 40)
                return
    # Fallback: any bench forward/mid anywhere (should never trigger).
    fallback = Player.query.filter_by(is_starting=False).first()
    if fallback is not None:
        fallback.attack = 48


def _build_template_database() -> None:
    # Start from a clean slate: on a schema-version bump the old template
    # still holds the previous leagues/teams, and league/team names are
    # UNIQUE — without this the rebuild would crash on duplicates.
    # (Row deletes, not drop_all: the player/team FK cycle makes DROP
    # sorting unreliable on SQLite.)
    db.create_all()
    for table in reversed(db.metadata.sorted_tables):
        db.session.execute(table.delete())
    db.session.commit()
    ensure_indexes()

    leagues: list = []
    for (country, tier), team_names in EUROPEAN_TEAMS.items():
        name = _league_name_for(country, tier)
        floor, ceiling = LEAGUE_RATING_BANDS[(country, tier)]
        reputation = LEAGUE_REPUTATIONS.get((country, tier), 50)
        league = League(
            name=name,
            tier=tier,
            reputation=reputation,
            country=country,
            rating_floor=floor,
            rating_ceiling=ceiling,
            season_start_week=1,
            is_continental=False,
        )
        db.session.add(league)
        leagues.append(league)
    db.session.commit()

    for league, ((country, tier), team_names) in zip(leagues, EUROPEAN_TEAMS.items()):
        league_key = (country, tier)
        floor, ceiling = LEAGUE_RATING_BANDS[league_key]
        for team_name in team_names:
            reputation = CLUB_REPUTATIONS.get(
                team_name, league.reputation + random.randint(-8, 8))
            reputation = max(30, min(99, int(reputation)))
            team = Team(
                name=team_name,
                morale=random.randint(60, 80),
                budget=calculate_club_budget(league_key, reputation),
                reputation=reputation,
                is_player_team=False,
                league_id=league.id,
                training_facilities=random.randint(1, 5),
                youth_facilities=random.randint(1, 5),
            )
            db.session.add(team)
            db.session.flush()

            squad_size = random.randint(18, 24)
            # Structured detailed roster: the 18-man core guarantees every
            # club a full backline (LB, CB, CB, RB + wing-back covers), two
            # GKs and a realistic spread; extras are drawn weighted from the
            # detailed pool. Legacy code rolled coarse positions then canned
            # randomised detailed tags, which left a third of the world
            # without any LB or RB (some clubs fielded 6 GKs).
            positions = build_squad_roster(squad_size)
            drafted = []
            for j, det in enumerate(positions):
                pos = coarse_position_of_detailed(det)
                region = random.choice(["Europe", "Middle_East", "South_America"])
                stats = generate_player_stats_v2(floor, ceiling, pos, region)
                value = calculate_market_value(
                    stats["attack"], stats["defense"], stats["potential"],
                    stats["age"], reputation)
                dattrs = _roll_detailed_attrs(floor, ceiling)
                player = Player(
                    name=generate_player_name(region),
                    position=pos,
                    detailed_position=det,
                    attack=stats["attack"],
                    defense=stats["defense"],
                    potential=stats["potential"],
                    age=stats["age"],
                    height=stats["height"],
                    weight=stats["weight"],
                    personality=stats["personality"],
                    region=region,
                    market_value=value,
                    team_id=team.id,
                    is_starting=False,
                    stamina=random.randint(60, 100),
                    technique=random.randint(30, 90),
                    speed=random.randint(30, 90),
                    traits=generate_player_traits(),
                    sharpness=random.randint(60, 100),
                    morale=random.randint(50, 80),
                    **{k: v for k, v in dattrs.items()
                        if hasattr(Player, k) and k not in (
                            "stamina", "technique", "speed",
                            "sharpness", "morale")},
                )
                # Derive position-weighted overall + attack/defense splits.
                try:
                    recompute_aggregates(player)
                except Exception:
                    player.overall = max(player.attack or 50, player.defense or 50)
                db.session.add(player)
                drafted.append(player)
            # Pick a real XI: the best available player per role
            # (exactly one GK, a LB/CB/CB/RB backline). Legacy logic
            # marked "first 11 after shuffling" and keeper around two GKs.
            apply_starting_xi(drafted)
        db.session.commit()

    _seed_star_players()
    db.session.commit()
    _ensure_global_rating_floor()
    db.session.commit()
    try:
        recompute_all_aggregates()
        db.session.commit()
    except Exception:
        pass
    for _t in Team.query.all():
        try:
            assign_team_philosophy(_t)
        except Exception:
            continue
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

    player_league = League.query.filter_by(name="Premier League").first()
    if player_league is None:
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

    # Domestic cups + continental league phases for season 1.
    try:
        import datetime as _dt
        from cup_services import seed_initial_cups
        seed_initial_cups(season=1, base_date=_dt.date.today().isoformat())
    except Exception:
        logger.exception("template cup seeding failed")
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