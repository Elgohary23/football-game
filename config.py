"""Game configuration & tuning constants.

Centralizes every "magic number" in one place so game balance can be
adjusted without touching simulation or routing logic.
"""

# --- Save slots / database ---
MAX_SAVE_SLOTS = 5
TEMPLATE_DB_FILENAME = "template.db"
DB_FILENAME = "db.sqlite3"

# --- Squad / Starting XI ---
STARTERS_LIMIT = 11
MAX_SQUAD_SIZE = 30
DAYS_PER_WEEK = 7
MIN_LINEUP_RATING = 10.0

# --- Finance ---
DEFAULT_BUDGET = 1_000_000
# Starting budget per league tier (1 = Premier League .. 4 = League Two).
# Single source of truth — use services.get_budget_for_tier() everywhere
# instead of hard-coding multipliers.
TIER_BUDGETS = {
    1: 4_000_000,
    2: 3_000_000,
    3: 2_000_000,
    4: 1_000_000,
}
TRAINING_COST_PER_LEVEL = 200_000
YOUTH_COST_PER_LEVEL = 150_000
# Per-club budget variation around the tier base: each reputation point
# above/below the league's base reputation shifts the starting budget by
# this step, plus a small random jitter (fraction). Keeps clubs in the
# same division distinct while tier bases keep divisions ordered.
BUDGET_REPUTATION_STEP = 100_000
BUDGET_JITTER = 0.05
MAX_FACILITY_LEVEL = 10
STAFF_FIRE_COMPENSATION_RATIO = 0.3
STAFF_MARKET_POOL_LIMIT = 8
PROMOTION_BONUS_PER_TIER = 500_000

MARKET_VALUE_MIN = 50_000
MARKET_VALUE_MAX = 300_000_000
MARKET_PERF_GOAL = 3_000
MARKET_PERF_ASSIST = 2_000
MARKET_VALUE_GROWTH_CAP = 0.05
MARKET_VALUE_DECAY_RATE = 0.01

# --- Fitness / injury ---
MAX_FITNESS = 100
DAILY_FITNESS_RECOVERY = 5
FITNESS_STARTING_DECAY = 10
FITNESS_DECAY_LAZY = 15
FITNESS_DECAY_PROFESSIONAL = 8
INJURY_CHANCE_SOFT = 0.03
INJURY_CHANCE_HARD = 0.05
INJURY_MIN_DAYS = 3
INJURY_MAX_DAYS_SOFT = 21
INJURY_MAX_DAYS_HARD = 28

# --- Youth academy ---
YOUTH_GENERATION_CHANCE = 0.12
YOUTH_ACADEMY_AGE = 16
YOUTH_RETIREMENT_AGE = 33
REGEN_AGE_MIN = 16
REGEN_AGE_RANGE = 3

# --- Match simulation ---
HOME_ATTACK_BONUS = 1.1
GOAL_EXPECTANCY_MIN = 0.15
GOAL_EXPECTANCY_MAX = 4.5
HOME_GOAL_MULTIPLIER = 1.0
AWAY_GOAL_MULTIPLIER = 0.85
SCORE_DIVISOR = 8
MAX_GOALS_PER_TEAM = 7
RANDOM_GOAL_RANGE = 2
LEADER_MORALE_BONUS = 5
SHORT_SQUAD_PENALTY_CAP = 11

# --- Seasons ---
TOP_LEAGUE_ROUNDS = 38
LOWER_LEAGUE_ROUNDS = 46
# Clubs promoted/relegated per adjacent league pair each season.
# Matches the standings legend ("relegation zone: last 3").
PROMOTION_RELEGATION_SPOTS = 3

# --- Player development ---
DEVELOPMENT_UPDATE_DAYS = 30
SEASON_RESET_PLAYER_STATS = True

# --- Calendar fast-forward (simulate_calendar day loop) ---
# Commit the transaction this often so an error on day N keeps days 1..N-1
# instead of rolling the whole stretch back to day 1 (Bug 1 fix).
CALENDAR_COMMIT_EVERY_DAYS = 1
# Optional server-side throttle: sleep this many ms after each simulated day.
# 0 = flat out (default, keeps requests fast); raise to pace long sims.
CALENDAR_DAY_THROTTLE_MS = 0

# --- AI transfers ---
POSITION_REQUIREMENTS = [("GK", 1), ("DEF", 4), ("MID", 4), ("FWD", 2)]
TRANSFER_OFFER_DEADLINE_DAYS = 3
TRANSFER_OFFER_PRICE_RANGE = (0.9, 1.2)
REPUTATION_TOLERANCE = 10

# --- Cleanup / garbage collection ---
OLD_DATA_KEEP_PLAYED_MATCHES = 150

# --- Schema migration guard ---
TEMPLATE_SCHEMA_VERSION = 12  # bump to force a template.db rebuild (world-sim: GameState.world_sim_last_week + world_last_summary)

# --- Advanced tactics (FM features) ---
PRESSING_STYLES = ("low", "normal", "high")
PRESSING_FATIGUE_MULT = {"low": 0.8, "normal": 1.0, "high": 1.5}
PRESSING_POSSESSION_BONUS = {"low": -4, "normal": 0, "high": 6}
PRESSING_ATTACK_MULT = {"low": 0.97, "normal": 1.0, "high": 1.04}
PRESSING_DEFENSE_MULT = {"low": 1.03, "normal": 1.0, "high": 0.96}

MATCH_TACTICS = ("balanced", "time_wasting", "attack", "defensive")
TIME_WASTING_LATE_MINUTE = 80
TIME_WASTING_LATE_EVENT_CHANCE = 0.05
LATE_EVENT_CHANCE = 0.10
EVENT_BASE_CHANCE = 0.15

PENALTY_BASE_CHANCE = 0.75
PENALTY_ATTACK_WEIGHT = 0.001
FREEKICK_CHANCE_BOOST = 0.10
SET_PIECE_TAKER_BONUS = 0.05

PLAYER_ROLES = ("standard", "target_man", "poacher", "regista", "ball_winner", "wing_back")
ROLE_SCORER_MULT = {"standard": 1.0, "target_man": 2.0, "poacher": 1.8,
                    "regista": 0.3, "ball_winner": 0.4, "wing_back": 0.8}
ROLE_ASSIST_MULT = {"standard": 1.0, "target_man": 0.5, "poacher": 0.7,
                    "regista": 2.2, "ball_winner": 0.8, "wing_back": 1.7}
ROLE_TACKLE_MULT = {"standard": 1.0, "target_man": 0.6, "poacher": 0.6,
                    "regista": 1.2, "ball_winner": 2.2, "wing_back": 1.6}
ROLE_DEFENSIVE_MULT = {"standard": 1.0, "target_man": 0.9, "poacher": 0.8,
                       "regista": 1.0, "ball_winner": 1.3, "wing_back": 1.1}

PRESET_TYPES = ("standard", "attacking", "defensive")

# --- Pre-defined pitch layouts (/formation spreads empty slots) ----------
# Each entry: "key" (persisted inside formation_data under FORMATION_LAYOUT_KEY),
# Arabic "label" for the dropdown, and 11 slots (1 GK + 10 outfield).
# Coordinates are pitch percentages: x 0..100 (left→right), y 0..100
# (top = opponents' goal, bottom = own goal). The engines never read x/y
# (they use is_starting + roles) — layouts are purely editorial.
FORMATION_LAYOUT_KEY = "__layout__"  # reserved formation_data key, not a player

PRESET_FORMATIONS = [
    # --- الدفاع الثلاثي (3) ---
    {"key": "3-4-3", "label": "٣-٤-٣", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "CB", "x": 28, "y": 72}, {"pos": "CB", "x": 50, "y": 74},
        {"pos": "CB", "x": 72, "y": 72},
        {"pos": "LM", "x": 12, "y": 48}, {"pos": "CM", "x": 38, "y": 48},
        {"pos": "CM", "x": 62, "y": 48}, {"pos": "RM", "x": 88, "y": 48},
        {"pos": "LW", "x": 14, "y": 16}, {"pos": "ST", "x": 50, "y": 13},
        {"pos": "RW", "x": 86, "y": 16},
    ]},
    {"key": "3-4-2-1", "label": "٣-٤-٢-١", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "CB", "x": 28, "y": 72}, {"pos": "CB", "x": 50, "y": 74},
        {"pos": "CB", "x": 72, "y": 72},
        {"pos": "LM", "x": 12, "y": 48}, {"pos": "CM", "x": 38, "y": 48},
        {"pos": "CM", "x": 62, "y": 48}, {"pos": "RM", "x": 88, "y": 48},
        {"pos": "CAM", "x": 36, "y": 30}, {"pos": "CAM", "x": 64, "y": 30},
        {"pos": "ST", "x": 50, "y": 13},
    ]},
    {"key": "3-4-1-2", "label": "٣-٤-١-٢", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "CB", "x": 28, "y": 72}, {"pos": "CB", "x": 50, "y": 74},
        {"pos": "CB", "x": 72, "y": 72},
        {"pos": "LM", "x": 12, "y": 48}, {"pos": "CM", "x": 38, "y": 48},
        {"pos": "CM", "x": 62, "y": 48}, {"pos": "RM", "x": 88, "y": 48},
        {"pos": "CAM", "x": 50, "y": 28},
        {"pos": "ST", "x": 40, "y": 13}, {"pos": "ST", "x": 60, "y": 13},
    ]},
    {"key": "3-5-2", "label": "٣-٥-٢", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "CB", "x": 28, "y": 74}, {"pos": "CB", "x": 50, "y": 76},
        {"pos": "CB", "x": 72, "y": 74},
        {"pos": "LWB", "x": 8, "y": 58}, {"pos": "CDM", "x": 50, "y": 52},
        {"pos": "CM", "x": 34, "y": 40}, {"pos": "CM", "x": 66, "y": 40},
        {"pos": "RWB", "x": 92, "y": 58},
        {"pos": "ST", "x": 40, "y": 14}, {"pos": "ST", "x": 60, "y": 14},
    ]},
    {"key": "3-5-1-1", "label": "٣-٥-١-١", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "CB", "x": 28, "y": 74}, {"pos": "CB", "x": 50, "y": 76},
        {"pos": "CB", "x": 72, "y": 74},
        {"pos": "LWB", "x": 8, "y": 58}, {"pos": "CDM", "x": 50, "y": 52},
        {"pos": "CM", "x": 34, "y": 40}, {"pos": "CM", "x": 66, "y": 40},
        {"pos": "RWB", "x": 92, "y": 58},
        {"pos": "CAM", "x": 50, "y": 26}, {"pos": "ST", "x": 50, "y": 12},
    ]},
    {"key": "3-6-1", "label": "٣-٦-١", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "CB", "x": 28, "y": 74}, {"pos": "CB", "x": 50, "y": 76},
        {"pos": "CB", "x": 72, "y": 74},
        {"pos": "LWB", "x": 8, "y": 56}, {"pos": "CM", "x": 30, "y": 46},
        {"pos": "CDM", "x": 50, "y": 54}, {"pos": "CM", "x": 70, "y": 46},
        {"pos": "RWB", "x": 92, "y": 56}, {"pos": "CAM", "x": 50, "y": 30},
        {"pos": "ST", "x": 50, "y": 12},
    ]},
    {"key": "3-3-3-1", "label": "٣-٣-٣-١", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "CB", "x": 30, "y": 72}, {"pos": "CB", "x": 50, "y": 74},
        {"pos": "CB", "x": 70, "y": 72},
        {"pos": "CDM", "x": 50, "y": 56}, {"pos": "CM", "x": 32, "y": 42},
        {"pos": "CM", "x": 68, "y": 42},
        {"pos": "LM", "x": 13, "y": 24}, {"pos": "CAM", "x": 50, "y": 22},
        {"pos": "RM", "x": 87, "y": 24}, {"pos": "ST", "x": 50, "y": 10},
    ]},
    {"key": "3-1-4-2", "label": "٣-١-٤-٢", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "CB", "x": 28, "y": 74}, {"pos": "CB", "x": 50, "y": 76},
        {"pos": "CB", "x": 72, "y": 74},
        {"pos": "CDM", "x": 50, "y": 58},
        {"pos": "LM", "x": 14, "y": 40}, {"pos": "CM", "x": 38, "y": 42},
        {"pos": "CM", "x": 62, "y": 42}, {"pos": "RM", "x": 86, "y": 40},
        {"pos": "ST", "x": 40, "y": 15}, {"pos": "ST", "x": 60, "y": 15},
    ]},
    # --- الدفاع الرباعي (4) ---
    {"key": "4-4-2", "label": "٤-٤-٢ كلاسيكية", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "LB", "x": 15, "y": 70}, {"pos": "CB", "x": 36, "y": 72},
        {"pos": "CB", "x": 64, "y": 72}, {"pos": "RB", "x": 85, "y": 70},
        {"pos": "LM", "x": 13, "y": 47}, {"pos": "CM", "x": 38, "y": 47},
        {"pos": "CM", "x": 62, "y": 47}, {"pos": "RM", "x": 87, "y": 47},
        {"pos": "ST", "x": 40, "y": 15}, {"pos": "ST", "x": 60, "y": 15},
    ]},
    {"key": "4-4-2-diamond", "label": "٤-٤-٢ (الماس)", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "LB", "x": 15, "y": 70}, {"pos": "CB", "x": 36, "y": 72},
        {"pos": "CB", "x": 64, "y": 72}, {"pos": "RB", "x": 85, "y": 70},
        {"pos": "CDM", "x": 50, "y": 60}, {"pos": "CM", "x": 32, "y": 44},
        {"pos": "CM", "x": 68, "y": 44}, {"pos": "CAM", "x": 50, "y": 28},
        {"pos": "ST", "x": 40, "y": 14}, {"pos": "ST", "x": 60, "y": 14},
    ]},
    {"key": "4-4-1-1", "label": "٤-٤-١-١", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "LB", "x": 15, "y": 70}, {"pos": "CB", "x": 36, "y": 72},
        {"pos": "CB", "x": 64, "y": 72}, {"pos": "RB", "x": 85, "y": 70},
        {"pos": "LM", "x": 13, "y": 48}, {"pos": "CM", "x": 38, "y": 48},
        {"pos": "CM", "x": 62, "y": 48}, {"pos": "RM", "x": 87, "y": 48},
        {"pos": "CAM", "x": 50, "y": 30}, {"pos": "ST", "x": 50, "y": 13},
    ]},
    {"key": "4-1-3-2", "label": "٤-١-٣-٢", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "LB", "x": 15, "y": 70}, {"pos": "CB", "x": 36, "y": 72},
        {"pos": "CB", "x": 64, "y": 72}, {"pos": "RB", "x": 85, "y": 70},
        {"pos": "CDM", "x": 50, "y": 58},
        {"pos": "LM", "x": 13, "y": 36}, {"pos": "CAM", "x": 50, "y": 34},
        {"pos": "RM", "x": 87, "y": 36},
        {"pos": "ST", "x": 40, "y": 14}, {"pos": "ST", "x": 60, "y": 14},
    ]},
    {"key": "4-2-3-1", "label": "٤-٢-٣-١", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "LB", "x": 15, "y": 70}, {"pos": "CB", "x": 36, "y": 72},
        {"pos": "CB", "x": 64, "y": 72}, {"pos": "RB", "x": 85, "y": 70},
        {"pos": "CDM", "x": 36, "y": 58}, {"pos": "CDM", "x": 64, "y": 58},
        {"pos": "LM", "x": 14, "y": 32}, {"pos": "CAM", "x": 50, "y": 30},
        {"pos": "RM", "x": 86, "y": 32}, {"pos": "ST", "x": 50, "y": 13},
    ]},
    {"key": "4-2-3-1-wide", "label": "٤-٢-٣-١ (أجنحة عريضة)", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "LB", "x": 15, "y": 70}, {"pos": "CB", "x": 36, "y": 72},
        {"pos": "CB", "x": 64, "y": 72}, {"pos": "RB", "x": 85, "y": 70},
        {"pos": "CDM", "x": 36, "y": 58}, {"pos": "CDM", "x": 64, "y": 58},
        {"pos": "LW", "x": 10, "y": 30}, {"pos": "CAM", "x": 50, "y": 32},
        {"pos": "RW", "x": 90, "y": 30}, {"pos": "ST", "x": 50, "y": 13},
    ]},
    {"key": "4-3-3", "label": "٤-٣-٣ متوازنة", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "LB", "x": 15, "y": 70}, {"pos": "CB", "x": 36, "y": 72},
        {"pos": "CB", "x": 64, "y": 72}, {"pos": "RB", "x": 85, "y": 70},
        {"pos": "CM", "x": 30, "y": 46}, {"pos": "CM", "x": 50, "y": 44},
        {"pos": "CM", "x": 70, "y": 46},
        {"pos": "LW", "x": 12, "y": 18}, {"pos": "ST", "x": 50, "y": 14},
        {"pos": "RW", "x": 88, "y": 18},
    ]},
    {"key": "4-3-3-holding", "label": "٤-٣-٣ (ضابط اللعب)", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "LB", "x": 15, "y": 70}, {"pos": "CB", "x": 36, "y": 72},
        {"pos": "CB", "x": 64, "y": 72}, {"pos": "RB", "x": 85, "y": 70},
        {"pos": "CDM", "x": 50, "y": 54}, {"pos": "CM", "x": 30, "y": 44},
        {"pos": "CM", "x": 70, "y": 44},
        {"pos": "LW", "x": 12, "y": 18}, {"pos": "ST", "x": 50, "y": 14},
        {"pos": "RW", "x": 88, "y": 18},
    ]},
    {"key": "4-3-3-attack", "label": "٤-٣-٣ هجومية", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "LB", "x": 15, "y": 70}, {"pos": "CB", "x": 36, "y": 72},
        {"pos": "CB", "x": 64, "y": 72}, {"pos": "RB", "x": 85, "y": 70},
        {"pos": "CM", "x": 30, "y": 46}, {"pos": "CM", "x": 70, "y": 46},
        {"pos": "CAM", "x": 50, "y": 36},
        {"pos": "LW", "x": 12, "y": 18}, {"pos": "ST", "x": 50, "y": 14},
        {"pos": "RW", "x": 88, "y": 18},
    ]},
    {"key": "4-1-4-1", "label": "٤-١-٤-١", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "LB", "x": 15, "y": 70}, {"pos": "CB", "x": 36, "y": 72},
        {"pos": "CB", "x": 64, "y": 72}, {"pos": "RB", "x": 85, "y": 70},
        {"pos": "CDM", "x": 50, "y": 58},
        {"pos": "LM", "x": 13, "y": 40}, {"pos": "CM", "x": 38, "y": 42},
        {"pos": "CM", "x": 62, "y": 42}, {"pos": "RM", "x": 87, "y": 40},
        {"pos": "ST", "x": 50, "y": 14},
    ]},
    {"key": "4-5-1", "label": "٤-٥-١ أوسع", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "LB", "x": 15, "y": 70}, {"pos": "CB", "x": 36, "y": 72},
        {"pos": "CB", "x": 64, "y": 72}, {"pos": "RB", "x": 85, "y": 70},
        {"pos": "LM", "x": 13, "y": 47}, {"pos": "CM", "x": 32, "y": 47},
        {"pos": "CM", "x": 50, "y": 45}, {"pos": "CM", "x": 68, "y": 47},
        {"pos": "RM", "x": 87, "y": 47}, {"pos": "ST", "x": 50, "y": 14},
    ]},
    {"key": "4-2-2-2", "label": "٤-٢-٢-٢ (صندوق)", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "LB", "x": 15, "y": 70}, {"pos": "CB", "x": 36, "y": 72},
        {"pos": "CB", "x": 64, "y": 72}, {"pos": "RB", "x": 85, "y": 70},
        {"pos": "CDM", "x": 36, "y": 58}, {"pos": "CDM", "x": 64, "y": 58},
        {"pos": "CAM", "x": 36, "y": 36}, {"pos": "CAM", "x": 64, "y": 36},
        {"pos": "ST", "x": 40, "y": 15}, {"pos": "ST", "x": 60, "y": 15},
    ]},
    {"key": "4-3-1-2", "label": "٤-٣-١-٢", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "LB", "x": 15, "y": 70}, {"pos": "CB", "x": 36, "y": 72},
        {"pos": "CB", "x": 64, "y": 72}, {"pos": "RB", "x": 85, "y": 70},
        {"pos": "CM", "x": 28, "y": 46}, {"pos": "CM", "x": 50, "y": 50},
        {"pos": "CM", "x": 72, "y": 46}, {"pos": "CAM", "x": 50, "y": 30},
        {"pos": "ST", "x": 40, "y": 14}, {"pos": "ST", "x": 60, "y": 14},
    ]},
    {"key": "4-3-2-1", "label": "٤-٣-٢-١ (شجرة عيد الميلاد)", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "LB", "x": 15, "y": 70}, {"pos": "CB", "x": 36, "y": 72},
        {"pos": "CB", "x": 64, "y": 72}, {"pos": "RB", "x": 85, "y": 70},
        {"pos": "CM", "x": 30, "y": 50}, {"pos": "CM", "x": 50, "y": 54},
        {"pos": "CM", "x": 70, "y": 50}, {"pos": "CAM", "x": 36, "y": 32},
        {"pos": "CAM", "x": 64, "y": 32}, {"pos": "ST", "x": 50, "y": 13},
    ]},
    {"key": "4-2-4", "label": "٤-٢-٤", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "LB", "x": 15, "y": 70}, {"pos": "CB", "x": 36, "y": 72},
        {"pos": "CB", "x": 64, "y": 72}, {"pos": "RB", "x": 85, "y": 70},
        {"pos": "CDM", "x": 36, "y": 58}, {"pos": "CDM", "x": 64, "y": 58},
        {"pos": "LW", "x": 12, "y": 16}, {"pos": "ST", "x": 38, "y": 14},
        {"pos": "ST", "x": 62, "y": 14}, {"pos": "RW", "x": 88, "y": 16},
    ]},
    {"key": "4-6-0", "label": "٤-٦-٠ (رقم تسع كاذب)", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "LB", "x": 15, "y": 70}, {"pos": "CB", "x": 36, "y": 72},
        {"pos": "CB", "x": 64, "y": 72}, {"pos": "RB", "x": 85, "y": 70},
        {"pos": "CDM", "x": 36, "y": 60}, {"pos": "CDM", "x": 64, "y": 60},
        {"pos": "LM", "x": 12, "y": 38}, {"pos": "CM", "x": 36, "y": 42},
        {"pos": "CAM", "x": 50, "y": 30}, {"pos": "RM", "x": 88, "y": 38},
    ]},
    # --- الدفاع الخماسي ---
    {"key": "5-3-2", "label": "٥-٣-٢", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "LWB", "x": 6, "y": 60}, {"pos": "CB", "x": 28, "y": 76},
        {"pos": "CB", "x": 50, "y": 78}, {"pos": "CB", "x": 72, "y": 76},
        {"pos": "RWB", "x": 94, "y": 60},
        {"pos": "CM", "x": 30, "y": 46}, {"pos": "CM", "x": 50, "y": 42},
        {"pos": "CM", "x": 70, "y": 46},
        {"pos": "ST", "x": 38, "y": 14}, {"pos": "ST", "x": 62, "y": 14},
    ]},
    {"key": "5-4-1", "label": "٥-٤-١", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "LWB", "x": 6, "y": 60}, {"pos": "CB", "x": 28, "y": 76},
        {"pos": "CB", "x": 50, "y": 78}, {"pos": "CB", "x": 72, "y": 76},
        {"pos": "RWB", "x": 94, "y": 60},
        {"pos": "LM", "x": 14, "y": 44}, {"pos": "CM", "x": 38, "y": 46},
        {"pos": "CM", "x": 62, "y": 46}, {"pos": "RM", "x": 86, "y": 44},
        {"pos": "ST", "x": 50, "y": 14},
    ]},
    {"key": "5-2-1-2", "label": "٥-٢-١-٢", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "LWB", "x": 6, "y": 60}, {"pos": "CB", "x": 28, "y": 76},
        {"pos": "CB", "x": 50, "y": 78}, {"pos": "CB", "x": 72, "y": 76},
        {"pos": "RWB", "x": 94, "y": 60},
        {"pos": "CM", "x": 36, "y": 50}, {"pos": "CM", "x": 64, "y": 50},
        {"pos": "CAM", "x": 50, "y": 28},
        {"pos": "ST", "x": 40, "y": 13}, {"pos": "ST", "x": 60, "y": 13},
    ]},
    {"key": "5-2-3", "label": "٥-٢-٣", "slots": [
        {"pos": "GK", "x": 50, "y": 88},
        {"pos": "LWB", "x": 6, "y": 60}, {"pos": "CB", "x": 28, "y": 76},
        {"pos": "CB", "x": 50, "y": 78}, {"pos": "CB", "x": 72, "y": 76},
        {"pos": "RWB", "x": 94, "y": 60},
        {"pos": "CM", "x": 36, "y": 52}, {"pos": "CM", "x": 64, "y": 52},
        {"pos": "LW", "x": 14, "y": 18}, {"pos": "ST", "x": 50, "y": 14},
        {"pos": "RW", "x": 86, "y": 18},
    ]},
]

# --- "Fill by ability" (combining preset slots with the best squad) ------
ABILITY_FILL_EXACT_WEIGHT = 1.0   # exact detailed-position match (e.g. ST in a ST slot)
ABILITY_FILL_GROUP_WEIGHT = 0.7   # same positional band (e.g. CM in a CAM slot)
ABILITY_FILL_ANY_WEIGHT = 0.2     # out of position (never GK ↔ outfield)
ABILITY_FILL_FITNESS_WEIGHT = 0.6  # ability scales by match readiness
FORMATION_SLOT_DROP_RADIUS = 15   # pitch-% max distance for snapping a drop to a slot

TACTICAL_FAMILIARITY_START = 100
TACTICAL_FAMILIARITY_LOSS = 20
TACTICAL_FAMILIARITY_GAIN = 10
TACTICAL_FAMILIARITY_PENALTY_START = 50
FAMILIARITY_MAX_PENALTY = 0.15

# --- Player traits / RPG ---
TRAITS_POOL = [
    "long_shots", "leadership", "big_game_player", "hard_worker", "team_player",
    "set_piece_specialist", "injury_prone", "temperamental", "tries_through_balls",
    "gets_forward",
]
TRAITS_PER_PLAYER_MIN = 1
TRAITS_PER_PLAYER_MAX = 3
TRAIT_LONG_SHOTS_MULT = 1.3
TRAIT_SET_PIECE_BOOST = 0.05
TRAIT_BIG_GAME_MULT = 1.05
TRAIT_HARD_WORKER_DECAY = 0.85
TRAIT_INJURY_PRONE_MULT = 1.6
TRAIT_TEMPERAMENTAL_CARD_MULT = 1.5
TRAIT_TEAM_PLAYER_CHEMISTRY_BONUS = 3
TRAIT_GETS_FORWARD_MULT = 1.2

# --- Match sharpness ---
SHARPNESS_START = 50
SHARPNESS_GAIN_FULL = 10
SHARPNESS_GAIN_PARTIAL = 3
SHARPNESS_DECAY_IDLE = 5
SHARPNESS_MATCH_WEIGHT = 0.2

# --- Player morale ---
PLAYER_MORALE_START = 50

# --- Aging curve ---
PHYSICAL_DECLINE_AGE = 30
TECHNICAL_DECLINE_AGE = 33
PHYSICAL_DECLINE_MAX = 3
TECHNICAL_DECLINE_MAX = 2
AGING_TECHNICAL_PLATEAU_BOOST = 1

# --- Mentoring ---
MENTORING_PERSONALITY_CHANCE = 0.10
MENTORING_TRAIT_CHANCE = 0.12
MENTORING_MAX_AGE_MENTEE = 23

# --- Training complaints ---
TRAINING_COMPLAINT_INTENSITY = 8
TRAINING_COMPLAINT_MORALE_LOSS = 10

# --- Free agents ---
FREE_AGENT_SIGN_FEE_RATIO = 0.10
FREE_AGENT_WEEKLY_CHANCE = 0.35
FREE_AGENT_MAX = 40

# --- Sell-on clause ---
SELL_ON_CLAUSE_RANGE = (0.05, 0.25)
SELL_ON_CLAUSE_DISCOUNT_RATIO = 0.5  # offer price is halved when selling with clause

# --- Board intervention ---
BOARD_INTERVENTION_MULTIPLE = 2.5
BOARD_INTERVENTION_MIN_VALUE = 1_000_000

# --- Rivalries ---
RIVALS = {
    "Arsenal": "Tottenham Hotspur",
    "Tottenham Hotspur": "Arsenal",
    "Manchester United": "Manchester City",
    "Manchester City": "Manchester United",
    "Liverpool": "Everton",
    "Everton": "Liverpool",
    "Chelsea": "Arsenal",
    "Arsenal2": "Chelsea",
}

# --- Scouting ---
SCOUT_REGIONS = ("Europe", "South_America", "Middle_East", "Africa", "Asia")
SCOUT_KNOWLEDGE_GAIN = 8
SCOUT_KNOWLEDGE_THRESHOLD = 50
MASK_RATING_OFFSET = 10
SCOUT_WONDERKID_CHANCE_BASE = 0.10
SCOUT_ASSIGNMENT_SALARY_PRICE = 20_000

# --- Board objectives / confidence ---
BOARD_CONFIDENCE_START = 60
BOARD_OBJECTIVES_MAX = 3
BOARD_CONFIDENCE_PENALTY = 10
BOARD_CONFIDENCE_REWARD = 10
BOARD_OBJECTIVE_TIERS = {
    "avg_age": {"target": 26.0, "direction": "low"},
    "youth_goals": {"target": 8, "direction": "high"},
    "league_position": {"target": 4, "direction": "high"},
}

# --- Chemistry ---
CHEMISTRY_BASE = 50
CHEMISTRY_SAME_REGION_THRESHOLD = 3
CHEMISTRY_SAME_REGION_BONUS = 2

# --- Captain intervention ---
CAPTAIN_MORALE_THRESHOLD = 30
CAPTAIN_MORALE_BOOST = 10

# --- Live match engine (new; old engine kept for later) ---
LIVE_HALF_MINUTES = 45
LIVE_MATCH_MINUTES = 90
LIVE_MINUTE_STEP = 1
LIVE_AUTO_EVENT_TICK = 90  # auto mode advances straight to full time

XG_MIN = 0.01
XG_MAX = 0.90

# --- Logging ---
LOG_FILE = "game_errors.log"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# --- Transfer windows (phase 1: structure) ---
# Summer: June..August, Winter: January. Deadline = last 3 days of window.
TRANSFER_WINDOW_SUMMER_MONTHS = (6, 7, 8)
TRANSFER_WINDOW_WINTER_MONTHS = (1,)
TRANSFER_DEADLINE_DAYS = 3  # last N days of a window are "deadline day"
TRANSFER_DEADLINE_SURGE = 2.0  # AI offer frequency multiplier on deadline
TRANSFER_DEADLINE_EXPIRY_DAYS = 1  # offers made on deadline expire fast

# --- Contracts (phase 2: the heart of every deal) ---
CONTRACT_MIN_YEARS = 1
CONTRACT_MAX_YEARS = 5
CONTRACT_DAYS_PER_YEAR = 365
# Yearly wage band as a fraction of market value (negotiation room).
WAGE_YEARLY_MIN_RATIO = 0.05
WAGE_YEARLY_MAX_RATIO = 0.20
SIGNING_BONUS_MAX_RATIO = 0.15  # of transfer fee / market value
RELEASE_CLAUSE_MIN_MULT = 1.2  # clause must be >= 1.2x market value
RELEASE_CLAUSE_DEFAULT_MULT = 2.0
PRECONTRACT_MONTHS = 6  # Bosman: last 6 months of a contract
PRECONTRACT_DAYS = 180

# --- Deal clauses (phase 3: FIFA-style terms) ---
INSTALLMENTS_MAX = 4  # "50% now, 50% next season"
INSTALLMENT_INTERVAL_DAYS = 180
BUY_BACK_MAX_RATIO = 0.10  # buy-back price capped at 10% above sale price... (absolute cap below)
BUY_BACK_PRICE_CAP_MULT = 3.0  # buy-back price <= 3x sale price
FIRST_REFUSAL_NOTICE_DAYS = 3
PERF_BONUS_GOAL_TARGET = 20
PERF_BONUS_MAX_RATIO = 0.25  # of transfer fee
SELL_ON_CLAUSE_MAX = 0.30

# --- Live negotiation (phase 4) ---
AGENT_FEE_RATIO = 0.05  # agent commission on top of the fee
AGENT_FEE_STAR_RATIO = 0.10  # star players (value >= threshold) cost more
AGENT_FEE_STAR_THRESHOLD = 5_000_000
OFFER_MAX_ROUNDS = 3  # multi-round haggling before talks collapse
COUNTER_OFFER_STEP = 0.10  # AI counters ~10% toward its valuation
# Dynamic valuation weights (form vs age vs base).
VALUATION_GOAL_WEIGHT = 5_000
VALUATION_ASSIST_WEIGHT = 3_000
VALUATION_YOUTH_BONUS = 1.2  # age <= 23
VALUATION_VETERAN_MALUS = 0.7  # age >= 32
VALUATION_MORALE_BONUS_STEP = 2_000  # per 20 morale above 60

# --- Loans & rival competition (phase 5) ---
LOAN_FEE_RATIO = 0.05  # of market value for the loan spell
LOAN_MAX_DAYS = 365
LOAN_MIN_DAYS = 90
RIVAL_BID_CHANCE = 0.15  # a bigger club hijacks a deal you bid on
RIVAL_REPUTATION_GAP = 10  # rival must be this much more reputable
PLAYER_PREF_REPUTATION_GAP = 15  # player snubs you for a much bigger club

# --- Financial fair play ---
FFP_BASE_ALLOWANCE = 5_000_000  # spending room above net income
FFP_SPEND_RATIO = 1.0  # allowed spend = income + base allowance

# --- Transfer list / requests ---
TRANSFER_LIST_DISCOUNT = 0.9  # listed players go ~10% cheaper
TRANSFER_REQUEST_MORALE_LOSS = 15
TRANSFER_REQUEST_AI_INTEREST_MULT = 1.5

# --- European domestic pyramids (single source of truth) ---
# (country, tier) -> (rating_floor, rating_ceiling)
# floor = worst player primary ability in this league
# ceiling = best player primary ability in this league
LEAGUE_RATING_BANDS = {
    # England
    ("England", 1): (60, 88),
    ("England", 2): (54, 78),
    ("England", 3): (50, 68),
    ("England", 4): (47, 62),
    # Germany
    ("Germany", 1): (60, 87),
    ("Germany", 2): (54, 77),
    ("Germany", 3): (50, 67),
    # Spain
    ("Spain", 1): (61, 89),
    ("Spain", 2): (54, 78),
    # France
    ("France", 1): (59, 87),
    ("France", 2): (53, 76),
    # Italy
    ("Italy", 1): (60, 87),
    ("Italy", 2): (54, 77),
    ("Italy", 3): (48, 64),
    # Netherlands
    ("Netherlands", 1): (57, 82),
    ("Netherlands", 2): (50, 68),
    # Portugal
    ("Portugal", 1): (57, 83),
    ("Portugal", 2): (50, 68),
    # Belgium
    ("Belgium", 1): (56, 80),
    ("Belgium", 2): (49, 66),
    # Turkey
    ("Turkey", 1): (56, 81),
    ("Turkey", 2): (49, 66),
    # Scotland
    ("Scotland", 1): (54, 78),
    ("Scotland", 2): (47, 64),
    # Others (top divisions)
    ("Austria", 1): (55, 79),
    ("Switzerland", 1): (55, 79),
    ("Greece", 1): (54, 78),
    ("Ukraine", 1): (54, 79),
    ("Russia", 1): (55, 80),
    ("Denmark", 1): (54, 78),
    ("Sweden", 1): (51, 74),
    ("Norway", 1): (51, 75),
    ("Poland", 1): (51, 74),
    ("Croatia", 1): (52, 77),
    ("Serbia", 1): (51, 75),
    ("Czech Republic", 1): (51, 75),
    ("Romania", 1): (49, 73),
}

# (country, tier) -> base reputation for clubs without an explicit entry
# in CLUB_REPUTATIONS. Big-5 top flights keep their historic values.
LEAGUE_REPUTATIONS = {
    ("England", 1): 90,
    ("England", 2): 70,
    ("England", 3): 50,
    ("England", 4): 35,
    ("Germany", 1): 84,
    ("Germany", 2): 66,
    ("Germany", 3): 46,
    ("Spain", 1): 88,
    ("Spain", 2): 68,
    ("France", 1): 82,
    ("France", 2): 62,
    ("Italy", 1): 84,
    ("Italy", 2): 66,
    ("Italy", 3): 44,
    ("Netherlands", 1): 72,
    ("Netherlands", 2): 48,
    ("Portugal", 1): 73,
    ("Portugal", 2): 48,
    ("Belgium", 1): 68,
    ("Belgium", 2): 44,
    ("Turkey", 1): 68,
    ("Turkey", 2): 44,
    ("Scotland", 1): 64,
    ("Scotland", 2): 40,
    ("Austria", 1): 64,
    ("Switzerland", 1): 63,
    ("Greece", 1): 63,
    ("Ukraine", 1): 64,
    ("Russia", 1): 66,
    ("Denmark", 1): 62,
    ("Sweden", 1): 56,
    ("Norway", 1): 56,
    ("Poland", 1): 56,
    ("Croatia", 1): 60,
    ("Serbia", 1): 57,
    ("Czech Republic", 1): 57,
    ("Romania", 1): 53,
}

# League prestige multiplier applied on top of the reputation-based budget.
# Premier League gets the biggest TV money; lower divisions get less.
LEAGUE_BUDGET_MULTIPLIER = {
    # England
    ("England", 1): 1.6,
    ("England", 2): 0.8,
    ("England", 3): 0.4,
    ("England", 4): 0.2,
    # Germany
    ("Germany", 1): 1.3,
    ("Germany", 2): 0.6,
    ("Germany", 3): 0.25,
    # Spain
    ("Spain", 1): 1.4,
    ("Spain", 2): 0.6,
    # France
    ("France", 1): 1.2,
    ("France", 2): 0.5,
    # Italy
    ("Italy", 1): 1.2,
    ("Italy", 2): 0.6,
    ("Italy", 3): 0.25,
    # Netherlands
    ("Netherlands", 1): 0.7,
    ("Netherlands", 2): 0.3,
    # Portugal
    ("Portugal", 1): 0.7,
    ("Portugal", 2): 0.3,
    # Belgium
    ("Belgium", 1): 0.6,
    ("Belgium", 2): 0.25,
    # Turkey
    ("Turkey", 1): 0.6,
    ("Turkey", 2): 0.25,
    # Scotland
    ("Scotland", 1): 0.5,
    ("Scotland", 2): 0.2,
    # Others (top divisions)
    ("Austria", 1): 0.5,
    ("Switzerland", 1): 0.5,
    ("Greece", 1): 0.5,
    ("Ukraine", 1): 0.5,
    ("Russia", 1): 0.7,
    ("Denmark", 1): 0.5,
    ("Sweden", 1): 0.4,
    ("Norway", 1): 0.4,
    ("Poland", 1): 0.4,
    ("Croatia", 1): 0.4,
    ("Serbia", 1): 0.4,
    ("Czech Republic", 1): 0.4,
    ("Romania", 1): 0.35,
}

# Per-club reputation table. Clubs missing here fall back to
# LEAGUE_REPUTATIONS[(country, tier)] + random.randint(-8, 8).
CLUB_REPUTATIONS = {
    # Spain
    "Real Madrid": 95, "Barcelona": 94, "Atlético Madrid": 88,
    "Sevilla": 80, "Real Sociedad": 78, "Athletic Bilbao": 77,
    "Villarreal": 76, "Real Betis": 74, "Valencia": 73,
    "Getafe": 65, "Almería": 58, "Cádiz": 58,
    # England
    "Manchester City": 95, "Arsenal": 93, "Liverpool": 93,
    "Manchester United": 91, "Chelsea": 90, "Tottenham Hotspur": 88,
    "Newcastle United": 85, "Aston Villa": 84, "West Ham United": 80,
    "Everton": 74, "Wolverhampton Wanderers": 74,
    # Germany
    "Bayern München": 94, "Borussia Dortmund": 86, "RB Leipzig": 84,
    "Bayer Leverkusen": 83, "Eintracht Frankfurt": 78,
    "VfB Stuttgart": 77, "Borussia Mönchengladbach": 74,
    # France
    "Paris Saint-Germain": 92, "Marseille": 80, "Lyon": 78,
    "Monaco": 78, "Lille": 76, "Nice": 74,
    # Italy
    "Inter": 90, "AC Milan": 88, "Juventus": 88, "Napoli": 85,
    "Roma": 83, "Lazio": 80, "Atalanta": 80, "Fiorentina": 77,
    # Netherlands
    "Ajax": 80, "PSV": 80, "Feyenoord": 78, "AZ Alkmaar": 72,
    # Portugal
    "Benfica": 80, "Porto": 80, "Sporting CP": 79, "Braga": 72,
    # Turkey
    "Galatasaray": 76, "Fenerbahçe": 76, "Beşiktaş": 73,
    # Others
    "Celtic": 74, "Rangers": 72,
    "Shakhtar Donetsk": 74, "Dynamo Kyiv": 72,
    "Club Brugge": 73, "Anderlecht": 70,
    "Red Bull Salzburg": 73, "Young Boys": 70,
    "Olympiacos": 72, "Panathinaikos": 70,
    "Red Star Belgrade": 70,
    "Copenhagen": 70, "Zenit": 74, "Spartak Moscow": 70,
}

# Seeded world-class players (procedural generation covers everyone else).
# Each entry overrides one generated squad member at the named club.
# Famous squad members to pin on specific clubs after world generation.
# "detailed" is the player's signature detailed position — star swaps are
# like-for-like (Kane takes an ST slot, Hakimi the RB) so the backline's
# LB/CB/RB coverage is never disturbed.
SEED_STAR_PLAYERS = [
    {"name": "Kylian Mbappé", "position": "FWD", "detailed": "ST", "attack": 91, "defense": 45,
     "potential": 93, "age": 25, "region": "Europe", "club": "Real Madrid"},
    {"name": "Erling Haaland", "position": "FWD", "detailed": "ST", "attack": 90, "defense": 42,
     "potential": 92, "age": 24, "region": "Europe", "club": "Manchester City"},
    {"name": "Jude Bellingham", "position": "MID", "detailed": "CM", "attack": 88, "defense": 74,
     "potential": 92, "age": 21, "region": "Europe", "club": "Real Madrid"},
    {"name": "Vinícius Júnior", "position": "FWD", "detailed": "LW", "attack": 89, "defense": 40,
     "potential": 91, "age": 24, "region": "South_America", "club": "Real Madrid"},
    {"name": "Lamine Yamal", "position": "FWD", "detailed": "RW", "attack": 88, "defense": 38,
     "potential": 95, "age": 17, "region": "Europe", "club": "Barcelona"},
    {"name": "Rodri", "position": "MID", "detailed": "CDM", "attack": 82, "defense": 88,
     "potential": 89, "age": 28, "region": "Europe", "club": "Manchester City"},
    {"name": "Mohamed Salah", "position": "FWD", "detailed": "RW", "attack": 89, "defense": 45,
     "potential": 90, "age": 32, "region": "Middle_East", "club": "Liverpool"},
    {"name": "Harry Kane", "position": "FWD", "detailed": "ST", "attack": 89, "defense": 48,
     "potential": 90, "age": 31, "region": "Europe", "club": "Bayern München"},
    {"name": "Kevin De Bruyne", "position": "MID", "detailed": "CAM", "attack": 87, "defense": 60,
     "potential": 88, "age": 33, "region": "Europe", "club": "Manchester City"},
    {"name": "Virgil van Dijk", "position": "DEF", "detailed": "CB", "attack": 55, "defense": 89,
     "potential": 90, "age": 33, "region": "Europe", "club": "Liverpool"},
    {"name": "Robert Lewandowski", "position": "FWD", "detailed": "ST", "attack": 88, "defense": 44,
     "potential": 89, "age": 36, "region": "Europe", "club": "Barcelona"},
    {"name": "Jamal Musiala", "position": "MID", "detailed": "CAM", "attack": 87, "defense": 55,
     "potential": 93, "age": 21, "region": "Europe", "club": "Bayern München"},
    {"name": "Florian Wirtz", "position": "MID", "detailed": "CAM", "attack": 86, "defense": 52,
     "potential": 92, "age": 21, "region": "Europe", "club": "Bayer Leverkusen"},
    {"name": "Bukayo Saka", "position": "FWD", "detailed": "RW", "attack": 86, "defense": 50,
     "potential": 90, "age": 23, "region": "Europe", "club": "Arsenal"},
    {"name": "Phil Foden", "position": "MID", "detailed": "CM", "attack": 85, "defense": 52,
     "potential": 89, "age": 24, "region": "Europe", "club": "Manchester City"},
    {"name": "Cole Palmer", "position": "FWD", "detailed": "CF", "attack": 85, "defense": 45,
     "potential": 90, "age": 22, "region": "Europe", "club": "Chelsea"},
    {"name": "Declan Rice", "position": "MID", "detailed": "CDM", "attack": 78, "defense": 85,
     "potential": 88, "age": 25, "region": "Europe", "club": "Arsenal"},
    {"name": "Martin Ødegaard", "position": "MID", "detailed": "CAM", "attack": 85, "defense": 58,
     "potential": 89, "age": 25, "region": "Europe", "club": "Arsenal"},
    {"name": "Antoine Griezmann", "position": "FWD", "detailed": "CF", "attack": 86, "defense": 55,
     "potential": 87, "age": 33, "region": "Europe", "club": "Atlético Madrid"},
    {"name": "Federico Valverde", "position": "MID", "detailed": "CM", "attack": 83, "defense": 78,
     "potential": 89, "age": 26, "region": "South_America", "club": "Real Madrid"},
    {"name": "Thibaut Courtois", "position": "GK", "detailed": "GK", "attack": 40, "defense": 89,
     "potential": 90, "age": 32, "region": "Europe", "club": "Real Madrid"},
    {"name": "Alisson Becker", "position": "GK", "detailed": "GK", "attack": 38, "defense": 88,
     "potential": 89, "age": 32, "region": "South_America", "club": "Liverpool"},
    {"name": "Gianluigi Donnarumma", "position": "GK", "detailed": "GK", "attack": 38, "defense": 87,
     "potential": 90, "age": 25, "region": "Europe", "club": "Paris Saint-Germain"},
    {"name": "Antonio Rüdiger", "position": "DEF", "detailed": "CB", "attack": 50, "defense": 86,
     "potential": 87, "age": 31, "region": "Europe", "club": "Real Madrid"},
    {"name": "William Saliba", "position": "DEF", "detailed": "CB", "attack": 52, "defense": 86,
     "potential": 90, "age": 23, "region": "Europe", "club": "Arsenal"},
    {"name": "Rúben Dias", "position": "DEF", "detailed": "CB", "attack": 50, "defense": 87,
     "potential": 88, "age": 27, "region": "Europe", "club": "Manchester City"},
    {"name": "Achraf Hakimi", "position": "DEF", "detailed": "RB", "attack": 78, "defense": 82,
     "potential": 86, "age": 25, "region": "Middle_East", "club": "Paris Saint-Germain"},
    {"name": "Ousmane Dembélé", "position": "FWD", "detailed": "RW", "attack": 86, "defense": 48,
     "potential": 88, "age": 27, "region": "Europe", "club": "Paris Saint-Germain"},
    {"name": "Lautaro Martínez", "position": "FWD", "detailed": "ST", "attack": 87, "defense": 50,
     "potential": 89, "age": 27, "region": "South_America", "club": "Inter"},
    {"name": "Nicolò Barella", "position": "MID", "detailed": "CM", "attack": 80, "defense": 83,
     "potential": 87, "age": 27, "region": "Europe", "club": "Inter"},
    {"name": "Rafael Leão", "position": "FWD", "detailed": "LW", "attack": 85, "defense": 42,
     "potential": 89, "age": 25, "region": "Europe", "club": "AC Milan"},
    {"name": "Mike Maignan", "position": "GK", "detailed": "GK", "attack": 40, "defense": 86,
     "potential": 88, "age": 29, "region": "Europe", "club": "AC Milan"},
    {"name": "Khvicha Kvaratskhelia", "position": "FWD", "detailed": "LW", "attack": 85, "defense": 50,
     "potential": 89, "age": 23, "region": "Europe", "club": "Napoli"},
    {"name": "Victor Osimhen", "position": "FWD", "detailed": "ST", "attack": 86, "defense": 45,
     "potential": 88, "age": 25, "region": "Middle_East", "club": "Napoli"},
    {"name": "Dušan Vlahović", "position": "FWD", "detailed": "ST", "attack": 84, "defense": 42,
     "potential": 87, "age": 24, "region": "Europe", "club": "Juventus"},
    {"name": "Alex Grimaldo", "position": "DEF", "detailed": "LB", "attack": 76, "defense": 80,
     "potential": 84, "age": 29, "region": "Europe", "club": "Bayer Leverkusen"},
    {"name": "Xavi Simons", "position": "MID", "detailed": "CAM", "attack": 82, "defense": 55,
     "potential": 89, "age": 21, "region": "Europe", "club": "RB Leipzig"},
    {"name": "Serhou Guirassy", "position": "FWD", "detailed": "ST", "attack": 84, "defense": 40,
     "potential": 85, "age": 28, "region": "Europe", "club": "Borussia Dortmund"},
    {"name": "Alexis Mac Allister", "position": "MID", "detailed": "CM", "attack": 80, "defense": 78,
     "potential": 85, "age": 25, "region": "South_America", "club": "Liverpool"},
    {"name": "Ryan Gravenberch", "position": "MID", "detailed": "CDM", "attack": 78, "defense": 80,
     "potential": 87, "age": 22, "region": "Europe", "club": "Liverpool"},
]

# Real club names per domestic pyramid, keyed by (country, tier).
# Each country's pyramid is self-contained (promotion/relegation never
# crosses countries).
EUROPEAN_TEAMS = {
    ("England", 1): [
        "Arsenal", "Aston Villa", "Bournemouth", "Brentford",
        "Brighton & Hove Albion", "Chelsea", "Crystal Palace", "Everton",
        "Fulham", "Ipswich Town", "Leicester City", "Liverpool",
        "Manchester City", "Manchester United", "Newcastle United",
        "Nottingham Forest", "Southampton", "Tottenham Hotspur",
        "West Ham United", "Wolverhampton Wanderers",
    ],
    ("England", 2): [
        "Blackburn Rovers", "Bristol City", "Burnley", "Cardiff City",
        "Coventry City", "Derby County", "Hull City", "Leeds United",
        "Luton Town", "Middlesbrough", "Millwall", "Norwich City",
        "Oxford United", "Plymouth Argyle", "Portsmouth", "Preston North End",
        "Queens Park Rangers", "Sheffield United", "Sheffield Wednesday",
        "Stoke City", "Sunderland", "Swansea City", "Watford",
        "West Bromwich Albion",
    ],
    ("England", 3): [
        "Barnsley", "Birmingham City", "Blackpool", "Bolton Wanderers",
        "Bristol Rovers", "Burton Albion", "Cambridge United",
        "Charlton Athletic", "Crawley Town", "Exeter City",
        "Huddersfield Town", "Leyton Orient", "Lincoln City",
        "Mansfield Town", "Northampton Town", "Peterborough United",
        "Reading", "Rotherham United", "Shrewsbury Town", "Stevenage",
        "Stockport County", "Wigan Athletic", "Wrexham", "Wycombe Wanderers",
    ],
    ("England", 4): [
        "Accrington Stanley", "AFC Wimbledon", "Barrow", "Bradford City",
        "Bromley", "Carlisle United", "Cheltenham Town", "Chesterfield",
        "Colchester United", "Crewe Alexandra", "Doncaster Rovers",
        "Fleetwood Town", "Gillingham", "Grimsby Town", "Harrogate Town",
        "Milton Keynes Dons", "Morecambe", "Newport County", "Notts County",
        "Port Vale", "Salford City", "Swindon Town", "Tranmere Rovers",
        "Walsall",
    ],
    ("Germany", 1): [
        "Bayern München", "Borussia Dortmund", "RB Leipzig",
        "Bayer Leverkusen", "VfB Stuttgart", "Eintracht Frankfurt",
        "Borussia Mönchengladbach", "Wolfsburg", "Freiburg", "Hoffenheim",
        "Mainz 05", "Augsburg", "Union Berlin", "Werder Bremen", "Bochum",
        "Heidenheim", "St. Pauli", "Holstein Kiel",
    ],
    ("Germany", 2): [
        "Schalke 04", "Hamburger SV", "Hertha Berlin", "Kaiserslautern",
        "Nürnberg", "Hannover 96", "Fortuna Düsseldorf", "Karlsruher SC",
        "Greuther Fürth", "Darmstadt 98", "Paderborn", "Elversberg",
        "Magdeburg", "Jahn Regensburg", "Eintracht Braunschweig", "Ulm",
        "Preußen Münster", "Dynamo Dresden",
    ],
    ("Germany", 3): [
        "Arminia Bielefeld", "Hansa Rostock", "Ingolstadt", "1860 München",
        "Waldhof Mannheim", "Rot-Weiss Essen", "Borussia Dortmund II",
        "Verl", "Viktoria Köln", "Saarbrücken", "Alemannia Aachen",
        "Energie Cottbus", "Erzgebirge Aue", "Unterhaching", "Sandhausen",
        "Osnabrück", "Wehen Wiesbaden", "VfB Lübeck", "Hallescher FC",
        "Jahn Regensburg II",
    ],
    ("Spain", 1): [
        "Real Madrid", "Barcelona", "Atlético Madrid", "Sevilla",
        "Real Sociedad", "Athletic Bilbao", "Villarreal", "Real Betis",
        "Valencia", "Getafe", "Almería", "Cádiz", "Girona", "Osasuna",
        "Celta Vigo", "Rayo Vallecano", "Mallorca", "Las Palmas", "Alavés",
        "Granada",
    ],
    ("Spain", 2): [
        "Espanyol", "Real Valladolid", "Eibar", "Real Oviedo",
        "Sporting Gijón", "Racing Santander", "Real Zaragoza", "Tenerife",
        "Levante", "Albacete", "Cartagena", "Burgos", "Mirandés", "Huesca",
        "Eldense", "Andorra", "Leganés", "Elche", "Alcorcón", "Amorebieta",
        "Racing Ferrol", "Málaga",
    ],
    ("France", 1): [
        "Paris Saint-Germain", "Marseille", "Lyon", "Monaco", "Lille",
        "Nice", "Lens", "Rennes", "Nantes", "Montpellier", "Toulouse",
        "Strasbourg", "Reims", "Auxerre", "Angers", "Le Havre",
        "Saint-Étienne", "Brest",
    ],
    ("France", 2): [
        "Bordeaux", "Caen", "Guingamp", "Paris FC", "Amiens", "Bastia",
        "Ajaccio", "Troyes", "Valenciennes", "Quevilly", "Rodez", "Pau",
        "Grenoble", "Annecy", "Laval", "Dunkerque", "Martigues", "Red Star",
        "Clermont", "Metz",
    ],
    ("Italy", 1): [
        "Inter", "AC Milan", "Juventus", "Napoli", "Roma", "Lazio",
        "Atalanta", "Fiorentina", "Torino", "Udinese", "Empoli", "Lecce",
        "Cagliari", "Genoa", "Verona", "Monza", "Bologna", "Sassuolo",
        "Como", "Parma",
    ],
    ("Italy", 2): [
        "Sampdoria", "Palermo", "Bari", "Brescia", "Cremonese", "Venezia",
        "Catanzaro", "Pisa", "Ascoli", "Reggiana", "Modena", "Südtirol",
        "Cosenza", "Ternana", "Spezia", "Cittadella", "Frosinone", "Salernitana",
        "Sassuolo B", "Lecco",
    ],
    ("Italy", 3): [
        "Juventus Next Gen", "Padova", "Triestina", "Vicenza", "Mantova",
        "Cesena", "Perugia", "Pescara", "Catania", "Avellino", "Benevento",
        "Foggia", "Taranto", "Crotone", "Messina", "Rimini", "Pontedera",
        "Arezzo", "Torres", "Carrarese",
    ],
    ("Netherlands", 1): [
        "Ajax", "PSV", "Feyenoord", "AZ Alkmaar", "Twente", "Utrecht",
        "Vitesse", "Heerenveen", "Groningen", "Sparta Rotterdam", "NEC",
        "Go Ahead Eagles", "Zwolle", "Fortuna Sittard", "Heracles",
        "Excelsior", "Volendam", "Almere City",
    ],
    ("Netherlands", 2): [
        "Willem II", "Roda JC", "NAC Breda", "ADO Den Haag", "De Graafschap",
        "VVV-Venlo", "Cambuur", "Emmen", "Telstar", "TOP Oss", "Jong Ajax",
        "Jong PSV", "Jong AZ", "Jong Utrecht", "Dordrecht", "Eindhoven",
        "Helmond Sport", "MVV", "Den Bosch", "RKC Waalwijk",
    ],
    ("Portugal", 1): [
        "Benfica", "Porto", "Sporting CP", "Braga", "Vitória Guimarães",
        "Boavista", "Famalicão", "Rio Ave", "Gil Vicente", "Estoril",
        "Casa Pia", "Chaves", "Farense", "Arouca", "Moreirense",
        "Estrela Amadora", "Portimonense", "Vizela",
    ],
    ("Portugal", 2): [
        "Santa Clara", "Nacional", "Marítimo", "Paços de Ferreira",
        "Académico Viseu", "Leixões", "Mafra", "Torreense", "Feirense",
        "Oliveirense", "Penafiel", "Tondela", "União Leiria", "Belenenses",
        "Alverca", "Felgueiras", "Benfica B", "Porto B",
    ],
    ("Belgium", 1): [
        "Club Brugge", "Anderlecht", "Genk", "Standard Liège", "Gent",
        "Antwerp", "Union Saint-Gilloise", "Cercle Brugge", "Mechelen",
        "Charleroi", "Sint-Truiden", "Kortrijk", "OH Leuven", "Westerlo",
        "Eupen", "RWDM",
    ],
    ("Belgium", 2): [
        "Beerschot", "Dender", "Deinze", "Zulte Waregem", "Lierse", "Lommel",
        "Patro Eisden", "RFC Liège", "Francs Borains", "Seraing",
        "Jong Genk", "Club NXT", "RSCA Futures", "Beveren", "Lokeren-Temse",
        "Olympic Charleroi",
    ],
    ("Turkey", 1): [
        "Galatasaray", "Fenerbahçe", "Beşiktaş", "Trabzonspor",
        "Başakşehir", "Antalyaspor", "Konyaspor", "Kayserispor", "Sivasspor",
        "Ankaragücü", "Rizespor", "Samsunspor", "Kasımpaşa", "Alanyaspor",
        "Hatayspor", "Pendikspor", "Fatih Karagümrük", "İstanbulspor",
        "Adana Demirspor", "Gaziantep",
    ],
    ("Turkey", 2): [
        "Eyüpspor", "Göztepe", "Sakaryaspor", "Bodrum", "Kocaelispor",
        "Bandırmaspor", "Boluspor", "Erzurumspor", "Gençlerbirliği",
        "Altay", "Manisa", "Ümraniyespor", "Tuzlaspor", "Şanlıurfaspor",
        "Adanaspor", "Keçiörengücü", "Çorum", "Iğdır",
    ],
    ("Scotland", 1): [
        "Celtic", "Rangers", "Hearts", "Aberdeen", "Hibernian",
        "St Mirren", "Dundee", "Motherwell", "Kilmarnock", "St Johnstone",
        "Ross County", "Livingston",
    ],
    ("Scotland", 2): [
        "Dundee United", "Raith Rovers", "Partick Thistle", "Ayr United",
        "Morton", "Dunfermline", "Inverness", "Queen's Park",
        "Airdrieonians", "Arbroath",
    ],
    ("Austria", 1): [
        "Red Bull Salzburg", "Sturm Graz", "Rapid Wien", "Austria Wien",
        "LASK", "Wolfsberger AC", "Austria Klagenfurt", "Hartberg",
        "WSG Tirol", "Altach", "Blau-Weiß Linz", "Grazer AK",
    ],
    ("Switzerland", 1): [
        "Young Boys", "Basel", "Servette", "Lugano", "St. Gallen",
        "Luzern", "Zürich", "Grasshopper", "Winterthur", "Lausanne-Sport",
        "Yverdon", "Sion",
    ],
    ("Greece", 1): [
        "Olympiacos", "Panathinaikos", "AEK Athens", "PAOK", "Aris",
        "Asteras Tripolis", "OFI Crete", "Atromitos", "Lamia", "Volos",
        "Panetolikos", "PAS Giannina", "Panserraikos", "Kifisia",
    ],
    ("Ukraine", 1): [
        "Shakhtar Donetsk", "Dynamo Kyiv", "Dnipro-1", "Zorya Luhansk",
        "Vorskla Poltava", "Oleksandriya", "Kryvbas", "Kolos Kovalivka",
        "Rukh Lviv", "Polissya Zhytomyr", "Chornomorets Odesa",
        "Metalist 1925", "Veres Rivne", "Obolon Kyiv", "LNZ Cherkasy",
        "Mynai",
    ],
    ("Russia", 1): [
        "Zenit", "Spartak Moscow", "CSKA Moscow", "Dynamo Moscow",
        "Lokomotiv Moscow", "Krasnodar", "Rubin Kazan", "Akhmat Grozny",
        "Rostov", "Ural", "Krylia Sovetov", "Orenburg", "Fakel Voronezh",
        "Pari NN", "Sochi", "Baltika",
    ],
    ("Denmark", 1): [
        "Copenhagen", "Midtjylland", "Brøndby", "Nordsjælland", "Aarhus",
        "Silkeborg", "Viborg", "Randers", "Odense", "Lyngby", "Vejle",
        "Hvidovre",
    ],
    ("Sweden", 1): [
        "Malmö FF", "AIK", "Djurgården", "Hammarby", "IFK Göteborg",
        "Elfsborg", "Häcken", "Norrköping", "Kalmar", "Sirius",
        "Brommapojkarna", "Halmstad", "Mjällby", "Värnamo", "Västerås",
        "GAIS",
    ],
    ("Norway", 1): [
        "Bodø/Glimt", "Molde", "Rosenborg", "Viking", "Brann",
        "Lillestrøm", "Vålerenga", "Tromsø", "Odd", "Sarpsborg",
        "Haugesund", "Strømsgodset", "Sandefjord", "HamKam", "KFUM Oslo",
        "Fredrikstad",
    ],
    ("Poland", 1): [
        "Legia Warsaw", "Lech Poznań", "Raków", "Górnik Zabrze",
        "Jagiellonia", "Pogoń Szczecin", "Wisła Kraków", "Śląsk Wrocław",
        "Cracovia", "Piast Gliwice", "Zagłębie Lubin", "Stal Mielec",
        "Widzew Łódź", "Radomiak", "Warta Poznań", "Puszcza Niepołomice",
        "Korona Kielce", "Ruch Chorzów",
    ],
    ("Croatia", 1): [
        "Dinamo Zagreb", "Hajduk Split", "Rijeka", "Osijek",
        "Lokomotiva Zagreb", "Gorica", "Slaven Belupo", "Varaždin",
        "Istra 1961", "Rudeš",
    ],
    ("Serbia", 1): [
        "Red Star Belgrade", "Partizan", "TSC Bačka Topola", "Vojvodina",
        "Čukarički", "Radnički Niš", "Napredak", "Spartak Subotica",
        "Mladost Lučani", "Novi Pazar", "Radnik Surdulica", "Voždovac",
        "Javor Ivanjica", "Železničar Pančevo", "IMT Beograd",
        "Jedinstvo Ub",
    ],
    ("Czech Republic", 1): [
        "Slavia Prague", "Sparta Prague", "Viktoria Plzeň", "Baník Ostrava",
        "Slovácko", "Sigma Olomouc", "Mladá Boleslav", "Liberec",
        "Jablonec", "Bohemians 1905", "Hradec Králové", "Teplice",
        "Pardubice", "Zlín", "Karviná", "České Budějovice",
    ],
    ("Romania", 1): [
        "FCSB", "CFR Cluj", "Universitatea Craiova", "Rapid București",
        "Farul Constanța", "Sepsi", "Petrolul Ploiești", "UTA Arad",
        "Hermannstadt", "Oțelul Galați", "Universitatea Cluj",
        "Dinamo București", "Poli Iași", "Voluntari", "Botoșani",
        "Gloria Buzău",
    ],
}

# (league name) -> (country, tier), derived from EUROPEAN_TEAMS.
LEAGUE_NAME_TO_KEY = {
    "Premier League": ("England", 1),
    "Championship": ("England", 2),
    "League One": ("England", 3),
    "League Two": ("England", 4),
    "Bundesliga": ("Germany", 1),
    "2. Bundesliga": ("Germany", 2),
    "3. Liga": ("Germany", 3),
    "La Liga": ("Spain", 1),
    "La Liga 2": ("Spain", 2),
    "Ligue 1": ("France", 1),
    "Ligue 2": ("France", 2),
    "Serie A": ("Italy", 1),
    "Serie B": ("Italy", 2),
    "Serie C": ("Italy", 3),
    "Eredivisie": ("Netherlands", 1),
    "Eerste Divisie": ("Netherlands", 2),
    "Primeira Liga": ("Portugal", 1),
    "Liga Portugal 2": ("Portugal", 2),
    "Belgian Pro League": ("Belgium", 1),
    "Challenger Pro League": ("Belgium", 2),
    "Süper Lig": ("Turkey", 1),
    "1. Lig": ("Turkey", 2),
    "Scottish Premiership": ("Scotland", 1),
    "Scottish Championship": ("Scotland", 2),
    "Austrian Bundesliga": ("Austria", 1),
    "Swiss Super League": ("Switzerland", 1),
    "Super League Greece": ("Greece", 1),
    "Ukrainian Premier League": ("Ukraine", 1),
    "Russian Premier League": ("Russia", 1),
    "Danish Superliga": ("Denmark", 1),
    "Allsvenskan": ("Sweden", 1),
    "Eliteserien": ("Norway", 1),
    "Ekstraklasa": ("Poland", 1),
    "HNL": ("Croatia", 1),
    "Serbian SuperLiga": ("Serbia", 1),
    "Czech First League": ("Czech Republic", 1),
    "Liga I": ("Romania", 1),
}

# --- Cup & continental tournaments (single source of truth) ---
# week == 0 on a Match marks a cup/non-league fixture; the league engine
# (weeks >= 1) never touches those rows.
CUP_MATCH_WEEK = 0

CUP_TYPES = (
    "fa_cup",
    "league_cup",
    "national_cup",
    "bsm",
    "super_cup",
    "champions_league",
    "europa_league",
    "conference_league",
)

CUP_SCOPES = {
    "fa_cup": "domestic",
    "league_cup": "domestic",
    "national_cup": "domestic",
    "bsm": "domestic",
    "super_cup": "domestic",
    "champions_league": "continental",
    "europa_league": "continental",
    "conference_league": "continental",
}

CUP_NAMES = {
    "fa_cup": "كأس الاتحاد",
    "league_cup": "كأس الرابطة",
    "national_cup": "الكأس الوطنية",
    "bsm": "كأس BSM",
    "super_cup": "كأس السوبر",
    "champions_league": "دوري أبطال أوروبا",
    "europa_league": "الدوري الأوروبي",
    "conference_league": "دوري المؤتمر الأوروبي",
}

# Badge colours for the calendar (Blue = league, Red = FA Cup, Gold = UCL).
CUP_BADGE_COLORS = {
    "league": "#3498db",
    "fa_cup": "#e74c3c",
    "league_cup": "#e67e22",
    "national_cup": "#e74c3c",
    "bsm": "#9b59b6",
    "super_cup": "#f1c40f",
    "champions_league": "#f1c40f",
    "europa_league": "#e67e22",
    "conference_league": "#2ecc71",
}

# Continental league-phase size + games per team (Swiss system).
CONTINENTAL_SIZE = 36
UCL_LEAGUE_MATCHES = 8
UEL_LEAGUE_MATCHES = 8
CONFERENCE_LEAGUE_MATCHES = 6
# 1-8 -> R16 directly, 9-24 -> play-offs, 25-36 eliminated.
CONTINENTAL_DIRECT_SPOTS = 8
CONTINENTAL_PLAYOFF_SPOTS = 24

# Days between cup rounds when auto-scheduling a new edition.
CUP_ROUND_SPACING_DAYS = 21
CUP_LEG_SPACING_DAYS = 7
CONTINENTAL_PHASE_SPACING_DAYS = 14

# --- Position-specific overall ratings (single source of truth) ---
# For each detailed position, the weights used to compute the "overall"
# (and the derived attack/defense splits). Weights per position MUST sum
# to 1.0. Attributes not listed are ignored for that position.
POSITION_OVERALL_WEIGHTS = {
    # ------------------------------------------------------------------ GK
    "GK": {
        "gk_diving": 0.16, "gk_handling": 0.16, "gk_reflexes": 0.18,
        "gk_positioning": 0.14, "gk_one_on_ones": 0.10,
        "gk_kicking": 0.06, "gk_communication": 0.06,
        "concentration": 0.06, "composure": 0.04, "reactions": 0.04,
    },
    # ---------------------------------------------------------------- CB
    "CB": {
        "standing_tackle": 0.16, "sliding_tackle": 0.10, "marking": 0.14,
        "positioning": 0.12, "defensive_awareness": 0.12,
        "strength": 0.08, "jumping": 0.07, "interceptions": 0.07,
        "heading": 0.05, "composure": 0.04, "passing": 0.05,
    },
    # ---------------------------------------------------------------- LB
    "LB": {
        "standing_tackle": 0.12, "marking": 0.10, "positioning": 0.10,
        "interceptions": 0.08, "defensive_awareness": 0.08,
        "acceleration": 0.08, "sprint_speed": 0.08, "stamina": 0.08,
        "crossing": 0.08, "passing": 0.06, "dribbling": 0.04,
        "work_rate": 0.05, "composure": 0.05,
    },
    # ---------------------------------------------------------------- RB
    # (mirror of LB — kept explicit so future tuning can diverge)
    "RB": {
        "standing_tackle": 0.12, "marking": 0.10, "positioning": 0.10,
        "interceptions": 0.08, "defensive_awareness": 0.08,
        "acceleration": 0.08, "sprint_speed": 0.08, "stamina": 0.08,
        "crossing": 0.08, "passing": 0.06, "dribbling": 0.04,
        "work_rate": 0.05, "composure": 0.05,
    },
    # ---------------------------------------------------------------- LWB
    "LWB": {
        "stamina": 0.14, "acceleration": 0.10, "sprint_speed": 0.10,
        "crossing": 0.14, "dribbling": 0.09, "passing": 0.08,
        "standing_tackle": 0.09, "marking": 0.06,
        "interceptions": 0.06, "work_rate": 0.08,
        "positioning": 0.06,
    },
    # ---------------------------------------------------------------- RWB
    "RWB": {
        "stamina": 0.14, "acceleration": 0.10, "sprint_speed": 0.10,
        "crossing": 0.14, "dribbling": 0.09, "passing": 0.08,
        "standing_tackle": 0.09, "marking": 0.06,
        "interceptions": 0.06, "work_rate": 0.08,
        "positioning": 0.06,
    },
    # ---------------------------------------------------------------- CDM
    "CDM": {
        "interceptions": 0.16, "standing_tackle": 0.13,
        "positioning": 0.13, "defensive_awareness": 0.12,
        "passing": 0.10, "work_rate": 0.09,
        "strength": 0.06, "ball_control": 0.06,
        "vision": 0.05, "composure": 0.05, "teamwork": 0.05,
    },
    # ---------------------------------------------------------------- CM
    "CM": {
        "passing": 0.16, "vision": 0.13, "ball_control": 0.11,
        "technique": 0.10, "decision_making": 0.08,
        "interceptions": 0.08, "standing_tackle": 0.06,
        "work_rate": 0.08, "stamina": 0.07,
        "composure": 0.05, "teamwork": 0.05, "long_shots": 0.03,
    },
    # ---------------------------------------------------------------- CAM
    "CAM": {
        "passing": 0.15, "vision": 0.16, "ball_control": 0.12,
        "dribbling": 0.12, "technique": 0.10, "flair": 0.08,
        "finishing": 0.08, "composure": 0.06,
        "decision_making": 0.05, "agility": 0.04, "long_shots": 0.04,
    },
    # ---------------------------------------------------------------- LM
    "LM": {
        "crossing": 0.14, "dribbling": 0.12, "passing": 0.11,
        "acceleration": 0.10, "sprint_speed": 0.10, "stamina": 0.10,
        "ball_control": 0.09, "work_rate": 0.07,
        "vision": 0.06, "technique": 0.06, "agility": 0.05,
    },
    # ---------------------------------------------------------------- RM
    "RM": {
        "crossing": 0.14, "dribbling": 0.12, "passing": 0.11,
        "acceleration": 0.10, "sprint_speed": 0.10, "stamina": 0.10,
        "ball_control": 0.09, "work_rate": 0.07,
        "vision": 0.06, "technique": 0.06, "agility": 0.05,
    },
    # ---------------------------------------------------------------- LW
    "LW": {
        "dribbling": 0.16, "acceleration": 0.13, "sprint_speed": 0.12,
        "ball_control": 0.11, "finishing": 0.10, "crossing": 0.08,
        "agility": 0.08, "technique": 0.07,
        "vision": 0.05, "flair": 0.05, "composure": 0.05,
    },
    # ---------------------------------------------------------------- RW
    "RW": {
        "dribbling": 0.16, "acceleration": 0.13, "sprint_speed": 0.12,
        "ball_control": 0.11, "finishing": 0.10, "crossing": 0.08,
        "agility": 0.08, "technique": 0.07,
        "vision": 0.05, "flair": 0.05, "composure": 0.05,
    },
    # ---------------------------------------------------------------- CF
    "CF": {
        "finishing": 0.16, "off_the_ball": 0.12, "ball_control": 0.10,
        "dribbling": 0.09, "composure": 0.09, "acceleration": 0.08,
        "sprint_speed": 0.07, "passing": 0.07, "vision": 0.06,
        "technique": 0.05, "shot_power": 0.06, "first_touch": 0.05,
    },
    # ---------------------------------------------------------------- ST
    "ST": {
        "finishing": 0.20, "off_the_ball": 0.14, "shot_power": 0.11,
        "composure": 0.10, "acceleration": 0.09, "sprint_speed": 0.08,
        "ball_control": 0.07, "first_touch": 0.06,
        "heading": 0.05, "strength": 0.05, "positioning": 0.05,
    },
}

ATTACK_ATTRS = frozenset({
    "finishing", "shot_power", "long_shots", "volleys", "penalties",
    "free_kick_accuracy", "curve", "passing", "vision", "crossing",
    "ball_control", "dribbling", "agility", "balance", "composure",
    "off_the_ball", "first_touch", "acceleration", "sprint_speed",
    "flair", "technique",
})

DEFENSE_ATTRS = frozenset({
    "standing_tackle", "sliding_tackle", "marking", "positioning",
    "interceptions", "defensive_awareness", "strength", "jumping",
    "heading", "aggression", "work_rate", "teamwork", "concentration",
    "reactions", "stamina", "bravery", "gk_diving", "gk_handling",
    "gk_kicking", "gk_positioning", "gk_reflexes", "gk_one_on_ones",
    "gk_communication",
})

# --- Squad generation: detailed-position roster (single source of truth) ---
# Every generated squad starts from this 18-man core so each club always has
# a complete backline (LB, CB, CB, RB + wing-backs), exactly 2 GKs, and a
# realistic midfield/attack spread. Extras beyond squad-size 18 are drawn
# from SQUAD_EXTRA_POSITION_WEIGHTS instead of coarse dice — the old
# per-coarse dice produced squads with 6 GKs or backlines lacking an
# LB/RB entirely (verified: 213/644 squads had no LB at all).
SQUAD_CORE_DETAILED_POSITIONS = [
    "GK", "GK",
    "LB", "CB", "CB", "RB", "LWB", "RWB",
    "CDM", "CM", "CM", "CAM", "LM", "RM",
    "LW", "RW", "ST", "ST",
]
# Extras (squad size 19-24) draw from this weighted pool; GK is excluded so
# a squad never grows beyond its two core keepers.
SQUAD_EXTRA_POSITION_WEIGHTS = {
    "CB": 2.0, "LB": 1.0, "RB": 1.0, "LWB": 0.5, "RWB": 0.5,
    "CDM": 1.0, "CM": 2.0, "CAM": 1.0, "LM": 1.0, "RM": 1.0,
    "LW": 1.0, "RW": 1.0, "ST": 1.5, "CF": 1.0,
}
# Default starting XI (4-4-2): exactly one GK and a LB/CB/CB/RB backline.
# Starters are picked as the best available player for each role — never
# "first 11 after shuffling" (which fielded two GKs or none).
SQUAD_STARTING_XI = [
    "GK", "LB", "CB", "CB", "RB",
    "LM", "CM", "CM", "RM",
    "ST", "ST",
]


# --- Tier-bounded philosophies (single source of truth) ---
# Allowed philosophies per reputation band. The philosophy assignment
# picks ONLY from the allowed list for the club's band — this is what
# keeps Barcelona from becoming a "build-and-sell youth project" and
# keeps a League Two club from becoming "galacticos".
PHILOSOPHY_BANDS = {
    "elite": {  # rep >= 85
        "min_rep": 85,
        "allowed": ["galacticos", "possession_domination", "total_football",
                    "fluid_attacking", "tiki_taka", "gegenpress"],
    },
    "ambitious": {  # 70 <= rep < 85
        "min_rep": 70,
        "allowed": ["tiki_taka", "gegenpress", "wing_play",
                    "high_line_defense", "moneyball",
                    "possession_domination", "balanced", "fluid_attacking"],
    },
    "pragmatic": {  # 55 <= rep < 70
        "min_rep": 55,
        "allowed": ["counter_attack", "direct_football", "wing_play",
                    "high_line_defense", "balanced", "moneyball",
                    "youth_development", "route_one"],
    },
    "survivor": {  # rep < 55
        "min_rep": 0,
        "allowed": ["park_the_bus", "route_one", "direct_football",
                    "counter_attack", "balanced", "youth_development"],
    },
}


def philosophy_band_for(reputation: int) -> str:
    try:
        rep = int(reputation or 0)
    except (TypeError, ValueError):
        rep = 0
    for band_name, spec in PHILOSOPHY_BANDS.items():
        if rep >= spec["min_rep"]:
            return band_name
    return "survivor"


# Probability per club per season that its philosophy shifts within its
# band. Kept low on purpose — the user asked for "rare in real life,
# rare in the game".
PHILOSOPHY_SHIFT_CHANCE = 0.03  # 3%

# Probability per club per season that it MOVES band (up or down).
# This is the "small club becomes a project" / "big club declines"
# event. Also kept very low.
PHILOSOPHY_BAND_SHIFT_CHANCE = 0.01  # 1%

# Canonical tactical profiles per identity. Numeric knobs are 0.0-1.0;
# assign_team_philosophy() copies the profile and jitters each field.
PHILOSOPHY_PROFILES = {
    "galacticos": {"attacking_intent": 0.90, "pressing_intensity": 0.50,
                   "possession_focus": 0.60, "width": 0.60, "tempo": 0.70,
                   "defensive_line": 0.60, "build_up_style": 0.50,
                   "transition_style": 0.70},
    "possession_domination": {"attacking_intent": 0.80, "pressing_intensity": 0.70,
                              "possession_focus": 0.95, "width": 0.70, "tempo": 0.55,
                              "defensive_line": 0.70, "build_up_style": 0.90,
                              "transition_style": 0.50},
    "total_football": {"attacking_intent": 0.85, "pressing_intensity": 0.80,
                       "possession_focus": 0.75, "width": 0.80, "tempo": 0.75,
                       "defensive_line": 0.70, "build_up_style": 0.80,
                       "transition_style": 0.75},
    "fluid_attacking": {"attacking_intent": 0.85, "pressing_intensity": 0.55,
                        "possession_focus": 0.65, "width": 0.75, "tempo": 0.80,
                        "defensive_line": 0.55, "build_up_style": 0.60,
                        "transition_style": 0.85},
    "tiki_taka": {"attacking_intent": 0.75, "pressing_intensity": 0.70,
                  "possession_focus": 0.95, "width": 0.65, "tempo": 0.45,
                  "defensive_line": 0.65, "build_up_style": 0.95,
                  "transition_style": 0.40},
    "gegenpress": {"attacking_intent": 0.80, "pressing_intensity": 0.95,
                   "possession_focus": 0.55, "width": 0.60, "tempo": 0.90,
                   "defensive_line": 0.75, "build_up_style": 0.40,
                   "transition_style": 0.95},
    "wing_play": {"attacking_intent": 0.65, "pressing_intensity": 0.50,
                  "possession_focus": 0.45, "width": 0.95, "tempo": 0.70,
                  "defensive_line": 0.50, "build_up_style": 0.35,
                  "transition_style": 0.70},
    "high_line_defense": {"attacking_intent": 0.60, "pressing_intensity": 0.75,
                          "possession_focus": 0.55, "width": 0.55, "tempo": 0.60,
                          "defensive_line": 0.90, "build_up_style": 0.55,
                          "transition_style": 0.60},
    "moneyball": {"attacking_intent": 0.55, "pressing_intensity": 0.50,
                  "possession_focus": 0.50, "width": 0.55, "tempo": 0.55,
                  "defensive_line": 0.50, "build_up_style": 0.55,
                  "transition_style": 0.60},
    "balanced": {"attacking_intent": 0.50, "pressing_intensity": 0.50,
                 "possession_focus": 0.50, "width": 0.50, "tempo": 0.50,
                 "defensive_line": 0.50, "build_up_style": 0.50,
                 "transition_style": 0.50},
    "counter_attack": {"attacking_intent": 0.55, "pressing_intensity": 0.35,
                       "possession_focus": 0.30, "width": 0.55, "tempo": 0.85,
                       "defensive_line": 0.30, "build_up_style": 0.25,
                       "transition_style": 0.95},
    "direct_football": {"attacking_intent": 0.60, "pressing_intensity": 0.45,
                        "possession_focus": 0.30, "width": 0.60, "tempo": 0.80,
                        "defensive_line": 0.40, "build_up_style": 0.20,
                        "transition_style": 0.80},
    "youth_development": {"attacking_intent": 0.50, "pressing_intensity": 0.55,
                          "possession_focus": 0.55, "width": 0.60, "tempo": 0.60,
                          "defensive_line": 0.50, "build_up_style": 0.60,
                          "transition_style": 0.60},
    "route_one": {"attacking_intent": 0.55, "pressing_intensity": 0.40,
                  "possession_focus": 0.20, "width": 0.45, "tempo": 0.75,
                  "defensive_line": 0.35, "build_up_style": 0.10,
                  "transition_style": 0.75},
    "park_the_bus": {"attacking_intent": 0.20, "pressing_intensity": 0.25,
                     "possession_focus": 0.25, "width": 0.40, "tempo": 0.30,
                     "defensive_line": 0.15, "build_up_style": 0.20,
                     "transition_style": 0.30},
}

# Philosophy -> pressing/tactic mapping for the live engine.
PHILOSOPHY_PRESSING = {
    "galacticos": "normal", "possession_domination": "high",
    "total_football": "high", "fluid_attacking": "normal",
    "tiki_taka": "high", "gegenpress": "high",
    "wing_play": "normal", "high_line_defense": "high",
    "moneyball": "normal", "balanced": "normal",
    "counter_attack": "low", "direct_football": "normal",
    "youth_development": "normal", "route_one": "low",
    "park_the_bus": "low",
}

PHILOSOPHY_TACTIC = {
    "galacticos": "attack", "possession_domination": "attack",
    "total_football": "attack", "fluid_attacking": "attack",
    "tiki_taka": "attack", "gegenpress": "attack",
    "wing_play": "balanced", "high_line_defense": "balanced",
    "moneyball": "balanced", "balanced": "balanced",
    "counter_attack": "defensive", "direct_football": "defensive",
    "youth_development": "balanced", "route_one": "defensive",
    "park_the_bus": "defensive",
}

# Philosophy -> training focus mapping for AI coaching decisions.
# The weekly AI planner syncs each AI club's training_focus to its
# identity so a gegenpress side drills fitness while a tiki-taka side
# drills attack, etc. Player teams are never touched (user's choice wins).
PHILOSOPHY_TRAINING_FOCUS = {
    "galacticos": "attack", "possession_domination": "attack",
    "total_football": "attack", "fluid_attacking": "attack",
    "tiki_taka": "attack", "wing_play": "attack",
    "gegenpress": "physical",
    "counter_attack": "defense", "direct_football": "defense",
    "route_one": "defense", "park_the_bus": "defense",
    "high_line_defense": "defense",
    "moneyball": "balanced", "balanced": "balanced",
    "youth_development": "balanced",
}

# --- Deeper sim: hard rules engine (Feature A, single source of truth) ---
# League.rules_json may override any of these per league.
RULES = {
    "bench": 9,
    "subs": 5,
    "windows": 3,  # + halftime always allowed as a free window
    "min_squad": 16,
    "max_squad": 30,
    "registered_max": 25,
    "foreign_xi": 5,
    "homegrown_min": 4,
    "loans_in_max": 2,
    "loans_out_max": 2,
    "fitness_floor_start": 40,
    "min_age": 16,
    "suspension_yellows": 5,
    "min_on_pitch": 7,
}
# Summer = season weeks 1-3, winter = mid-season 2-week window.
TRANSFER_WINDOWS = {
    "summer_weeks": (1, 3),
    "winter_week": None,  # resolved at runtime: TOTAL_ROUNDS // 2
    "winter_length": 2,
}
# Region mapping for the foreign-player cap (Player.nationality -> region).
NATIONALITY_REGION = {
    "Europe": "Europe", "South_America": "South_America",
    "Middle_East": "Middle_East", "Africa": "Africa", "Asia": "Asia",
}

# --- Deeper sim: tactical system (Feature B) ---
TACTICS_MENTALITY = (1, 2, 3, 4, 5)  # very defensive .. very attacking
TACTICS_DEFENSIVE_LINES = ("much_lower", "lower", "standard", "higher", "much_higher")
TACTICS_ENGAGEMENT = ("low_block", "standard", "high_press")
TACTICS_TACKLING = ("cautious", "normal", "aggressive")
FAMILIARITY_PER_L1 = 8
FAMILIARITY_PER_L2 = 4
FAMILIARITY_PER_L3 = 2
FAMILIARITY_RESET_ON_CHANGE = 40

# --- Deeper sim: tiered engines + world sim (Feature C) ---
SIM_LEVELS = (1, 2, 3)
ENGINE_BADGES = {1: "محاكاة كاملة", 2: "محاكاة متوسطة", 3: "محاكاة سريعة"}
L1_BUDGET_MS = 120
L2_BUDGET_MS = 3
L3_BUDGET_MS = 0.3
WORLD_WEEK_BUDGET_S = 5
WORLD_COMMIT_CHUNK = 200

# --- World simulation hub (fix.txt): single source of truth ---
# Probability per world week that an AI-to-AI transfer fires. The forced
# fallback every 4th week guarantees measurable frequency in seeded tests.
WORLD_SIM_TRANSFER_CHANCE = 0.35
WORLD_SIM_SACK_CHANCE = 0.08
WORLD_SIM_UPSET_REP_GAP = 10
WORLD_SIM_BOARD_DRIFT_MIN = 1
WORLD_SIM_BOARD_DRIFT_MAX = 3
WORLD_SIM_BOARD_PROMISE_TOP = 4  # rank <= this counts as "good" for confidence
WORLD_SIM_MAX_INBOX = 3
WORLD_SIM_CONTINENTAL_SPOTS = 4  # domestic leagues: top-N badge + standings legend

# --- Deeper sim: competent-but-beatable AI (Feature D) ---
AI_DIFFICULTIES = ("سهل", "عادي", "صعب")
AI_DIFFICULTY_DEFAULT = "عادي"
AI_SKILL_RANGE = (0.35, 0.85)
AI_MISTAKE_BASE = 0.35  # mistake prob = (1 - skill) * AI_MISTAKE_BASE
AI_MAX_BIDDERS_PER_PLAYER = 2
AI_MAX_COUNTERS = 2
AI_REACTION_DAYS = (1, 3)
MATCH_AI_CHECKPOINTS = (45, 60, 75)
MATCH_AI_LOW_RATING = 6.2
MATCH_AI_LOW_FITNESS = 50

# Philosophy -> full coaching profile. THIS is what makes AI coaching
# decisions come from the team philosophy: every AI choice (mentality,
# pressing, line, tempo, width, subs, risk) is derived here first, then
# only modulated by match state and skill noise. Never hard-code AI
# tactics elsewhere — import from here.
PHILOSOPHY_COACHING = {
    "galacticos": {"mentality": 4, "pressing": 2, "def_line": "higher",
                   "tempo": "fast", "width": "standard", "risk": 0.7,
                   "sub_aggression": 0.7, "chase_shape": "attack"},
    "possession_domination": {"mentality": 4, "pressing": 3, "def_line": "higher",
                   "tempo": "normal", "width": "wide", "risk": 0.5,
                   "sub_aggression": 0.5, "chase_shape": "possession"},
    "total_football": {"mentality": 4, "pressing": 3, "def_line": "higher",
                   "tempo": "fast", "width": "wide", "risk": 0.6,
                   "sub_aggression": 0.6, "chase_shape": "attack"},
    "fluid_attacking": {"mentality": 4, "pressing": 2, "def_line": "standard",
                   "tempo": "fast", "width": "wide", "risk": 0.65,
                   "sub_aggression": 0.7, "chase_shape": "attack"},
    "tiki_taka": {"mentality": 3, "pressing": 3, "def_line": "higher",
                   "tempo": "slow", "width": "standard", "risk": 0.4,
                   "sub_aggression": 0.4, "chase_shape": "possession"},
    "gegenpress": {"mentality": 4, "pressing": 4, "def_line": "much_higher",
                   "tempo": "fast", "width": "standard", "risk": 0.7,
                   "sub_aggression": 0.8, "chase_shape": "press"},
    "wing_play": {"mentality": 3, "pressing": 2, "def_line": "standard",
                   "tempo": "fast", "width": "wide", "risk": 0.5,
                   "sub_aggression": 0.5, "chase_shape": "width"},
    "high_line_defense": {"mentality": 3, "pressing": 3, "def_line": "much_higher",
                   "tempo": "normal", "width": "standard", "risk": 0.55,
                   "sub_aggression": 0.5, "chase_shape": "press"},
    "moneyball": {"mentality": 3, "pressing": 2, "def_line": "standard",
                   "tempo": "normal", "width": "standard", "risk": 0.5,
                   "sub_aggression": 0.5, "chase_shape": "balanced"},
    "balanced": {"mentality": 3, "pressing": 2, "def_line": "standard",
                   "tempo": "normal", "width": "standard", "risk": 0.5,
                   "sub_aggression": 0.5, "chase_shape": "balanced"},
    "counter_attack": {"mentality": 2, "pressing": 1, "def_line": "lower",
                   "tempo": "fast", "width": "standard", "risk": 0.6,
                   "sub_aggression": 0.6, "chase_shape": "counter"},
    "direct_football": {"mentality": 3, "pressing": 2, "def_line": "lower",
                   "tempo": "fast", "width": "standard", "risk": 0.55,
                   "sub_aggression": 0.5, "chase_shape": "direct"},
    "youth_development": {"mentality": 3, "pressing": 2, "def_line": "standard",
                   "tempo": "normal", "width": "standard", "risk": 0.5,
                   "sub_aggression": 0.6, "chase_shape": "balanced"},
    "route_one": {"mentality": 2, "pressing": 1, "def_line": "lower",
                   "tempo": "fast", "width": "narrow", "risk": 0.55,
                   "sub_aggression": 0.4, "chase_shape": "direct"},
    "park_the_bus": {"mentality": 1, "pressing": 1, "def_line": "much_lower",
                   "tempo": "slow", "width": "narrow", "risk": 0.25,
                   "sub_aggression": 0.3, "chase_shape": "defend"},
}
