"""Backend tests: models, simulation engine, services, HTTP routes."""
import json

from models import (
    db,
    GameState,
    League,
    Match,
    Message,
    Player,
    PlayerRating,
    Staff,
    Team,
    TransferOffer,
)


def _player_team():
    return Team.query.filter_by(is_player_team=True).first()


def _opponent():
    return Team.query.filter_by(is_player_team=False).first()


def _state():
    return GameState.query.first()


# ---------------------------------------------------------------- models
class TestTeamStrength:
    def test_full_xi_positive(self, ctx):
        team = _player_team()
        att, dfn = team.get_strength()
        assert att > 0 and dfn > 0

    def test_empty_squad_returns_zero(self, ctx):
        team = Team(name="Empty FC", morale=70)
        db.session.add(team)
        db.session.flush()
        assert team.get_strength() == (0.0, 0.0)

    def test_no_starters_returns_minimum(self, ctx):
        from config import MIN_LINEUP_RATING

        team = _player_team()
        for p in team.players:
            p.is_starting = False
        att, dfn = team.get_strength()
        assert att == MIN_LINEUP_RATING and dfn == MIN_LINEUP_RATING

    def test_short_squad_penalty(self, ctx):
        team = _player_team()
        full_att, _ = team.get_strength()
        for p in team.players[5:]:
            p.is_starting = False
        short_att, _ = team.get_strength()
        assert short_att < full_att

    def test_injured_excluded(self, ctx):
        team = _player_team()
        full_att, _ = team.get_strength()
        starter = next(p for p in team.players if p.is_starting)
        starter.injury_days = 10
        injured_att, _ = team.get_strength()
        assert injured_att < full_att

    def test_leader_morale_bonus(self, ctx):
        from config import LEADER_MORALE_BONUS

        team = _player_team()
        base_att, _ = team.get_strength()
        starter = next(p for p in team.players if p.is_starting)
        starter.personality = "Leader"
        bonus_att, _ = team.get_strength()
        assert bonus_att >= base_att
        assert LEADER_MORALE_BONUS > 0

    def test_staff_bonuses_log_dampened(self, ctx):
        team = _player_team()
        db.session.add(
            Staff(name="Coach", role="fitness", level=2, salary=10000,
                  attack_bonus=1.0, defense_bonus=1.0, team_id=team.id)
        )
        db.session.flush()
        bonuses = team.get_staff_bonuses()
        import math

        assert bonuses["attack"] == pytest_approx(math.log1p(2.0))

    def test_get_lineup_strength_none(self, ctx):
        from simulation import get_lineup_strength

        assert get_lineup_strength(None) == (0.0, 0.0)

    def test_player_rating_helpers(self, ctx):
        p = Player.query.filter_by(position="FWD").first()
        assert p.primary_ability("FWD") == "attack"
        assert p.primary_ability("GK") == "defense"
        assert p.rating_for() in (p.attack, p.defense)
        assert "$" in p.value_label()


def pytest_approx(x):
    class _A:
        def __eq__(self, other):
            return abs(other - x) < 1e-6

    return _A()


# ------------------------------------------------------- league table
class TestLeagueTable:
    def test_home_win(self, ctx):
        from simulation import update_league_table

        home, away = _player_team(), _opponent()
        hp, ap = home.points, away.points
        update_league_table(home, away, 2, 0)
        assert home.points == hp + 3
        assert home.wins == 1 and away.losses == 1
        assert home.goals_for >= 2

    def test_away_win(self, ctx):
        from simulation import update_league_table

        home, away = _player_team(), _opponent()
        update_league_table(home, away, 0, 3)
        assert away.points == 3 and away.wins == 1 and home.losses == 1

    def test_draw(self, ctx):
        from simulation import update_league_table

        home, away = _player_team(), _opponent()
        update_league_table(home, away, 1, 1)
        assert home.points == 1 and away.points == 1
        assert home.draws == 1 and away.draws == 1

    def test_table_sorted(self, ctx):
        from services import get_league_table, invalidate_league_cache

        home, away = _player_team(), _opponent()
        home.points = 6
        away.points = 0
        db.session.commit()
        invalidate_league_cache()
        rows = get_league_table(home.league_id)
        assert rows[0].id == home.id


# ------------------------------------------------------- match sim
class TestSimulateMatch:
    def test_quick_mode(self, ctx):
        from simulation import simulate_match

        m = Match.query.filter_by(played=False).first()
        assert simulate_match(m, mode="quick") is True
        assert m.played is True
        assert m.result and "-" in m.result

    def test_idempotency(self, ctx):
        from simulation import simulate_match

        m = Match.query.filter_by(played=False).first()
        assert simulate_match(m, mode="quick") is True
        pts = m.home_team.points
        assert simulate_match(m, mode="quick") is False
        assert m.home_team.points == pts

    def test_medium_and_deep(self, ctx):
        from simulation import simulate_match

        teams = Team.query.all()
        for mode in ("medium", "deep"):
            m = Match(home_team_id=teams[0].id, away_team_id=teams[1].id,
                      week=99, played=False)
            db.session.add(m)
            db.session.flush()
            assert simulate_match(m, mode=mode) is True
            assert m.played is True

    def test_match_director_updates_manager_record(self, ctx):
        from simulation import match_director

        state = _state()
        m = Match.query.filter_by(played=False).first()
        w, l, d = state.manager_wins, state.manager_losses, state.manager_draws
        match_director(m, state)
        assert (state.manager_wins + state.manager_losses + state.manager_draws) == w + l + d + 1

    def test_match_report_and_ratings(self, ctx):
        from simulation import get_match_report, simulate_match

        m = Match.query.filter_by(played=False).first()
        simulate_match(m, mode="deep")
        db.session.commit()
        report = get_match_report(m.id)
        assert report is not None
        assert report["match"].id == m.id
        assert get_match_report(999999) is None

    def test_top_scorers_assists_queries(self, ctx):
        from simulation import get_top_scorers, get_top_assists, simulate_match

        m = Match.query.filter_by(played=False).first()
        simulate_match(m, mode="deep")
        db.session.commit()
        league_id = _player_team().league_id
        assert isinstance(get_top_scorers(league_id), list)
        assert isinstance(get_top_assists(league_id), list)


# ------------------------------------------------------- progression
class TestProgression:
    def test_simulate_day_increments(self, ctx):
        from simulation import simulate_day

        state = _state()
        day = state.current_day
        simulate_day(state)
        assert state.current_day == day + 1

    def test_is_match_day(self, ctx):
        from config import DAYS_PER_WEEK
        from simulation import is_match_day

        state = _state()
        state.current_day = DAYS_PER_WEEK
        assert is_match_day(state) is True
        state.current_day = 1
        assert is_match_day(state) is False

    def test_simulate_calendar_counts_days(self, ctx):
        from simulation import simulate_calendar

        state = _state()
        start = state.current_day
        res = simulate_calendar(3, state, background=True)
        assert res["days_simulated"] == 3
        assert state.current_day == start + 3

    def test_simulate_calendar_survives_midrun_error(self, ctx, monkeypatch):
        """Bug 1 regression: an error on day 2 must keep day 1's progress."""
        import simulation as sim_mod
        from simulation import simulate_calendar

        state = _state()
        start = state.current_day
        real_day = sim_mod.simulate_day
        calls = {"n": 0}

        def flaky_day(state):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("boom on day 2")
            return real_day(state)

        monkeypatch.setattr(sim_mod, "simulate_day", flaky_day)
        res = simulate_calendar(3, state, background=True)
        assert res["days_simulated"] == 3
        # Day 2 was skipped safely; days 1 and 3 still advanced the counter.
        assert state.current_day == start + 2

    def test_get_current_match(self, ctx):
        from simulation import get_current_match

        match = get_current_match(_state())
        assert match is not None

    def test_update_market_values_bounds(self, ctx):
        from config import MARKET_VALUE_MAX, MARKET_VALUE_MIN
        from simulation import update_market_values

        p = Player.query.first()
        p.goals = 10
        update_market_values()
        assert MARKET_VALUE_MIN <= p.market_value <= MARKET_VALUE_MAX

    def test_cleanup_old_data(self, ctx):
        from simulation import cleanup_old_data

        db.session.add(Message(title="t", content="c", type="info", read=True, created_day=1))
        db.session.commit()
        cleanup_old_data()
        db.session.commit()
        assert Message.query.filter_by(read=True).count() == 0

    def test_train_team_monthly(self, ctx):
        from simulation import train_team_monthly

        team = _player_team()
        team.training_focus = "attack"
        train_team_monthly(team)

    def test_generate_youth_and_retire(self, ctx):
        from simulation import generate_youth_players, process_season_end

        generate_youth_players()
        state = _state()
        old = Player.query.first()
        old.age = 40
        process_season_end(state)
        db.session.commit()

    def test_promotion_relegation_runs(self, ctx):
        from simulation import process_promotion_relegation

        l2 = League(name="Second Tier", tier=2, reputation=50)
        db.session.add(l2)
        db.session.flush()
        t = Team(name="Lower FC", morale=70, budget=500000, is_player_team=False, league_id=l2.id)
        db.session.add(t)
        db.session.commit()
        process_promotion_relegation(_state())
        db.session.commit()

    def test_relegation_drops_single_division(self, ctx):
        """Relegated clubs must land in the adjacent league, never skip one.

        Regression test: the old pair-by-pair loop let SQLAlchemy autoflush
        expose just-moved (zero-point) clubs to the next pair's query, so
        Premier League bottom sides cascaded straight into League Two.
        """
        from config import PROMOTION_RELEGATION_SPOTS
        from simulation import process_promotion_relegation

        leagues = []
        for name, tier in (("L1x", 1), ("L2x", 2), ("L3x", 3)):
            l = League(name=name, tier=tier, reputation=50)
            db.session.add(l)
            db.session.flush()
            leagues.append(l)
        size = 6
        for league in leagues:
            for j in range(size):
                db.session.add(Team(
                    name=f"{league.name}-T{j}", morale=70, budget=500000,
                    is_player_team=False, league_id=league.id,
                    points=100 - j * 10, goals_for=50 - j * 5, goals_against=10,
                ))
        db.session.commit()
        pl_bottom_ids = {
            t.id for t in Team.query.filter_by(league_id=leagues[0].id)
            .order_by(Team.points.asc()).limit(PROMOTION_RELEGATION_SPOTS).all()
        }
        process_promotion_relegation(_state())
        db.session.commit()
        for team_id in pl_bottom_ids:
            assert db.session.get(Team, team_id).league_id == leagues[1].id
        for league in leagues:
            assert Team.query.filter_by(league_id=league.id).count() == size


# ------------------------------------------------------- services
class TestServices:
    def test_budget_for_tier(self, ctx):
        from config import DEFAULT_BUDGET, TIER_BUDGETS
        from services import get_budget_for_tier

        assert get_budget_for_tier(1) == TIER_BUDGETS[1]
        assert get_budget_for_tier(999) == DEFAULT_BUDGET
        assert get_budget_for_tier(None) == DEFAULT_BUDGET

    def test_club_budget_varies_with_reputation(self, ctx):
        from services import calculate_club_budget

        # New league-key API: exponential reputation curve + TV multiplier.
        low = calculate_club_budget(("Spain", 1), 65)
        high = calculate_club_budget(("Spain", 1), 95)
        # Reputation spread must differentiate clubs of the same league...
        assert high - low >= 100_000_000
        # ...while staying positive and ordered across leagues.
        for value in (low, high, calculate_club_budget(("England", 1), 90)):
            assert value > 0
        assert calculate_club_budget(("England", 1), 75) > \
            calculate_club_budget(("Italy", 3), 55)
        # Legacy positional form calculate_club_budget(tier, rep, league_rep)
        # still works (treated as an English tier).
        assert calculate_club_budget(1, 95, 90) > calculate_club_budget(1, 85, 90)
        assert calculate_club_budget(999, 50, 50) > 0

    def test_validate_formation(self, ctx):
        from services import validate_formation

        team = _player_team()
        ok, _ = validate_formation(team, {"1": {}, "2": {}})
        assert ok is True
        ok, _ = validate_formation(team, {})
        assert ok is False
        ok, _ = validate_formation(team, {str(i): {} for i in range(12)})
        assert ok is False

    def test_validate_formation_gk_rule(self, ctx):
        from services import validate_formation

        team = _player_team()
        ids = [p.id for p in team.players[:11]]
        gk = next(p for p in team.players if p.position == "GK")
        non_gk = [p for p in team.players if p.position != "GK"][:11]
        ok, msg = validate_formation(team, {str(p.id): {} for p in non_gk})
        assert ok is False and "حارس" in msg

    def test_apply_formation(self, ctx):
        from services import apply_formation

        team = _player_team()
        ids = [p.id for p in team.players[:5]]
        apply_formation(team, {str(i): {} for i in ids})
        assert sum(1 for p in team.players if p.is_starting) == 5

    def test_purchase_player(self, ctx):
        from services import purchase_player

        buyer, seller = _player_team(), _opponent()
        buyer.budget = 10_000_000
        target = next(p for p in seller.players)
        target.market_value = 100000
        ok, _ = purchase_player(buyer, target, day=1)
        assert ok is True
        assert target.team_id == buyer.id

    def test_purchase_insufficient_budget(self, ctx):
        from services import purchase_player

        buyer, seller = _player_team(), _opponent()
        buyer.budget = 1
        target = next(p for p in seller.players)
        target.market_value = 5_000_000
        ok, _ = purchase_player(buyer, target, day=1)
        assert ok is False

    def test_staff_hire_fire(self, ctx):
        from services import fire_staff_member, hire_staff_member

        team = _player_team()
        team.budget = 10_000_000
        s = Staff(name="S", role="fitness", level=1, salary=10000, team_id=None)
        db.session.add(s)
        db.session.flush()
        ok, _ = hire_staff_member(team, s)
        assert ok is True and s.team_id == team.id
        ok, _ = fire_staff_member(team, s)
        assert ok is True and s.team_id is None

    def test_offer_accept_reject(self, ctx):
        from services import process_offer_accept, process_offer_reject

        buyer, seller = _player_team(), _opponent()
        buyer.budget = 10_000_000
        target = next(p for p in seller.players)
        offer = TransferOffer(player_id=target.id, from_team_id=seller.id,
                             to_team_id=buyer.id, amount=100000,
                             status="pending", created_day=1, deadline_day=5)
        db.session.add(offer)
        db.session.flush()
        ok, _ = process_offer_accept(offer.id)
        assert ok is True
        offer2 = TransferOffer(player_id=target.id, from_team_id=seller.id,
                              to_team_id=buyer.id, amount=100000,
                              status="pending", created_day=1, deadline_day=5)
        db.session.add(offer2)
        db.session.flush()
        ok, _ = process_offer_reject(offer2.id)
        assert ok is True and offer2.status == "rejected"

    def test_calculate_team_tier(self, ctx):
        from services import calculate_team_tier

        data = calculate_team_tier(_player_team().id)
        assert "tier" in data and "rating" in data
        missing = calculate_team_tier(999999)
        assert missing["tier"] == 4

    def test_scout_display_rating(self, ctx):
        from services import get_display_rating

        team = _player_team()
        opp_player = _opponent().players[0]
        label = get_display_rating(opp_player, team)
        assert isinstance(label, str) and len(label) > 0


# ------------------------------------------------------- HTTP routes
class TestRoutes:
    def test_index_redirects_to_dashboard(self, client):
        r = client.get("/", follow_redirects=False)
        assert r.status_code in (301, 302)
        assert "/dashboard" in r.headers["Location"]

    def test_dashboard_ok(self, client):
        assert client.get("/dashboard").status_code == 200

    def test_squad_ok(self, client):
        r = client.get("/squad")
        assert r.status_code == 200
        assert "Player FC" in r.get_data(as_text=True)

    def test_formation_ok(self, client):
        assert client.get("/formation").status_code == 200

    def test_standings_ok(self, client):
        assert client.get("/standings").status_code == 200

    def test_calendar_ok(self, client):
        assert client.get("/calendar").status_code == 200

    def test_match_ok(self, client):
        assert client.get("/match").status_code == 200

    def test_stats_pages_ok(self, client):
        for url in ("/stats", "/stats/top-scorers", "/stats/top-assists"):
            assert client.get(url).status_code == 200, url

    def test_messages_and_inbox(self, client):
        assert client.get("/messages").status_code == 200
        assert client.get("/inbox_unread").status_code == 200

    def test_toggle_starter_success(self, client):
        with client.application.app_context():
            team = _player_team()
            bench = next((p for p in team.players if not p.is_starting and p.injury_days == 0), None)
            # make room: unset one starter
            if bench is None:
                starter = next(p for p in team.players if p.is_starting)
                starter.is_starting = False
                db.session.commit()
                bench = starter
            else:
                starter = next(p for p in team.players if p.is_starting)
                starter.is_starting = False
                db.session.commit()
            pid = bench.id
        r = client.post(f"/toggle_starter/{pid}")
        assert r.status_code == 200
        assert r.get_json()["success"] is True

    def test_toggle_starter_injured(self, client):
        with client.application.app_context():
            team = _player_team()
            p = team.players[0]
            p.injury_days = 5
            p.is_starting = False
            db.session.commit()
            pid = p.id
        r = client.post(f"/toggle_starter/{pid}")
        assert r.status_code == 400

    def test_toggle_starter_full_xi(self, client):
        with client.application.app_context():
            team = _player_team()
            for i, p in enumerate(team.players):
                p.is_starting = i < 11
                p.injury_days = 0
            db.session.commit()
            bench = team.players[11]
            pid = bench.id
        r = client.post(f"/toggle_starter/{pid}")
        assert r.status_code == 400

    def test_toggle_starter_get_rejected(self, client):
        # Mutations are POST-only: a GET must not toggle and must not
        # return a JSON success body (405, JSON for XHR callers).
        with client.application.app_context():
            team = _player_team()
            pid = team.players[0].id
        r = client.get(
            f"/toggle_starter/{pid}", headers={"X-Requested-With": "XMLHttpRequest"}
        )
        assert r.status_code == 405
        assert r.get_json()["error"] == "method not allowed"

    def test_save_formation_valid(self, client):
        with client.application.app_context():
            team = _player_team()
            gk = next(p for p in team.players if p.position == "GK")
            others = [p for p in team.players if p.position != "GK"][:10]
            ids = [gk.id] + [p.id for p in others]
        r = client.post("/formation/save", json={"positions": {str(i): {} for i in ids}})
        assert r.status_code == 200
        assert r.get_json()["success"] is True

    def test_save_formation_invalid(self, client):
        r = client.post("/formation/save", json={})
        assert r.status_code == 400

    def test_live_payload_exposes_sot(self, ctx):
        # Live UI renders shots as "total (on target)"; the payload must
        # carry the on-target counts the engine already tracks.
        from live_match import live_state_payload

        match = Match.query.filter_by(played=False).first()
        payload = live_state_payload(match)
        assert payload["home_sot"] == 0
        assert payload["away_sot"] == 0

    def test_advance_day(self, client):
        with client.application.app_context():
            day = _state().current_day
        r = client.post("/advance_day", follow_redirects=False)
        assert r.status_code in (200, 302)
        with client.application.app_context():
            assert _state().current_day >= day

    def test_match_report_missing_redirects(self, client):
        r = client.get("/match_report/999999", follow_redirects=False)
        assert r.status_code in (200, 302)

    def test_404_json_for_api(self, client):
        r = client.get("/api/no-such-thing", headers={"Accept": "application/json"})
        assert r.status_code == 404


# ------------------------------------------- European expansion (37 leagues)
class TestEuropeanExpansion:
    EXPECTED_KEYS = {
        ("England", 1), ("England", 2), ("England", 3), ("England", 4),
        ("Germany", 1), ("Germany", 2), ("Germany", 3),
        ("Spain", 1), ("Spain", 2),
        ("France", 1), ("France", 2),
        ("Italy", 1), ("Italy", 2), ("Italy", 3),
        ("Netherlands", 1), ("Netherlands", 2),
        ("Portugal", 1), ("Portugal", 2),
        ("Belgium", 1), ("Belgium", 2),
        ("Turkey", 1), ("Turkey", 2),
        ("Scotland", 1), ("Scotland", 2),
        ("Austria", 1), ("Switzerland", 1), ("Greece", 1),
        ("Ukraine", 1), ("Russia", 1), ("Denmark", 1),
        ("Sweden", 1), ("Norway", 1), ("Poland", 1),
        ("Croatia", 1), ("Serbia", 1), ("Czech Republic", 1),
        ("Romania", 1),
    }
    EXPECTED_SIZES = {
        ("England", 1): 20, ("England", 2): 24, ("England", 3): 24,
        ("England", 4): 24,
        ("Germany", 1): 18, ("Germany", 2): 18, ("Germany", 3): 20,
        ("Spain", 1): 20, ("Spain", 2): 22,
        ("France", 1): 18, ("France", 2): 20,
        ("Italy", 1): 20, ("Italy", 2): 20, ("Italy", 3): 20,
        ("Netherlands", 1): 18, ("Netherlands", 2): 20,
        ("Portugal", 1): 18, ("Portugal", 2): 18,
        ("Belgium", 1): 16, ("Belgium", 2): 16,
        ("Turkey", 1): 20, ("Turkey", 2): 18,
        ("Scotland", 1): 12, ("Scotland", 2): 10,
        ("Austria", 1): 12, ("Switzerland", 1): 12, ("Greece", 1): 14,
        ("Ukraine", 1): 16, ("Russia", 1): 16, ("Denmark", 1): 12,
        ("Sweden", 1): 16, ("Norway", 1): 16, ("Poland", 1): 18,
        ("Croatia", 1): 10, ("Serbia", 1): 16, ("Czech Republic", 1): 16,
        ("Romania", 1): 16,
    }

    def test_all_leagues_present(self, ctx):
        from config import EUROPEAN_TEAMS

        assert self.EXPECTED_KEYS <= set(EUROPEAN_TEAMS)
        assert len(EUROPEAN_TEAMS) == 37
        for key, size in self.EXPECTED_SIZES.items():
            assert len(EUROPEAN_TEAMS[key]) == size, key

    def test_league_names_cover_all(self, ctx):
        from config import EUROPEAN_TEAMS, LEAGUE_NAME_TO_KEY

        for name in ("Bundesliga", "La Liga", "Ligue 1", "Serie A",
                     "Eredivisie", "Primeira Liga", "Süper Lig",
                     "Scottish Premiership", "Allsvenskan"):
            assert name in LEAGUE_NAME_TO_KEY, name
        assert len(LEAGUE_NAME_TO_KEY) == 37

    def test_team_names_unique(self, ctx):
        from config import EUROPEAN_TEAMS

        all_names = [n for teams in EUROPEAN_TEAMS.values() for n in teams]
        assert len(all_names) == len(set(all_names))

    def test_real_club_names(self, ctx):
        from config import EUROPEAN_TEAMS

        all_names = {n for teams in EUROPEAN_TEAMS.values() for n in teams}
        for name in ("Real Madrid", "Bayern München", "Paris Saint-Germain",
                     "Inter", "Ajax", "Benfica", "Galatasaray", "Celtic",
                     "Manchester City", "Barcelona", "Liverpool"):
            assert name in all_names, name

    def test_rating_bands_cover_all_leagues(self, ctx):
        from config import EUROPEAN_TEAMS, LEAGUE_RATING_BANDS

        assert self.EXPECTED_KEYS <= set(LEAGUE_RATING_BANDS)
        for key in self.EXPECTED_KEYS:
            floor, ceiling = LEAGUE_RATING_BANDS[key]
            assert 30 <= floor < ceiling <= 99, key
        floors = [fl for fl, _ in LEAGUE_RATING_BANDS.values()]
        assert min(floors) == 47

    def test_generator_stays_inside_band(self, ctx):
        from procedural import generate_player_stats_v2

        for pos in ("GK", "DEF", "MID", "FWD"):
            for _ in range(50):
                stats = generate_player_stats_v2(54, 78, pos, "Europe")
                primary = (stats["attack"] if pos in ("FWD", "MID")
                           else stats["defense"])
                assert 54 <= primary <= 78, (pos, stats)
                assert 54 <= stats["attack"] <= 78
                assert 54 <= stats["defense"] <= 78

    def test_global_rating_range_anchors(self, ctx):
        from config import LEAGUE_RATING_BANDS, SEED_STAR_PLAYERS

        mbappe = next(p for p in SEED_STAR_PLAYERS
                      if p["name"] == "Kylian Mbappé")
        assert mbappe["attack"] == 91
        assert mbappe["club"] == "Real Madrid"
        assert min(fl for fl, _ in LEAGUE_RATING_BANDS.values()) == 47

    def test_mbappe_seeded(self, ctx):
        from config import EUROPEAN_TEAMS, SEED_STAR_PLAYERS

        mbappe = next(p for p in SEED_STAR_PLAYERS
                      if p["name"] == "Kylian Mbappé")
        assert mbappe is not None
        assert mbappe["attack"] == 91
        assert mbappe["club"] in EUROPEAN_TEAMS[("Spain", 1)]

    def test_yamal_seeded(self, ctx):
        from config import SEED_STAR_PLAYERS

        yamal = next(p for p in SEED_STAR_PLAYERS
                     if p["name"] == "Lamine Yamal")
        assert yamal is not None
        assert yamal["attack"] >= 85
        assert yamal["age"] <= 20

    def test_star_clubs_exist(self, ctx):
        from config import EUROPEAN_TEAMS, SEED_STAR_PLAYERS

        all_names = {n for teams in EUROPEAN_TEAMS.values() for n in teams}
        for star in SEED_STAR_PLAYERS:
            assert star["club"] in all_names, star

    def test_league_model_has_country_fields(self, ctx):
        league = League.query.first()
        assert hasattr(league, "country")
        assert hasattr(league, "rating_floor")
        assert hasattr(league, "rating_ceiling")
        assert hasattr(league, "is_continental")

    def test_schema_columns_present(self, ctx):
        from sqlalchemy import text as sql_text

        cols = [r[1] for r in db.session.execute(
            sql_text("PRAGMA table_info(league)"))]
        for col in ("country", "rating_floor", "rating_ceiling",
                    "season_start_week", "is_continental"):
            assert col in cols, col


class TestBudgetDistribution:
    def test_league_budget_gap(self, ctx):
        from services import calculate_club_budget

        real = calculate_club_budget(("Spain", 1), 95)
        almeria = calculate_club_budget(("Spain", 1), 58)
        assert real - almeria >= 100_000_000  # >=100M gap

    def test_premier_league_richer_than_serie_c(self, ctx):
        from services import calculate_club_budget

        pl_mid = calculate_club_budget(("England", 1), 75)
        sc_mid = calculate_club_budget(("Italy", 3), 55)
        assert pl_mid > sc_mid * 10

    def test_eredivisie_richer_than_sweden(self, ctx):
        from services import calculate_club_budget

        nl = calculate_club_budget(("Netherlands", 1), 70)
        se = calculate_club_budget(("Sweden", 1), 70)
        assert nl > se

    def test_budget_deterministic_for_same_inputs(self, ctx, monkeypatch):
        import random as _r

        from services import calculate_club_budget

        monkeypatch.setattr(_r, "uniform", lambda a, b: 1.0)
        a = calculate_club_budget(("England", 1), 90)
        b = calculate_club_budget(("England", 1), 90)
        assert a == b


class TestMarketValueScaling:
    def test_mbappe_value(self, ctx):
        from services import calculate_market_value

        v = calculate_market_value(91, 60, 93, 25, 95)
        assert 170_000_000 <= v <= 210_000_000

    def test_yamal_value(self, ctx):
        from services import calculate_market_value

        v = calculate_market_value(88, 55, 95, 17, 90)
        assert 200_000_000 <= v <= 240_000_000

    def test_lower_league_value(self, ctx):
        from services import calculate_market_value

        v = calculate_market_value(52, 50, 55, 25, 40)
        assert 200_000 <= v <= 500_000

    def test_star_purchase_premium(self, ctx):
        from services import get_asking_price

        class _P:
            pass

        star, mid, low = _P(), _P(), _P()
        star.market_value, mid.market_value, low.market_value = (
            180_000_000, 60_000_000, 5_000_000)
        assert get_asking_price(star) == int(180_000_000 * 1.15)
        assert get_asking_price(mid) == int(60_000_000 * 1.10)
        assert get_asking_price(low) == 5_000_000


class TestPromotionRelegationPerCountry:
    def _build_pyramids(self):
        leagues = {}
        for country, tiers in (("Germany", (1, 2, 3)), ("England", (1, 2))):
            for tier in tiers:
                lg = League(name=f"{country}-T{tier}", tier=tier,
                            reputation=60, country=country)
                db.session.add(lg)
                db.session.flush()
                leagues[(country, tier)] = lg
        size = 6
        for (country, tier), league in leagues.items():
            for j in range(size):
                db.session.add(Team(
                    name=f"{country}-T{tier}-C{j}", morale=70,
                    budget=500000, is_player_team=False,
                    league_id=league.id,
                    points=100 - j * 10, goals_for=50 - j * 5,
                    goals_against=10,
                ))
        db.session.commit()
        return leagues

    def test_germany_promotes_within_germany(self, ctx):
        from config import PROMOTION_RELEGATION_SPOTS
        from simulation import process_promotion_relegation

        leagues = self._build_pyramids()
        g3 = leagues[("Germany", 3)]
        g2 = leagues[("Germany", 2)]
        promoted_ids = {
            t.id for t in Team.query.filter_by(league_id=g3.id)
            .order_by(Team.points.desc()).limit(PROMOTION_RELEGATION_SPOTS).all()
        }
        process_promotion_relegation(_state())
        db.session.commit()
        for team_id in promoted_ids:
            assert db.session.get(Team, team_id).league_id == g2.id
        # No German club may land in an English league.
        english_ids = {leagues[("England", 1)].id,
                       leagues[("England", 2)].id}
        for team_id in promoted_ids:
            assert db.session.get(Team, team_id).league_id not in english_ids

    def test_england_stays_in_england(self, ctx):
        from config import PROMOTION_RELEGATION_SPOTS
        from simulation import process_promotion_relegation

        leagues = self._build_pyramids()
        e1 = leagues[("England", 1)]
        e2 = leagues[("England", 2)]
        relegated_ids = {
            t.id for t in Team.query.filter_by(league_id=e1.id)
            .order_by(Team.points.asc()).limit(PROMOTION_RELEGATION_SPOTS).all()
        }
        process_promotion_relegation(_state())
        db.session.commit()
        for team_id in relegated_ids:
            assert db.session.get(Team, team_id).league_id == e2.id


# ---------------------------------------------------------------- cups
class TestCupCompetitions:
    def _two_team_cup(self):
        from cup_services import create_tournament, seed_knockout
        team, opp = _player_team(), _opponent()
        t = create_tournament("fa_cup", 1, country="England")
        seed_knockout(t, [team.id, opp.id], "2026-08-01")
        db.session.commit()
        return t, team, opp

    def test_draw_engine_creates_week_zero_fixtures(self, ctx):
        from config import CUP_MATCH_WEEK
        from models import TournamentFixture
        t, team, opp = self._two_team_cup()
        fixtures = TournamentFixture.query.filter_by(tournament_id=t.id).all()
        assert len(fixtures) == 1
        m = fixtures[0].match
        assert m.week == CUP_MATCH_WEEK
        assert {m.home_team_id, m.away_team_id} == {team.id, opp.id}

    def test_cup_winner_guaranteed_on_draw(self, ctx):
        from cup_services import process_cup_match_winner
        team, opp = _player_team(), _opponent()
        m = Match(home_team_id=team.id, away_team_id=opp.id, week=0,
                  played=True, home_score=1, away_score=1)
        db.session.add(m)
        db.session.commit()
        for _ in range(10):
            assert process_cup_match_winner(m) in (team.id, opp.id)

    def test_cup_result_leaves_league_table_untouched(self, ctx):
        from simulation import simulate_match
        t, team, opp = self._two_team_cup()
        m = t.fixtures[0].match
        simulate_match(m, _state(), mode="deep")
        db.session.commit()
        assert m.played is True
        assert db.session.get(Team, team.id).points == 0
        assert db.session.get(Team, opp.id).points == 0

    def test_knockout_completes_with_champion_and_trophy(self, ctx):
        from cup_services import after_match_played, play_due_cup_fixtures
        from models import Tournament
        t, team, opp = self._two_team_cup()
        state = _state()
        state.current_date = "2027-01-01"
        before = state.trophies or 0
        res = play_due_cup_fixtures(state, background=True)
        db.session.commit()
        assert res["played"] == 1
        t = db.session.get(Tournament, t.id)
        assert t.is_active is False
        assert t.winner_team_id in (team.id, opp.id)
        if t.winner_team_id == team.id:
            assert (state.trophies or 0) == before + 1

    def test_after_match_played_idempotent(self, ctx):
        from cup_services import after_match_played
        t, team, opp = self._two_team_cup()
        m = t.fixtures[0].match
        from simulation import simulate_match
        simulate_match(m, _state(), mode="quick")
        db.session.commit()
        first = after_match_played(m, _state())
        db.session.commit()
        assert first == after_match_played(m, _state())

    def test_continental_standings_updated(self, ctx):
        from cup_services import (create_tournament,
                                  seed_continental_league_phase,
                                  continental_table)
        from models import ContinentalStandings
        team, opp = _player_team(), _opponent()
        t = create_tournament("champions_league", 1)
        seed_continental_league_phase(t, [team.id, opp.id], "2026-08-01")
        db.session.commit()
        assert ContinentalStandings.query.filter_by(tournament_id=t.id).count() == 2
        table = continental_table(t.id)
        assert {r.team_id for r in table} == {team.id, opp.id}

    def test_cups_page_and_dashboard_strip(self, client):
        self._two_team_cup()
        r = client.get("/cups")
        assert r.status_code == 200
        text = r.get_data(as_text=True)
        assert "<html" in text and len(text.strip()) > 500
        dash = client.get("/dashboard").get_data(as_text=True)
        assert "/cups" in dash  # nav + dashboard strip link

    def test_calendar_carries_competition_badge(self, client):
        self._two_team_cup()
        text = client.get("/calendar").get_data(as_text=True)
        assert "comp_color" in text
        assert "competition" in text
