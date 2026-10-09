"""SQLAlchemy models.

Domain methods live on the model classes themselves (e.g. ``Team.get_strength``)
so route handlers stay thin and call sites are self-documenting.
"""

import math

from flask_sqlalchemy import SQLAlchemy

from config import (
    DEFAULT_BUDGET,
    LEADER_MORALE_BONUS,
    MIN_LINEUP_RATING,
    SHORT_SQUAD_PENALTY_CAP,
    STARTERS_LIMIT,
    TOP_LEAGUE_ROUNDS,
    LOWER_LEAGUE_ROUNDS,
)

db = SQLAlchemy()


class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True)
    morale = db.Column(db.Integer, default=70)
    budget = db.Column(db.Integer, default=DEFAULT_BUDGET)
    reputation = db.Column(db.Integer, default=50)

    training_facilities = db.Column(db.Integer, default=3)
    youth_facilities = db.Column(db.Integer, default=3)

    training_focus = db.Column(db.String(20), default="balanced")
    training_intensity = db.Column(db.Integer, default=5)

    formation_data = db.Column(db.JSON, nullable=True)

    # --- Advanced tactics (FM features) ---
    freekick_taker_id = db.Column(db.Integer, db.ForeignKey('player.id'), nullable=True)
    penalty_taker_id = db.Column(db.Integer, db.ForeignKey('player.id'), nullable=True)
    pressing_style = db.Column(db.String(20), default="normal")
    match_tactic = db.Column(db.String(20), default="balanced")
    last_formation = db.Column(db.String(20), nullable=True)
    tactical_familiarity = db.Column(db.Integer, default=100)
    chemistry = db.Column(db.Integer, default=50)

    points = db.Column(db.Integer, default=0)
    goals_for = db.Column(db.Integer, default=0)
    goals_against = db.Column(db.Integer, default=0)
    wins = db.Column(db.Integer, default=0)
    losses = db.Column(db.Integer, default=0)
    draws = db.Column(db.Integer, default=0)

    is_player_team = db.Column(db.Boolean, default=False)
    league_id = db.Column(db.Integer, db.ForeignKey('league.id'))
    youth_goals = db.Column(db.Integer, default=0)
    players = db.relationship('Player', backref='team', lazy=True, foreign_keys='Player.team_id')

    table_sort_pipeline = staticmethod(
        lambda teams: sorted(
            teams,
            key=lambda t: (t.points, t.goals_for - t.goals_against, t.goals_for),
            reverse=True,
        )
    )

    def applicable_staff(self) -> list:
        return [s for s in self.staff_members]

    def get_staff_bonuses(self) -> dict:
        """Aggregate coaching bonuses with logarithmic dampening (Tip 23).

        Raw sums would stack linearly and let stacked high-level staff push a
        squad past any reasonable ceiling; ``log1p`` keeps every extra hire
        valuable yet saturating.
        """
        bonuses = {
            "attack": 0.0,
            "defense": 0.0,
            "fitness": 0.0,
            "youth": 0.0,
            "injury_reduction": 0.0,
        }
        for s in self.staff_members:
            bonuses["attack"] += s.attack_bonus * s.level
            bonuses["defense"] += s.defense_bonus * s.level
            bonuses["fitness"] += s.fitness_bonus * s.level
            bonuses["youth"] += s.youth_bonus * s.level
            bonuses["injury_reduction"] += s.injury_reduction * s.level

        for key in ("attack", "defense", "fitness", "youth"):
            bonuses[key] = math.log1p(bonuses[key])
        return bonuses

    def get_strength(self) -> tuple[float, float]:
        """Best-XI attack/defence ratings used by the match engine (Tip 5).

        Moves the former ``simulation.get_lineup_strength`` onto the model so
        callers only need ``team.get_strength()``.
        """
        if not self.players:
            return 0.0, 0.0

        starters = [
            p for p in self.players
            if p.is_starting and p.injury_days == 0
        ]

        if not starters:
            return MIN_LINEUP_RATING, MIN_LINEUP_RATING

        num_starters = len(starters)
        penalty = 1.0
        if num_starters < STARTERS_LIMIT:
            penalty = num_starters / SHORT_SQUAD_PENALTY_CAP

        base_att = sum(p.attack for p in starters) * penalty
        base_def = sum(p.defense for p in starters) * penalty

        leader_bonus = 0
        if any(p.personality == "Leader" for p in starters):
            leader_bonus = LEADER_MORALE_BONUS

        morale_bonus = (self.morale // 20) + leader_bonus

        staff = self.get_staff_bonuses()

        att_result = (base_att / STARTERS_LIMIT) + morale_bonus + staff["attack"]
        def_result = (base_def / STARTERS_LIMIT) + morale_bonus + staff["defense"]

        return att_result, def_result


class Player(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50))
    position = db.Column(db.String(10))

    attack = db.Column(db.Integer)
    defense = db.Column(db.Integer)
    potential = db.Column(db.Integer)

    age = db.Column(db.Integer, default=20)
    fitness = db.Column(db.Integer, default=100)
    injury_days = db.Column(db.Integer, default=0)

    personality = db.Column(db.String(20), default="Normal")
    region = db.Column(db.String(20), default="Europe")

    height = db.Column(db.Integer)
    weight = db.Column(db.Integer)

    market_value = db.Column(db.Integer, default=100000)
    is_starting = db.Column(db.Boolean, default=False)

    goals = db.Column(db.Integer, default=0)
    assists = db.Column(db.Integer, default=0)
    matches_played = db.Column(db.Integer, default=0)

    stamina = db.Column(db.Integer, default=100)
    technique = db.Column(db.Integer, default=50)
    speed = db.Column(db.Integer, default=50)

    # --- RPG layer (FM features) ---
    traits = db.Column(db.JSON, default=list)
    sharpness = db.Column(db.Integer, default=50)
    morale = db.Column(db.Integer, default=50)

    injury_history = db.Column(db.Text, nullable=True)

    team_id = db.Column(db.Integer, db.ForeignKey('team.id'))

    __table_args__ = (
        db.Index('idx_position_attack_age', 'position', 'attack', 'age'),
        db.Index('idx_team_id', 'team_id'),
        db.Index('idx_goals', 'goals'),
        db.Index('idx_assists', 'assists'),
    )

    @staticmethod
    def primary_ability(position: str) -> str:
        return "attack" if position in ("FWD", "MID") else "defense"

    def rating_for(self) -> int:
        ability = self.primary_ability(self.position)
        return getattr(self, ability)

    def value_label(self) -> str:
        return f"${self.market_value:,}" if self.market_value else "$0"


class League(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True)
    tier = db.Column(db.Integer)
    reputation = db.Column(db.Integer)
    teams = db.relationship('Team', backref='league', lazy=True)

    def rounds(self) -> int:
        return TOP_LEAGUE_ROUNDS if self.tier == 1 else LOWER_LEAGUE_ROUNDS


class GameState(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    manager_name = db.Column(db.String(50), default="المدرب")
    manager_nationality = db.Column(db.String(50), default="غير محدد")
    current_day = db.Column(db.Integer, default=1)
    current_week = db.Column(db.Integer, default=1)
    season_over = db.Column(db.Boolean, default=False)
    user_league_id = db.Column(db.Integer, db.ForeignKey('league.id'), nullable=True)
    manager_wins = db.Column(db.Integer, default=0)
    manager_losses = db.Column(db.Integer, default=0)
    manager_draws = db.Column(db.Integer, default=0)
    trophies = db.Column(db.Integer, default=0)
    start_date = db.Column(db.String(10), default="2026-06-01")
    current_date = db.Column(db.String(10), default="2026-06-01")
    season_number = db.Column(db.Integer, default=1)
    manager_history = db.Column(db.Text, nullable=True)
    board_confidence = db.Column(db.Integer, default=60)


class Match(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    home_team_id = db.Column(db.Integer, db.ForeignKey('team.id'))
    away_team_id = db.Column(db.Integer, db.ForeignKey('team.id'))
    week = db.Column(db.Integer)
    date = db.Column(db.String(10), nullable=True)
    played = db.Column(db.Boolean, default=False)
    result = db.Column(db.String(20), nullable=True)

    home_score = db.Column(db.Integer, default=0)
    away_score = db.Column(db.Integer, default=0)

    home_events = db.Column(db.JSON, nullable=True)
    away_events = db.Column(db.JSON, nullable=True)

    home_possession = db.Column(db.Integer, default=50)
    away_possession = db.Column(db.Integer, default=50)

    home_shots = db.Column(db.Integer, default=0)
    away_shots = db.Column(db.Integer, default=0)
    home_shots_on_target = db.Column(db.Integer, default=0)
    away_shots_on_target = db.Column(db.Integer, default=0)
    home_corners = db.Column(db.Integer, default=0)
    away_corners = db.Column(db.Integer, default=0)
    home_fouls = db.Column(db.Integer, default=0)
    away_fouls = db.Column(db.Integer, default=0)

    home_xg = db.Column(db.Float, default=0.0)
    away_xg = db.Column(db.Float, default=0.0)

    # Live interactive match state (new engine). Old engine untouched.
    live_active = db.Column(db.Boolean, default=False)
    live_state = db.Column(db.JSON, nullable=True)

    home_team = db.relationship('Team', foreign_keys=[home_team_id])
    away_team = db.relationship('Team', foreign_keys=[away_team_id])

    __table_args__ = (
        db.Index('idx_match_week_played', 'week', 'played'),
        db.Index('idx_match_home_team', 'home_team_id'),
        db.Index('idx_match_away_team', 'away_team_id'),
    )


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100))
    content = db.Column(db.Text)
    type = db.Column(db.String(20))
    read = db.Column(db.Boolean, default=False)
    created_day = db.Column(db.Integer)
    urgent = db.Column(db.Boolean, default=False)


class PlayerRating(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey('player.id'))
    match_id = db.Column(db.Integer, db.ForeignKey('match.id'))
    rating = db.Column(db.Float)
    goals = db.Column(db.Integer, default=0)
    assists = db.Column(db.Integer, default=0)
    shots = db.Column(db.Integer, default=0)
    passes_completed = db.Column(db.Integer, default=0)
    tackles = db.Column(db.Integer, default=0)
    minutes_played = db.Column(db.Integer, default=90)
    key_passes = db.Column(db.Integer, default=0)
    errors = db.Column(db.Integer, default=0)

    player = db.relationship('Player')
    match = db.relationship('Match')

    __table_args__ = (
        db.CheckConstraint('rating IS NULL OR (rating >= 1.0 AND rating <= 10.0)',
                           name='ck_player_rating_range'),
        db.CheckConstraint('goals IS NULL OR goals >= 0', name='ck_player_rating_goals'),
        db.CheckConstraint('assists IS NULL OR assists >= 0', name='ck_player_rating_assists'),
    )


class TransferOffer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey('player.id'))
    from_team_id = db.Column(db.Integer, db.ForeignKey('team.id'))
    to_team_id = db.Column(db.Integer, db.ForeignKey('team.id'))
    amount = db.Column(db.Integer)
    status = db.Column(db.String(20), default='pending')
    created_day = db.Column(db.Integer)
    deadline_day = db.Column(db.Integer)

    # --- FM features ---
    sell_on_clause = db.Column(db.Float, default=0.0)  # 0.0 .. 1.0 keeps a cut for from_team
    offered_player_id = db.Column(db.Integer, db.ForeignKey('player.id'), nullable=True)

    player = db.relationship('Player', foreign_keys=[player_id])
    offered_player = db.relationship('Player', foreign_keys=[offered_player_id])
    from_team = db.relationship('Team', foreign_keys=[from_team_id])
    to_team = db.relationship('Team', foreign_keys=[to_team_id])


class Staff(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50))
    role = db.Column(db.String(30))
    specialty = db.Column(db.String(30), nullable=True)
    level = db.Column(db.Integer, default=1)
    salary = db.Column(db.Integer, default=50000)
    attack_bonus = db.Column(db.Float, default=0.0)
    defense_bonus = db.Column(db.Float, default=0.0)
    fitness_bonus = db.Column(db.Float, default=0.0)
    youth_bonus = db.Column(db.Float, default=0.0)
    injury_reduction = db.Column(db.Float, default=0.0)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=True)

    # --- Scouting assignments (FM features) ---
    region = db.Column(db.String(20), nullable=True)
    on_assignment = db.Column(db.Boolean, default=False)

    team = db.relationship('Team', backref='staff_members')


class TacticPreset(db.Model):
    """Saved tactical presets: standard / attacking / defensive (FM feature)."""
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'))
    preset_type = db.Column(db.String(10))  # standard | attacking | defensive
    name = db.Column(db.String(50), default="خطة محفوظة")
    formation_data = db.Column(db.JSON, nullable=True)
    pressing_style = db.Column(db.String(20), default="normal")
    match_tactic = db.Column(db.String(20), default="balanced")


class Mentoring(db.Model):
    """Veteran mentor develops a young player (FM feature)."""
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'))
    mentor_id = db.Column(db.Integer, db.ForeignKey('player.id'))
    mentee_id = db.Column(db.Integer, db.ForeignKey('player.id'))
    started_day = db.Column(db.Integer, default=0)

    mentor = db.relationship('Player', foreign_keys=[mentor_id])
    mentee = db.relationship('Player', foreign_keys=[mentee_id])


class ScoutReport(db.Model):
    """Knowledge level per player/team so ratings can be masked (FM feature)."""
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'))
    player_id = db.Column(db.Integer, db.ForeignKey('player.id'))
    knowledge_level = db.Column(db.Integer, default=0)
    updated_day = db.Column(db.Integer, default=0)

    __table_args__ = (db.UniqueConstraint('team_id', 'player_id', name='uq_scout_team_player'),)


class BoardObjective(db.Model):
    """Seasonal board objectives beyond winning (FM feature)."""
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'))
    season = db.Column(db.Integer, default=1)
    objective_type = db.Column(db.String(20))  # avg_age | youth_goals | league_position
    target_value = db.Column(db.Float)
    achieved = db.Column(db.Boolean, default=False)


class SellOnClause(db.Model):
    """A cut owed to a previous club when a player is sold (FM feature)."""
    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey('player.id'))
    club_id = db.Column(db.Integer, db.ForeignKey('team.id'))
    percentage = db.Column(db.Float, default=0.0)

    club = db.relationship('Team')