"""Position-specific overalls + tier-bounded philosophies + identity shifts."""
from models import db, GameState, League, Match, Player, Team


def _elite_team(rep=95, name="Test Elite FC"):
    t = Team.query.filter_by(name=name).first()
    if t is None:
        league = League.query.first()
        t = Team(name=name, morale=70, budget=5_000_000, reputation=rep,
                 is_player_team=False, league_id=league.id)
        db.session.add(t)
        db.session.commit()
    else:
        t.reputation = rep
        db.session.commit()
    return t


def _small_team(rep=40, name="Test Small FC"):
    return _elite_team(rep=rep, name=name)


class TestPositionSpecificOverall:
    def test_striker_overall_ignores_interceptions(self, ctx):
        from services import recompute_aggregates
        st = Player.query.filter_by(detailed_position="ST").first()
        assert st is not None
        st.interceptions = 99
        recompute_aggregates(st)
        low = st.overall
        st.interceptions = 1
        recompute_aggregates(st)
        high = st.overall
        assert low == high

    def test_cb_overall_ignores_finishing(self, ctx):
        from services import recompute_aggregates
        cb = Player.query.filter_by(detailed_position="CB").first()
        assert cb is not None
        cb.finishing = 99
        recompute_aggregates(cb)
        a = cb.overall
        cb.finishing = 1
        recompute_aggregates(cb)
        b = cb.overall
        assert a == b

    def test_weights_sum_to_one(self, ctx):
        from config import POSITION_OVERALL_WEIGHTS
        for pos, weights in POSITION_OVERALL_WEIGHTS.items():
            assert abs(sum(weights.values()) - 1.0) < 0.001, pos

    def test_overall_range(self, ctx):
        for p in Player.query.limit(100):
            assert 1 <= (p.overall or 0) <= 99

    def test_striker_overall_higher_than_cb_with_same_attrs(self, ctx):
        from services import recompute_aggregates
        from config import POSITION_OVERALL_WEIGHTS
        # Identical attribute sets; only the position table differs.
        # Striker keys high, CB keys low -> ST overall must exceed CB overall.
        st_keys = set(POSITION_OVERALL_WEIGHTS["ST"].keys())
        cb_keys = set(POSITION_OVERALL_WEIGHTS["CB"].keys())
        st_only = st_keys - cb_keys
        cb_only = cb_keys - st_keys

        def _mk(det):
            p = Player(name=f"tmp-{det}", position="MID", detailed_position=det,
                       attack=50, defense=50, overall=50, potential=80, age=24)
            for attr in POSITION_OVERALL_WEIGHTS[det].keys():
                if attr in st_only:
                    setattr(p, attr, 90)
                elif attr in cb_only:
                    setattr(p, attr, 40)
                else:
                    setattr(p, attr, 65)
            # Irrelevant-table attrs pinned so they cannot leak in.
            p.interceptions = 40
            p.finishing = 90
            return p

        st = _mk("ST")
        cb = _mk("CB")
        recompute_aggregates(st)
        recompute_aggregates(cb)
        assert st.overall > cb.overall


class TestPhilosophyBands:
    def test_elite_club_never_gets_youth_development(self, ctx):
        from services import assign_team_philosophy
        real = _elite_team(rep=95, name="Test Real FC")
        for _ in range(50):
            assign_team_philosophy(real)
            db.session.commit()
            assert real.philosophy.primary != "youth_development"
            assert real.philosophy.primary != "park_the_bus"

    def test_lower_league_club_never_gets_galacticos(self, ctx):
        from services import assign_team_philosophy
        small = _small_team(rep=40, name="Test Tiny FC")
        for _ in range(50):
            assign_team_philosophy(small)
            db.session.commit()
            assert small.philosophy.primary not in ("galacticos", "total_football")

    def test_within_band_randomised(self, ctx):
        from services import assign_team_philosophy
        seen = set()
        for i in range(10):
            t = _elite_team(rep=92, name=f"Elite Random {i}")
            assign_team_philosophy(t)
            db.session.commit()
            seen.add(t.philosophy.primary)
        assert len(seen) >= 2

    def test_band_shift_is_rare(self, ctx, monkeypatch):
        import random as _r
        monkeypatch.setattr(_r, "random", lambda: 0.99)
        from services import roll_season_philosophy_shifts
        before = {t.id: (t.philosophy.primary if t.philosophy else None) for t in Team.query.all()}
        roll_season_philosophy_shifts(GameState.query.first())
        after = {t.id: (t.philosophy.primary if t.philosophy else None) for t in Team.query.all()}
        assert before == after

    def test_band_shift_changes_philosophy_when_triggered(self, ctx, monkeypatch):
        import random as _r
        monkeypatch.setattr(_r, "random", lambda: 0.001)
        from services import roll_season_philosophy_shifts
        msgs = roll_season_philosophy_shifts(GameState.query.first())
        assert len(msgs) >= 1

    def test_elite_club_band_shift_only_with_top_finish(self, ctx, monkeypatch):
        import random as _r
        from config import PHILOSOPHY_BANDS
        from services import roll_season_philosophy_shifts
        # Build a 6-team league; elite club finishes LAST.
        league = League(name="Shift Test League", tier=2, reputation=60)
        db.session.add(league)
        db.session.flush()
        teams = []
        for j in range(6):
            t = Team(name=f"Shift-T{j}", morale=70, budget=500000,
                     is_player_team=False, league_id=league.id,
                     points=100 - j * 10, goals_for=50 - j * 5, goals_against=10,
                     reputation=90 if j == 5 else 70)
            db.session.add(t)
            teams.append(t)
        db.session.commit()
        bottom = teams[-1]
        assert bottom.reputation >= 85
        from services import assign_team_philosophy
        assign_team_philosophy(bottom)
        db.session.commit()
        order = ["survivor", "pragmatic", "ambitious", "elite"]
        before_idx = order.index(bottom.philosophy.band)
        monkeypatch.setattr(_r, "random", lambda: 0.001)
        roll_season_philosophy_shifts(GameState.query.first())
        db.session.commit()
        after_band = bottom.philosophy.band
        # Must NOT have moved UP (a bottom-table club cannot climb bands).
        assert order.index(after_band) <= before_idx


class TestOrganicSurprises:
    def test_youngster_surprise_score_higher_for_high_potential(self, ctx):
        from services import evaluate_youngster_surprise_potential
        wunderkind = Player.query.filter(Player.age <= 21).order_by(Player.potential.desc()).first()
        if wunderkind is None:
            wunderkind = Player(name="w", position="MID", detailed_position="CAM",
                                attack=60, defense=55, overall=60, potential=92, age=19)
            db.session.add(wunderkind)
            db.session.commit()
        journeyman = Player.query.filter(Player.age >= 28).order_by(Player.potential.asc()).first()
        if journeyman is None:
            journeyman = Player(name="j", position="MID", detailed_position="CM",
                                attack=60, defense=60, overall=62, potential=62, age=30)
            db.session.add(journeyman)
            db.session.commit()
        assert (evaluate_youngster_surprise_potential(wunderkind, "moneyball")
                > evaluate_youngster_surprise_potential(journeyman, "moneyball"))

    def test_philosophy_bonus(self, ctx):
        from services import evaluate_youngster_surprise_potential
        p = Player.query.filter(Player.age <= 21).first()
        if p is None:
            p = Player(name="y", position="MID", detailed_position="CAM",
                       attack=60, defense=55, overall=60, potential=70, age=19)
            db.session.add(p)
            db.session.commit()
        a = evaluate_youngster_surprise_potential(p, "youth_development")
        b = evaluate_youngster_surprise_potential(p, "galacticos")
        assert a > b


class TestMatchEnginePhilosophy:
    def test_ai_team_plays_its_philosophy(self, ctx):
        from live_match import start_live
        from services import effective_match_tactic, effective_pressing
        ai_team = Team.query.filter_by(is_player_team=False).first()
        match = Match.query.filter_by(played=False).first()
        # Stale values that must be overridden at kickoff.
        ai_team.pressing_style = "low" if effective_pressing(ai_team) == "high" else "high"
        ai_team.match_tactic = "defensive" if effective_match_tactic(ai_team) == "attack" else "attack"
        assert start_live(match) is True
        assert ai_team.pressing_style == effective_pressing(ai_team)
        assert ai_team.match_tactic == effective_match_tactic(ai_team)
        assert match.live_state["home_xp" if match.home_team_id == ai_team.id else "away_xp"]

    def test_overall_used_in_ratings(self, ctx):
        from live_match import _team_ratings
        team = Team.query.first()
        on_pitch = team.players[:11]
        att, dfn = _team_ratings(team, on_pitch, chemistry=60)
        assert att > 0 and dfn > 0
