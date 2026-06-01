from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True)
    morale = db.Column(db.Integer, default=70)
    budget = db.Column(db.Integer, default=1000000)
    reputation = db.Column(db.Integer, default=50)  # 1-100
    
    training_facilities = db.Column(db.Integer, default=3)  # 1-5
    youth_facilities = db.Column(db.Integer, default=3)     # 1-5
    
    points = db.Column(db.Integer, default=0)
    goals_for = db.Column(db.Integer, default=0)
    goals_against = db.Column(db.Integer, default=0)
    wins = db.Column(db.Integer, default=0)
    losses = db.Column(db.Integer, default=0)
    draws = db.Column(db.Integer, default=0)
    
    is_player_team = db.Column(db.Boolean, default=False)
    league_id = db.Column(db.Integer, db.ForeignKey('league.id'))
    players = db.relationship('Player', backref='team', lazy=True)


class Player(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50))
    position = db.Column(db.String(10))  # GK, DEF, MID, FWD
    
    attack = db.Column(db.Integer)
    defense = db.Column(db.Integer)
    potential = db.Column(db.Integer)    # Max possible attack/defense
    
    age = db.Column(db.Integer, default=20)
    fitness = db.Column(db.Integer, default=100)
    injury_days = db.Column(db.Integer, default=0)
    
    personality = db.Column(db.String(20), default="Normal")  # Leader, Ambitious, Glass, etc.
    region = db.Column(db.String(20), default="Europe")
    
    height = db.Column(db.Integer)  # in cm
    weight = db.Column(db.Integer)  # in kg
    
    market_value = db.Column(db.Integer, default=100000)
    is_starting = db.Column(db.Boolean, default=False)
    
    goals = db.Column(db.Integer, default=0)
    assists = db.Column(db.Integer, default=0)
    matches_played = db.Column(db.Integer, default=0)
    
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'))

    # Performance Indexes
    __table_args__ = (
        db.Index('idx_position_attack_age', 'position', 'attack', 'age'),
        db.Index('idx_team_id', 'team_id'),
    )


class League(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True)
    tier = db.Column(db.Integer)  # 1 for top, 2 for second, etc.
    reputation = db.Column(db.Integer)
    teams = db.relationship('Team', backref='league', lazy=True)


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


class Match(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    home_team_id = db.Column(db.Integer, db.ForeignKey('team.id'))
    away_team_id = db.Column(db.Integer, db.ForeignKey('team.id'))
    week = db.Column(db.Integer)
    played = db.Column(db.Boolean, default=False)
    result = db.Column(db.String(20), nullable=True)
    
    home_score = db.Column(db.Integer, default=0)
    away_score = db.Column(db.Integer, default=0)
    
    home_events = db.Column(db.Text, nullable=True)
    away_events = db.Column(db.Text, nullable=True)
    
    home_possession = db.Column(db.Integer, default=50)
    away_possession = db.Column(db.Integer, default=50)
    
    home_team = db.relationship('Team', foreign_keys=[home_team_id])
    away_team = db.relationship('Team', foreign_keys=[away_team_id])


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
    
    player = db.relationship('Player')
    match = db.relationship('Match')


class TransferOffer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey('player.id'))
    from_team_id = db.Column(db.Integer, db.ForeignKey('team.id'))
    to_team_id = db.Column(db.Integer, db.ForeignKey('team.id'))
    amount = db.Column(db.Integer)
    status = db.Column(db.String(20), default='pending')
    created_day = db.Column(db.Integer)
    deadline_day = db.Column(db.Integer)
    
    player = db.relationship('Player', foreign_keys=[player_id])
    from_team = db.relationship('Team', foreign_keys=[from_team_id])
    to_team = db.relationship('Team', foreign_keys=[to_team_id])