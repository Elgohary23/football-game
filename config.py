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
MAX_FACILITY_LEVEL = 10
STAFF_FIRE_COMPENSATION_RATIO = 0.3
STAFF_MARKET_POOL_LIMIT = 8
PROMOTION_BONUS_PER_TIER = 500_000

MARKET_VALUE_MIN = 50_000
MARKET_VALUE_MAX = 50_000_000
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

# --- Player development ---
DEVELOPMENT_UPDATE_DAYS = 30
SEASON_RESET_PLAYER_STATS = True

# --- AI transfers ---
POSITION_REQUIREMENTS = [("GK", 1), ("DEF", 4), ("MID", 4), ("FWD", 2)]
TRANSFER_OFFER_DEADLINE_DAYS = 3
TRANSFER_OFFER_PRICE_RANGE = (0.9, 1.2)
REPUTATION_TOLERANCE = 10

# --- Cleanup / garbage collection ---
OLD_DATA_KEEP_PLAYED_MATCHES = 150

# --- Schema migration guard ---
TEMPLATE_SCHEMA_VERSION = 4  # bump to force a template.db rebuild

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

# --- Player comparison ---
COMPARE_RATING_FIELDS = ("attack", "defense", "potential", "stamina",
                         "technique", "speed", "fitness", "sharpness", "morale")

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