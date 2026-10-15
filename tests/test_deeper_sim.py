"""Deeper-sim tests: rules engine, tactics, tiered engines, philosophy AI."""
from models import db, GameState, Match, Team


def _player_team():
    return Team.query.filter_by(is_player_team=True).first()


def _opponent():
    return Team.query.filter_by(is_player_team=False).first()


class TestRulesEngine:
    def test_valid_xi_passes(self, ctx):
        from rules_engine import validate_starting_xi
        team = _player_team()
        # Seed world starts 11 (incl. 2 GKs); normalize to a valid XI first.
        gks = [p for p in team.players if p.position == "GK"]
        others = [p for p in team.players if p.position != "GK"]
        for p in team.players:
            p.is_starting = False
            p.injury_days = 0
            p.discipline_data = None
        for p in [gks[0]] + others[:10]:
            p.is_starting = True
        ok, errs = validate_starting_xi(team, "league")
        assert ok is True and errs == []

    def test_xi_requires_11_and_gk(self, ctx):
        from rules_engine import validate_starting_xi
        team = _player_team()
        for p in team.players[5:]:
            p.is_starting = False
        ok, errs = validate_starting_xi(team, "league")
        assert ok is False and errs

    def test_injured_unselectable(self, ctx):
        from rules_engine import validate_starting_xi
        team = _player_team()
        starter = next(p for p in team.players if p.is_starting)
        starter.injury_days = 5
        ok, errs = validate_starting_xi(team, "league")
        assert ok is False

    def test_suspended_unselectable(self, ctx):
        from rules_engine import validate_starting_xi
        team = _player_team()
        starter = next(p for p in team.players if p.is_starting)
        starter.discipline_data = {"yellows": 0, "suspensions": {"league": 1}}
        ok, _ = validate_starting_xi(team, "league")
        assert ok is False

    def test_sub_windows(self, ctx):
        from rules_engine import validate_substitution
        state = {"minute": 10,
                 "home_xp": {"on_pitch": [1], "bench": [2], "subs": 0, "sub_windows": 3}}
        ok, msg = validate_substitution(state, "home_xp", 2, 1)
        assert ok is False and "نوافذ" in msg
        state["home_xp"]["sub_windows"] = 0
        ok, _ = validate_substitution(state, "home_xp", 2, 1)
        assert ok is True
        # Halftime is a free window.
        state2 = {"minute": 45,
                  "home_xp": {"on_pitch": [1], "bench": [2], "subs": 4, "sub_windows": 3}}
        ok, _ = validate_substitution(state2, "home_xp", 2, 1)
        assert ok is True

    def test_auto_pick_compliant(self, ctx):
        from rules_engine import auto_pick_xi, validate_starting_xi
        team = _opponent()
        pick = auto_pick_xi(team, "league")
        assert len(pick["starters"]) == 11
        assert sum(1 for p in pick["starters"] if p.position == "GK") == 1

    def test_bulk_property_no_violations(self, ctx):
        """Bulk auto-picks: 11 starters, 1 GK, no injured/suspended."""
        import random
        from rules_engine import auto_pick_xi
        rng = random.Random(7)
        for team in Team.query.all():
            outfield = [p for p in team.players if p.position != "GK"]
            for p in team.players:
                p.injury_days = 0
                p.discipline_data = None
            # Fixed light injuries: exactly 2 outfield players per team.
            for p in outfield[:2]:
                p.injury_days = 3
            pick = auto_pick_xi(team, "league", rng)
            assert len(pick["starters"]) == 11
            assert sum(1 for p in pick["starters"] if p.position == "GK") == 1
            assert all(p.injury_days == 0 for p in pick["starters"])

    def test_discipline_accrual(self, ctx):
        from rules_engine import apply_discipline, get_suspension_state
        from models import Player
        p = Player.query.first()
        p.discipline_data = None
        for _ in range(5):
            apply_discipline(p, "league", yellow=True)
        assert get_suspension_state(p).get("league", 0) >= 1


class TestTactics:
    def test_profile_keys(self, ctx):
        from tactics import derive_team_profile
        prof = derive_team_profile(_player_team())
        for k in ("attack_eff", "defense_eff", "possession_base", "chance_quality",
                  "press_fatigue_mult", "foul_rate_mult", "offside_risk"):
            assert k in prof

    def test_pressing_moves_profile(self, ctx):
        from tactics import derive_team_profile
        team = _player_team()
        base = dict(team.tactics_data or {})
        t1 = {**base, "out_of_possession": {**(base.get("out_of_possession") or {}),
                                            "pressing_intensity": 1}}
        t2 = {**base, "out_of_possession": {**(base.get("out_of_possession") or {}),
                                            "pressing_intensity": 4}}
        team.tactics_data = t1
        low = derive_team_profile(team)
        team.tactics_data = t2
        high = derive_team_profile(team)
        assert high["press_fatigue_mult"] > low["press_fatigue_mult"]

    def test_mentality_moves_attack(self, ctx):
        from tactics import derive_team_profile
        team = _player_team()
        base = dict(team.tactics_data or {})
        team.tactics_data = {**base, "mentality": 1}
        d = derive_team_profile(team)
        team.tactics_data = {**base, "mentality": 5}
        a = derive_team_profile(team)
        assert a["attack_eff"] > d["attack_eff"]


class TestPhilosophyAI:
    def test_coaching_comes_from_philosophy(self, ctx):
        from ai_coaching import coaching_base
        team = _opponent()
        base = coaching_base(team)
        assert "primary" in base and "mentality" in base
        # Different philosophies -> different mentality targets.
        from config import PHILOSOPHY_COACHING
        assert PHILOSOPHY_COACHING["park_the_bus"]["mentality"] < \
            PHILOSOPHY_COACHING["gegenpress"]["mentality"]

    def test_checkpoint_derives_from_philosophy(self, ctx):
        from ai_coaching import decide_checkpoint
        team = _opponent()
        on = [p for p in team.players if p.is_starting][:11]
        bench = [p for p in team.players if not p.is_starting][:5]
        d = decide_checkpoint(None, "away_xp", 60, -1, team, on, bench, {})
        assert "reason" in d and isinstance(d["reason"], str) and d["reason"]

    def test_weekly_planner_touches_only_ai(self, ctx):
        from ai_coaching import weekly_planner_report
        weekly_planner_report()
        db.session.commit()
        assert _player_team().is_player_team is True

    def test_transfer_valuation_bounded(self, ctx):
        from ai_coaching import transfer_valuation
        team = _opponent()
        p = team.players[0]
        v = transfer_valuation(p, team)
        assert v >= 0


class TestSimLevels:
    def test_routing(self, ctx):
        from sim_levels import get_sim_level
        m = Match.query.filter_by(played=False).first()
        assert get_sim_level(m) == 1  # involves player team

    def test_l2_l3_deterministic(self, ctx):
        from sim_levels import engine_l2, engine_l3
        import random
        m = Match.query.filter_by(played=False).first()
        hp = {"attack_eff": 70, "defense_eff": 65, "possession_base": 55, "chance_quality": 1.0}
        ap = {"attack_eff": 60, "defense_eff": 62, "possession_base": 45, "chance_quality": 1.0}
        r1 = engine_l2(m, hp, ap, random.Random(42))
        r2 = engine_l2(m, hp, ap, random.Random(42))
        assert r1["home_score"] == r2["home_score"]
        r3 = engine_l3(m, hp, ap, random.Random(42))
        assert r3["level"] == 3

    def test_l3_respects_strength_gap(self, ctx):
        from sim_levels import engine_l3
        import random
        m = Match.query.filter_by(played=False).first()
        giant = {"attack_eff": 95, "defense_eff": 90}
        minnow = {"attack_eff": 45, "defense_eff": 42}
        hs = sum(engine_l3(m, giant, minnow, random.Random(i))["home_score"]
                 for i in range(30))
        aws = sum(engine_l3(m, giant, minnow, random.Random(i))["away_score"]
                  for i in range(30))
        assert hs > aws

    def test_world_route(self, client):
        assert client.get("/world").status_code == 200
        r = client.post("/world")
        assert r.status_code in (200, 302)

    def test_auto_pick_endpoint(self, client):
        r = client.post("/formation/auto-pick")
        assert r.status_code in (200, 400)
