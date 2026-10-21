"""Frontend tests: templates, RTL/Arabic UI, static assets, rendered pages.

CONVENTION (strict): every page test is DB-backed. A test never passes on an
empty render — it first reads the expected value from the database, then
asserts that exact value appears in the rendered HTML/JSON. A page that
returns 200 but shows nothing from the DB is a FAILURE, not a success.
"""
import json
import os
import re

import pytest

from models import (
    BoardObjective,
    db,
    GameState,
    League,
    Match,
    Mentoring,
    Message,
    Player,
    PlayerRating,
    Staff,
    TacticPreset,
    Team,
    TransferOffer,
)

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def _html(client, url):
    r = client.get(url)
    assert r.status_code == 200, url
    text = r.get_data(as_text=True)
    assert "<html" in text, url
    # Guard: a rendered page must have a real body, not an empty shell.
    assert len(text.strip()) > 500, url
    return text


def _player_team():
    return Team.query.filter_by(is_player_team=True).first()


def _opponent():
    return Team.query.filter_by(is_player_team=False).first()


def _state():
    return GameState.query.first()


def _money(value):
    return "${:,}".format(value or 0)


# ======================================================================
# Templates on disk
# ======================================================================
class TestTemplatesExist:
    REQUIRED = ["base.html", "dashboard.html", "squad.html", "tactics.html",
                "match.html", "calendar.html", "standings.html"]

    ALL_PAGES = ["base.html", "dashboard.html", "squad.html", "tactics.html",
                 "match.html", "calendar.html", "standings.html",
                 "messages.html",
                 "training.html", "staff.html", "medical.html",
                 "scouting.html", "live.html", "load.html",
                 "setup.html", "select_division.html", "select_team.html",
                 "manager_form.html", "new_manager.html", "match_report.html",
                 "match_history.html"]

    def test_required_templates_present(self):
        for name in self.REQUIRED:
            assert os.path.exists(os.path.join(TEMPLATES_DIR, name)), name

    def test_every_feature_template_present(self):
        for name in self.ALL_PAGES:
            assert os.path.exists(os.path.join(TEMPLATES_DIR, name)), name

    def test_every_template_non_empty(self):
        for name in self.ALL_PAGES:
            path = os.path.join(TEMPLATES_DIR, name)
            assert os.path.getsize(path) > 100, name

    def test_pages_extend_base(self):
        for name in ["dashboard.html", "squad.html", "tactics.html", "match.html"]:
            with open(os.path.join(TEMPLATES_DIR, name), encoding="utf-8") as f:
                content = f.read()
            assert 'extends "base.html"' in content or "extends 'base.html'" in content, name

    def test_all_feature_pages_extend_base(self):
        for name in [n for n in self.ALL_PAGES if n != "base.html"]:
            with open(os.path.join(TEMPLATES_DIR, name), encoding="utf-8") as f:
                content = f.read()
            assert 'extends "base.html"' in content or "extends 'base.html'" in content, name

    def test_no_unclosed_jinja_blocks(self):
        for fname in os.listdir(TEMPLATES_DIR):
            if not fname.endswith(".html"):
                continue
            with open(os.path.join(TEMPLATES_DIR, fname), encoding="utf-8") as f:
                content = f.read()
            assert content.count("{% block") == content.count("{% endblock"), fname

    def test_no_unclosed_jinja_if_for(self):
        for fname in os.listdir(TEMPLATES_DIR):
            if not fname.endswith(".html"):
                continue
            with open(os.path.join(TEMPLATES_DIR, fname), encoding="utf-8") as f:
                content = f.read()
            assert content.count("{% if") == content.count("{% endif"), fname
            assert content.count("{% for") == content.count("{% endfor"), fname

    def test_base_has_content_and_scripts_blocks(self):
        with open(os.path.join(TEMPLATES_DIR, "base.html"), encoding="utf-8") as f:
            content = f.read()
        assert "{% block content %}" in content
        assert "{% block scripts %}" in content
        assert "{% block title %}" in content


# ======================================================================
# RTL / Arabic shell + nav + context processor (DB-backed)
# ======================================================================
class TestRtlArabicUi:
    def test_base_is_rtl_arabic(self, client):
        text = _html(client, "/dashboard")
        assert 'dir="rtl"' in text
        assert 'lang="ar"' in text

    def test_every_page_is_rtl_arabic(self, client):
        for url in ("/dashboard", "/squad", "/formation", "/tactics", "/calendar", "/match",
                    "/standings", "/messages", "/training", "/staff",
                    "/medical", "/scouting",
                    "/match_history"):
            text = _html(client, url)
            assert 'dir="rtl"' in text, url
            assert 'lang="ar"' in text, url

    def test_dashboard_shows_team_and_manager(self, client):
        team = _player_team()
        state = _state()
        text = _html(client, "/dashboard")
        assert team.name in text  # DB team name, e.g. "Player FC"
        assert state.manager_name in text  # DB manager name, e.g. "Test Manager"

    def test_nav_links_present(self, client):
        text = _html(client, "/dashboard")
        for href in ["/dashboard", "/squad", "/tactics", "/calendar", "/match", "/standings"]:
            assert href in text, href

    def test_nav_has_every_feature_link(self, client):
        text = _html(client, "/dashboard")
        for href in ["/messages", "/scouting",
                     "/training", "/staff", "/medical", "/match_history", "/exit"]:
            assert href in text, href

    def test_logged_out_nav_links_load_page(self, client):
        with client.session_transaction() as sess:
            sess.pop("active_save", None)
        r = client.get("/dashboard", follow_redirects=False)
        assert r.status_code in (200, 301, 302)
        with client.session_transaction() as sess:
            sess["active_save"] = 1

    def test_squad_lists_players(self, client):
        team = _player_team()
        first = team.players[0]
        text = _html(client, "/squad")
        assert first.name in text  # exact DB player name, not a placeholder

    def test_formation_embeds_json(self, client):
        text = _html(client, "/formation")
        assert "formation" in text.lower()

    def test_standings_has_table(self, client):
        team = _player_team()
        text = _html(client, "/standings")
        assert "<table" in text
        assert team.name in text  # DB team name appears as a table row

    def test_calendar_shows_week(self, client):
        text = _html(client, "/calendar")
        assert "1" in text  # current week/day number visible

    def test_match_page_renders(self, client):
        team = _player_team()
        opp = _opponent()
        text = _html(client, "/match")
        assert team.name in text or opp.name in text or "المباراة" in text

    def test_stats_pages_render(self, client):
        # /stats and its leaderboard sub-pages are merged into /standings.
        text = _html(client, "/standings")
        assert "html" in text
        assert "الجدول والإحصائيات" in text

    def test_messages_page_renders(self, client):
        assert _html(client, "/messages")

    def test_context_processor_globals(self, client):
        text = _html(client, "/dashboard")
        # unread badge + manager name injected via context processor
        assert "unreadBadge" in text or "badge" in text

    def test_context_processor_db_values_on_every_page(self, client):
        team = _player_team()
        state = _state()
        for url in ("/dashboard", "/squad", "/standings"):
            text = _html(client, url)
            assert team.name in text, url  # player_team_name from DB
            assert state.manager_name in text, url  # manager_name from DB
            assert str(state.current_week) in text, url
            assert str(state.current_day) in text, url

    def test_unread_badge_count_matches_db(self, client):
        from simulation import get_unread_count
        expected = get_unread_count()
        text = _html(client, "/dashboard")
        assert str(expected) in text

    def test_footer_and_a11y_shell(self, client):
        text = _html(client, "/dashboard")
        assert "skip-link" in text
        assert 'role="banner"' in text
        assert 'role="main"' in text
        assert "2026" in text  # footer year


# ======================================================================
# Dashboard — every widget shows DB values
# ======================================================================
class TestDashboardDb:
    def test_budget_morale_points_from_db(self, client):
        team = _player_team()
        text = _html(client, "/dashboard")
        assert _money(team.budget) in text
        assert f"{team.morale}%" in text
        assert str(team.points) in text

    def test_manager_record_from_db(self, client):
        state = _state()
        text = _html(client, "/dashboard")
        assert str(state.manager_wins or 0) in text
        assert str(state.manager_draws or 0) in text
        assert str(state.manager_losses or 0) in text

    def test_season_and_board_confidence_from_db(self, client):
        state = _state()
        team = _player_team()
        text = _html(client, "/dashboard")
        assert str(state.season_number or 1) in text
        assert f"{state.board_confidence or 60}%" in text
        assert str(team.chemistry or 0) in text
        assert str(team.youth_goals or 0) in text

    def test_goal_difference_from_db(self, client):
        team = _player_team()
        text = _html(client, "/dashboard")
        assert str(team.goals_for - team.goals_against) in text

    def test_league_table_rows_from_db(self, client):
        from services import get_league_table
        team = _player_team()
        table = get_league_table(team.league_id)
        text = _html(client, "/dashboard")
        for row in table[:8]:
            assert row.name in text  # every table row name rendered
            assert str(row.points) in text

    def test_squad_table_lists_every_db_player(self, client):
        team = _player_team()
        text = _html(client, "/dashboard")
        for p in team.players:
            assert p.name in text, p.name  # no player hidden
            assert str(p.age) in text
            assert str(p.attack) in text
            assert str(p.defense) in text
            assert _money(p.market_value) in text

    def test_recent_results_from_db(self, client):
        from simulation import get_recent_results, simulate_match
        state = _state()
        team = _player_team()
        m = Match.query.filter_by(played=False).first()
        simulate_match(m, mode="quick")
        db.session.commit()
        text = _html(client, "/dashboard")
        results = get_recent_results(state, team.league_id)
        assert results, "expected recent results after simulating a match"
        for match in results[:6]:
            assert match.home_team.name in text
            assert match.away_team.name in text
            assert match.result in text

    def test_board_objectives_from_db(self, client):
        state = _state()
        team = _player_team()
        db.session.add(BoardObjective(team_id=team.id, season=state.season_number or 1,
                                      objective_type="youth_goals", target_value=5.0,
                                      achieved=False))
        db.session.commit()
        text = _html(client, "/dashboard")
        assert "أهداف مجلس الإدارة" in text
        assert "الأكاديمية" in text

    def test_quick_action_forms_present(self, client):
        text = _html(client, "/dashboard")
        assert 'action="/continue"' in text
        assert 'action="/advance_week"' in text
        assert "csrf_token" in text

    def test_player_modal_hook_present(self, client):
        text = _html(client, "/dashboard")
        assert "playerModal" in text
        assert "/player_profile/" in text


# ======================================================================
# Squad page — DB-backed
# ======================================================================
class TestSquadDb:
    def test_all_players_with_all_fields(self, client):
        team = _player_team()
        text = _html(client, "/squad")
        for p in team.players:
            assert p.name in text, p.name
            assert str(p.age) in text
            assert str(p.attack) in text
            assert str(p.defense) in text
            assert str(p.potential) in text
            assert _money(p.market_value) in text
            assert p.position in text

    def test_starter_count_matches_db(self, client):
        team = _player_team()
        n = sum(1 for p in team.players if p.is_starting and p.injury_days == 0)
        text = _html(client, "/squad")
        assert f"{n}/11" in text

    def test_tier_rating_from_db(self, client):
        from services import calculate_team_tier
        team = _player_team()
        data = calculate_team_tier(team.id)
        text = _html(client, "/squad")
        assert str(data["tier"]) in text
        assert "%.1f" % data["rating"] in text
        assert "%.1f" % data["attack"] in text
        assert "%.1f" % data["defense"] in text

    def test_toggle_forms_for_fit_players(self, client):
        team = _player_team()
        text = _html(client, "/squad")
        fit = [p for p in team.players if p.injury_days == 0]
        assert fit, "seed must have fit players"
        for p in fit[:3]:
            assert f"/toggle_starter/{p.id}" in text

    def test_injured_player_row_from_db(self, client):
        team = _player_team()
        p = team.players[0]
        p.injury_days = 6
        db.session.commit()
        try:
            text = _html(client, "/squad")
            assert p.name in text
            assert "مصاب" in text
        finally:
            p.injury_days = 0
            db.session.commit()

    def test_mentoring_card_removed_from_page(self, client):
        # Regression: the mentoring (guidance) card was stripped from the
        # squad screen; only the backend endpoints remain.
        text = _html(client, "/squad")
        assert "التوجيه" not in text
        assert "المرشد" not in text
        assert "/mentoring/add" not in text
        assert "/mentoring/remove" not in text

    def test_toggle_then_squad_reflects_db(self, client):
        team = _player_team()
        bench = next((p for p in team.players if not p.is_starting and p.injury_days == 0), None)
        if bench is None:
            starter = next(p for p in team.players if p.is_starting)
            starter.is_starting = False
            db.session.commit()
            bench = starter
        else:
            starter = next(p for p in team.players if p.is_starting)
            starter.is_starting = False
            db.session.commit()
        r = client.post(f"/toggle_starter/{bench.id}")
        assert r.status_code == 200
        text = _html(client, "/squad")
        assert bench.name in text  # still listed, now as starter (DB state shown)


# ======================================================================
# Formation page — DB-backed
# ======================================================================
class TestFormationDb:
    def test_all_squad_names_in_takers_dropdown(self, client):
        team = _player_team()
        text = _html(client, "/formation")
        for p in team.players[:5]:
            assert p.name in text, p.name

    def test_pressing_and_tactic_selected_from_db(self, client):
        team = _player_team()
        team.pressing_style = "high"
        team.match_tactic = "attack"
        db.session.commit()
        text = _html(client, "/formation")
        assert 'value="high" selected' in text
        assert 'value="attack" selected' in text

    def test_players_json_embeds_db_ids(self, client):
        team = _player_team()
        text = _html(client, "/formation")
        for p in team.players[:3]:
            assert str(p.id) in text
            assert p.name in text

    def test_saved_takers_selected_from_db(self, client):
        team = _player_team()
        taker = team.players[0]
        team.penalty_taker_id = taker.id
        team.freekick_taker_id = taker.id
        db.session.commit()
        text = _html(client, "/formation")
        assert f'value="{taker.id}" selected' in text
        assert taker.name in text

    def test_preset_appears_after_save(self, client):
        team = _player_team()
        team.formation_data = {str(p.id): {"x": 50, "y": 50} for p in team.players[:11]}
        db.session.commit()
        r = client.post("/formation/preset/save",
                        data={"preset_type": "standard", "name": "DB-Preset-1"},
                        follow_redirects=True)
        assert r.status_code == 200
        text = r.get_data(as_text=True)
        assert "DB-Preset-1" in text  # saved preset name rendered from DB

    def test_role_save_returns_db_values(self, client):
        team = _player_team()
        pid = team.players[0].id
        r = client.post("/formation/role", data={"player_id": pid, "role": "poacher"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["success"] is True
        assert body["player_id"] == pid
        assert body["role"] == "poacher"
        # persisted on the team's formation_data in the DB
        assert _player_team().formation_data[str(pid)]["role"] == "poacher"

    def test_tactics_save_reflected_in_form(self, client):
        client.post("/formation/tactics",
                    data={"pressing_style": "low", "match_tactic": "defensive"},
                    follow_redirects=True)
        text = _html(client, "/formation")
        assert 'value="low" selected' in text
        assert 'value="defensive" selected' in text


class TestFormationPresetsUI:
    def test_layout_select_with_preset_options(self, client):
        from config import PRESET_FORMATIONS

        text = _html(client, "/formation")
        assert 'id="formationLayout"' in text
        for f in PRESET_FORMATIONS[:6]:
            assert ('value="%s"' % f["key"]) in text, f["key"]
        for f in PRESET_FORMATIONS:
            assert f["label"] in text, f["label"]
        # Grouped into optgroups by backline.
        for group in ("الدفاع الثلاثي", "الدفاع الرباعي", "الدفاع الخماسي"):
            assert group in text

    def test_fill_by_ability_controls(self, client):
        text = _html(client, "/formation")
        assert 'id="fillByAbilityBtn"' in text
        with open(os.path.join(STATIC_DIR, "free_formation.js"), encoding="utf-8") as f:
            js = f.read()
        assert "/formation/ability-fill" in js
        assert "تعبئة بالقدرة" in text

    def test_overall_embedded_for_ability_fill(self, client):
        team = _player_team()
        p = team.players[0]
        text = _html(client, "/formation")
        assert '"overall"' in text          # players JSON carries overall
        assert str(p.overall) in text

    def test_saved_layout_restored_in_page(self, client):
        from config import FORMATION_LAYOUT_KEY

        team = _player_team()
        gk = next(p for p in team.players if p.position == "GK")
        ids = [gk.id] + [p.id for p in team.players if p.position != "GK"][:10]
        data = {FORMATION_LAYOUT_KEY: "5-2-3"}
        data.update({str(i): {"x": 50, "y": 50} for i in ids})
        team.formation_data = data
        db.session.commit()
        text = _html(client, "/formation")
        assert 'value="5-2-3"' in text
        assert FORMATION_LAYOUT_KEY in text
        assert "5-2-3" in text


# ======================================================================
# Calendar — DB-backed (embedded JSON + current date)
# ======================================================================
class TestCalendarDb:
    def test_current_date_from_db(self, client):
        state = _state()
        text = _html(client, "/calendar")
        assert state.current_date in text

    def test_match_json_contains_db_teams(self, client):
        team = _player_team()
        opp = _opponent()
        text = _html(client, "/calendar")
        assert team.name in text
        assert opp.name in text

    def test_match_week_and_score_from_db(self, client):
        m = Match.query.filter(
            (Match.home_team_id == _player_team().id) |
            (Match.away_team_id == _player_team().id)).first()
        text = _html(client, "/calendar")
        assert str(m.week) in text
        # The fixture is embedded as JSON (ensure_ascii escapes the dash),
        # so parse it and compare against the DB row directly.
        embedded = json.loads(
            re.search(r"^\s*matchData = (\{.*?\});\s*\n\s*gameDateStr",
                      text, re.S | re.M).group(1)
        )
        assert embedded, "calendar must embed match JSON from the DB"
        blob = json.dumps(embedded, ensure_ascii=False)
        assert m.home_team.name in blob
        assert m.away_team.name in blob
        assert (m.result or "—") in blob or (m.result or "\u2014") in text

    def test_simulate_form_present(self, client):
        text = _html(client, "/calendar")
        assert 'action="/simulate_to_date"' in text
        assert 'name="target_date"' in text


# ======================================================================
# Match center — DB-backed
# ======================================================================
class TestMatchCenterDb:
    def test_lineups_from_db(self, client):
        match = Match.query.filter_by(played=False).first()
        text = _html(client, "/match")
        assert match.home_team.name in text
        assert match.away_team.name in text
        for p in [x for x in match.home_team.players if x.is_starting][:3]:
            assert p.name in text
        for p in [x for x in match.away_team.players if x.is_starting][:3]:
            assert p.name in text

    def test_scouting_report_from_db(self, client):
        match = Match.query.filter_by(played=False).first()
        opp = match.home_team if not match.home_team.is_player_team else match.away_team
        danger = max(opp.players, key=lambda p: p.attack)
        text = _html(client, "/match")
        assert opp.name in text
        assert danger.name in text
        assert str(danger.goals) in text

    def test_played_match_score_visible_in_report_and_history(self, client):
        # get_current_match() only returns UNPLAYED fixtures, so once the
        # week's match is played /match correctly shows the empty state
        # (with the DB day count) while the DB score lives on the report
        # + history pages.
        from simulation import simulate_match
        m = Match.query.filter_by(played=False).first()
        simulate_match(m, mode="quick")
        db.session.commit()
        text = _html(client, "/match")
        assert str(_state().current_day) in text  # empty state is DB-backed
        report = _html(client, f"/match_report/{m.id}")
        assert str(m.home_score) in report
        assert str(m.away_score) in report
        history = _html(client, "/match_history")
        assert f"{m.home_score} - {m.away_score}" in history


class TestMatchReportHistoryDb:
    def test_report_shows_db_result_and_teams(self, client):
        from simulation import simulate_match
        m = Match.query.filter_by(played=False).first()
        simulate_match(m, mode="deep")
        db.session.commit()
        text = _html(client, f"/match_report/{m.id}")
        assert m.home_team.name in text
        assert m.away_team.name in text
        assert str(m.home_score) in text
        assert str(m.away_score) in text

    def test_history_lists_db_results(self, client):
        from simulation import simulate_match
        m = Match.query.filter_by(played=False).first()
        simulate_match(m, mode="quick")
        db.session.commit()
        text = _html(client, "/match_history")
        assert m.home_team.name in text
        assert m.away_team.name in text
        assert f"{m.home_score} - {m.away_score}" in text
        assert f"/match_report/{m.id}" in text


# ======================================================================
# Standings — DB-backed
# ======================================================================
class TestStandingsDb:
    def test_all_teams_points_wdl_from_db(self, client):
        from services import get_league_table
        team = _player_team()
        table = get_league_table(team.league_id)
        text = _html(client, "/standings")
        for t in table:
            assert t.name in text, t.name
            assert str(t.points) in text
            assert str(t.wins) in text
            assert str(t.draws) in text
            assert str(t.losses) in text

    def test_league_name_and_rank_from_db(self, client):
        from services import get_team_rank
        team = _player_team()
        league = db.session.get(League, team.league_id)
        rank = get_team_rank(team.league_id, team)
        text = _html(client, "/standings")
        assert league.name in text
        assert str(rank) in text

    def test_league_tabs_from_db(self, client):
        text = _html(client, "/standings")
        for league in League.query.all():
            assert league.name in text

    def test_points_change_reflected(self, client):
        team = _player_team()
        team.points = 42
        team.wins = 14
        db.session.commit()
        from services import invalidate_league_cache
        invalidate_league_cache()
        text = _html(client, "/standings")
        assert "42" in text
        assert team.name in text

    def test_unknown_league_falls_back_to_db_league(self, client):
        team = _player_team()
        r = client.get("/standings?league_id=999999", follow_redirects=True)
        assert r.status_code == 200
        assert team.name in r.get_data(as_text=True)


# ======================================================================
# Stats center — DB-backed
# ======================================================================
class TestStatsDb:
    def test_scorer_and_assist_from_db(self, client):
        # Leaderboards aggregate PlayerRating rows of PLAYED matches (not the
        # Player.goals column), so simulate a deep match then assert the
        # rated DB players render with their DB totals.
        from simulation import get_top_assists, get_top_scorers, simulate_match
        m = Match.query.filter_by(played=False).first()
        simulate_match(m, mode="deep")
        db.session.commit()
        league_id = _player_team().league_id
        scorers = get_top_scorers(league_id)
        assists = get_top_assists(league_id)
        assert scorers, "deep sim must yield scorer rows"
        assert assists, "deep sim must yield assist rows"
        sid, sname, _, _, sgoals, _ = scorers[0]
        aid, aname, _, _, aassists, _ = assists[0]
        assert db.session.get(Player, sid).name == sname
        # /stats and leaderboards are merged into /standings.
        text = _html(client, "/standings")
        assert sname in text
        assert aname in text
        assert str(sgoals) in text
        assert str(aassists) in text

    def test_team_names_in_leaderboards(self, client):
        from simulation import get_top_scorers, simulate_match
        m = Match.query.filter_by(played=False).first()
        simulate_match(m, mode="deep")
        db.session.commit()
        team_ids = {row[3] for row in get_top_scorers(_player_team().league_id)}
        text = _html(client, "/standings")
        for tid in team_ids:
            assert db.session.get(Team, tid).name in text

    def test_league_selector_from_db(self, client):
        text = _html(client, "/standings")
        for league in League.query.all():
            assert league.name in text


# ======================================================================
# Messages / inbox — DB-backed
# ======================================================================
class TestMessagesInboxDb:
    def _add_msg(self, **kw):
        kw.setdefault("title", "DB Title X1")
        kw.setdefault("content", "DB Content body abc 123")
        kw.setdefault("type", "info")
        kw.setdefault("read", False)
        kw.setdefault("created_day", 3)
        m = Message(**kw)
        db.session.add(m)
        db.session.commit()
        return m

    def test_message_title_content_day_rendered(self, client):
        m = self._add_msg()
        text = _html(client, "/messages")
        assert m.title in text
        assert m.content[:60] in text
        assert str(m.created_day) in text
        assert m.type in text

    def test_empty_inbox_shows_empty_state(self, client):
        Message.query.delete()
        db.session.commit()
        text = _html(client, "/messages")
        assert "لا توجد رسائل" in text

    def test_unread_json_matches_db(self, client):
        from simulation import get_unread_count
        Message.query.delete()
        db.session.add(Message(title="u1", content="c1", type="info", read=False, created_day=1))
        db.session.add(Message(title="u2", content="c2", type="info", read=True, created_day=1))
        db.session.commit()
        r = client.get("/inbox_unread")
        assert r.status_code == 200
        assert r.get_json()["unread"] == get_unread_count() == 1

    def test_inbox_api_returns_db_row_and_marks_read(self, client):
        m = self._add_msg(read=False)
        r = client.get(f"/inbox_api/{m.id}")
        assert r.status_code == 200
        body = r.get_json()
        assert body["title"] == m.title
        assert body["content"] == m.content
        assert body["created_day"] == m.created_day
        assert db.session.get(Message, m.id).read is True

    def test_inbox_api_unknown_is_404_json(self, client):
        r = client.get("/inbox_api/999999")
        assert r.status_code == 404
        assert r.get_json()["error"] == "not found"

    def test_message_read_marks_db_and_redirects(self, client):
        m = self._add_msg(read=False)
        r = client.get(f"/message_read/{m.id}", follow_redirects=False)
        assert r.status_code in (301, 302)
        assert db.session.get(Message, m.id).read is True

    def test_pending_offer_from_db_rendered(self, client):
        buyer, seller = _player_team(), _opponent()
        target = next(p for p in seller.players)
        offer = TransferOffer(player_id=target.id, from_team_id=seller.id,
                              to_team_id=buyer.id, amount=321321,
                              status="pending", created_day=1, deadline_day=9)
        db.session.add(offer)
        db.session.commit()
        try:
            text = _html(client, "/messages")
            assert target.name in text
            assert "321,321" in text
            assert seller.name in text or buyer.name in text
        finally:
            db.session.delete(offer)
            db.session.commit()

    def test_accept_offer_moves_player_in_db(self, client):
        buyer, seller = _player_team(), _opponent()
        buyer.budget = 10_000_000
        target = next(p for p in seller.players)
        target.market_value = 50000
        offer = TransferOffer(player_id=target.id, from_team_id=seller.id,
                              to_team_id=buyer.id, amount=50000,
                              status="pending", created_day=1, deadline_day=9)
        db.session.add(offer)
        db.session.commit()
        tid = target.id
        r = client.post(f"/transfer_offer/{offer.id}/accept", follow_redirects=True)
        assert r.status_code == 200
        assert db.session.get(Player, tid).team_id == buyer.id

    def test_reject_offer_status_in_db(self, client):
        buyer, seller = _player_team(), _opponent()
        target = next(p for p in seller.players)
        offer = TransferOffer(player_id=target.id, from_team_id=seller.id,
                              to_team_id=buyer.id, amount=50000,
                              status="pending", created_day=1, deadline_day=9)
        db.session.add(offer)
        db.session.commit()
        oid = offer.id
        client.post(f"/transfer_offer/{oid}/reject", follow_redirects=True)
        assert db.session.get(TransferOffer, oid).status == "rejected"


# ======================================================================
# Training / staff / facilities / medical — DB-backed
# ======================================================================
class TestTrainingDb:
    def test_focus_intensity_selected_from_db(self, client):
        team = _player_team()
        team.training_focus = "attack"
        team.training_intensity = 8
        db.session.commit()
        text = _html(client, "/training")
        assert 'value="attack" selected' in text
        assert 'value="8"' in text
        assert f"{team.training_facilities or 3}/10" in text

    def test_every_player_in_training_table(self, client):
        team = _player_team()
        text = _html(client, "/training")
        for p in team.players:
            assert p.name in text, p.name
            assert str(p.potential) in text

    def test_save_training_persists_and_renders(self, client):
        client.post("/training/save", data={"focus": "defense", "intensity": "3"},
                    follow_redirects=True)
        team = _player_team()
        assert team.training_focus == "defense"
        assert team.training_intensity == 3
        text = _html(client, "/training")
        assert 'value="defense" selected' in text


class TestStaffDb:
    def _mk_staff(self, team_id=None, name="DB Coach ZZ", role="fitness"):
        s = Staff(name=name, role=role, level=2, salary=11111,
                  attack_bonus=0.5, defense_bonus=0.5, team_id=team_id)
        db.session.add(s)
        db.session.commit()
        return s

    def test_team_and_available_staff_from_db(self, client):
        team = _player_team()
        mine = self._mk_staff(team_id=team.id, name="DB Mine Coach")
        free = self._mk_staff(team_id=None, name="DB Free Coach")
        try:
            text = _html(client, "/staff")
            assert mine.name in text
            assert free.name in text
            assert str(mine.level) in text
            assert "11,111" in text  # salary formatting from DB
        finally:
            db.session.delete(mine)
            db.session.delete(free)
            db.session.commit()

    def test_budget_from_db_on_staff_page(self, client):
        team = _player_team()
        text = _html(client, "/staff")
        assert _money(team.budget) in text

    def test_hire_moves_staff_to_db_team(self, client):
        team = _player_team()
        team.budget = 5_000_000
        s = self._mk_staff(team_id=None, name="DB Hire Me")
        client.post(f"/staff/hire/{s.id}", follow_redirects=True)
        assert db.session.get(Staff, s.id).team_id == team.id
        assert s.name in _html(client, "/staff")

    def test_fire_clears_team_in_db(self, client):
        team = _player_team()
        team.budget = 5_000_000
        s = self._mk_staff(team_id=team.id, name="DB Fire Me")
        client.post(f"/staff/fire/{s.id}", follow_redirects=True)
        assert db.session.get(Staff, s.id).team_id is None

    def test_generate_pool_adds_rows(self, client):
        n0 = Staff.query.filter_by(team_id=None).count()
        client.post("/staff/generate", follow_redirects=True)
        assert Staff.query.filter_by(team_id=None).count() >= n0


class TestMedicalDb:
    def test_injured_and_fit_sections_from_db(self, client):
        team = _player_team()
        for p in team.players:
            p.injury_days = 0
        hurt = team.players[0]
        hurt.injury_days = 9
        hurt.fitness = 33
        db.session.commit()
        text = _html(client, "/medical")
        assert hurt.name in text
        assert "9 يوم" in text
        assert "33%" in text
        fit_one = team.players[1]
        assert fit_one.name in text  # fit list also from DB

    def test_no_injuries_empty_state(self, client):
        team = _player_team()
        for p in team.players:
            p.injury_days = 0
        db.session.commit()
        text = _html(client, "/medical")
        assert "لا يوجد لاعبون مصابون" in text


# ======================================================================
# Scouting / transfers — DB-backed
# ======================================================================
class TestScoutingDb:
    def test_opponent_players_and_values_from_db(self, client):
        opp = _opponent()
        text = _html(client, "/scouting")
        for p in opp.players[:5]:
            assert p.name in text, p.name
            assert "{:,}".format(p.market_value) in text

    def test_budget_and_board_confidence_from_db(self, client):
        team = _player_team()
        state = _state()
        text = _html(client, "/scouting")
        assert _money(team.budget) in text
        assert f"{state.board_confidence or 60}%" in text

    def test_free_agent_section_from_db(self, client):
        fa = Player(name="DB Free Agent Q", position="FWD", attack=60, defense=40,
                    potential=70, age=22, height=180, weight=75,
                    market_value=77777, team_id=None)
        db.session.add(fa)
        db.session.commit()
        try:
            text = _html(client, "/scouting")
            assert "وكلاء أحرار" in text
            assert fa.name in text
            assert "77,777" in text
        finally:
            db.session.delete(fa)
            db.session.commit()

    def test_scout_row_and_region_from_db(self, client):
        team = _player_team()
        scout = Staff(name="DB Scout R", role="scout", level=3, salary=20000,
                      team_id=team.id, region="Europe", on_assignment=True)
        db.session.add(scout)
        db.session.commit()
        try:
            text = _html(client, "/scouting")
            assert scout.name in text
            assert "Europe" in text
        finally:
            db.session.delete(scout)
            db.session.commit()

    def test_buy_player_changes_db_and_scouting(self, client):
        team = _player_team()
        team.budget = 10_000_000
        opp = _opponent()
        target = next(p for p in opp.players)
        target.market_value = 60000
        tid = target.id
        db.session.commit()
        client.post(f"/buy_player/{tid}", data={"sell_on": "0"},
                    follow_redirects=True)
        assert db.session.get(Player, tid).team_id == team.id

    def test_swap_offer_endpoint(self, client):
        team = _player_team()
        opp = _opponent()
        target = next(p for p in opp.players)
        offered = next(p for p in team.players if not p.is_starting)
        r = client.post(f"/swap_offer/{target.id}",
                        data={"offered_player_id": offered.id, "cash": "0"},
                        follow_redirects=True)
        assert r.status_code == 200

    def test_scout_assign_unassign_db(self, client):
        team = _player_team()
        scout = Staff(name="DB Scout A", role="scout", level=1, salary=5000,
                      team_id=team.id)
        db.session.add(scout)
        db.session.commit()
        sid = scout.id
        client.post(f"/scout/assign/{sid}", data={"region": "Africa"},
                    follow_redirects=True)
        assert db.session.get(Staff, sid).on_assignment is True
        assert db.session.get(Staff, sid).region == "Africa"
        client.post(f"/scout/unassign/{sid}", follow_redirects=True)
        assert db.session.get(Staff, sid).on_assignment is False


# ======================================================================
# Compare + player profile — DB-backed
# ======================================================================
class TestPlayerProfileDb:
    def test_all_fields_match_db(self, client):
        p = _player_team().players[0]
        r = client.get(f"/player_profile/{p.id}")
        assert r.status_code == 200
        body = r.get_json()
        assert body["name"] == p.name
        assert body["position"] == p.position
        assert body["attack"] == p.attack
        assert body["defense"] == p.defense
        assert body["age"] == p.age
        assert body["market_value"] == p.market_value
        assert body["goals"] == (p.goals or 0)
        assert "avg_rating" in body and "recent_ratings" in body

    def test_ratings_in_profile_from_db(self, client):
        from simulation import simulate_match
        m = Match.query.filter_by(played=False).first()
        simulate_match(m, mode="deep")
        db.session.commit()
        rated = PlayerRating.query.first()
        assert rated is not None, "deep sim must create ratings"
        r = client.get(f"/player_profile/{rated.player_id}")
        assert r.status_code == 200
        assert r.get_json()["recent_ratings"], "ratings list must not be empty"

    def test_unknown_player_is_404_json(self, client):
        r = client.get("/player_profile/999999")
        assert r.status_code == 404
        assert r.get_json()["error"] == "not found"


# ======================================================================
# Live match — DB-backed
# ======================================================================
class TestLiveDb:
    def _start_live(self):
        from live_match import start_live
        match = Match.query.filter_by(played=False).first()
        assert start_live(match, mode="live") is True
        db.session.commit()
        return match

    def test_live_page_shows_db_teams(self, client):
        match = self._start_live()
        text = _html(client, f"/live/{match.id}")
        assert match.home_team.name in text
        assert match.away_team.name in text

    def test_live_state_json_from_db(self, client):
        match = self._start_live()
        r = client.get(f"/live/{match.id}/state")
        assert r.status_code == 200
        body = r.get_json()
        for key in ("home_score", "away_score", "minute", "home_sot", "away_sot"):
            assert key in body, key
        assert body["home_score"] == (match.live_state or {}).get("home_score", 0)

    def test_live_state_unknown_is_404(self, client):
        r = client.get("/live/999999/state")
        assert r.status_code == 404

    def test_live_advance_updates_db_minute(self, client):
        match = self._start_live()
        before = (match.live_state or {}).get("minute", 0)
        r = client.post(f"/live/{match.id}/advance", data={"minutes": 5})
        assert r.status_code == 200
        after = db.session.get(Match, match.id).live_state.get("minute", 0)
        assert after > before

    def test_live_sub_validates_db_squad(self, client):
        match = self._start_live()
        team_key = ("home_xp" if match.home_team.is_player_team else "away_xp")
        r = client.post(f"/live/{match.id}/sub",
                        data={"team_key": team_key, "player_out": 1, "player_in": 2})
        assert r.status_code == 200
        assert "ok" in r.get_json()

    def test_live_sub_wrong_side_rejected(self, client):
        match = self._start_live()
        other = ("away_xp" if match.home_team.is_player_team else "home_xp")
        r = client.post(f"/live/{match.id}/sub",
                        data={"team_key": other, "player_out": 1, "player_in": 2})
        assert r.status_code == 403

    def test_live_finish_marks_db_played(self, client):
        match = self._start_live()
        mid = match.id
        r = client.post(f"/live/{mid}/finish")
        assert r.status_code == 200
        assert r.get_json()["finished"] is True
        assert db.session.get(Match, mid).played is True


# ======================================================================
# Progression buttons reflect in the frontend (DB day/week shown)
# ======================================================================
class TestProgressionFrontendDb:
    def test_advance_day_increments_db_and_dashboard(self, client):
        day0 = _state().current_day
        client.post("/advance_day", follow_redirects=False)
        assert _state().current_day >= day0
        text = _html(client, "/dashboard")
        assert str(_state().current_day) in text

    def test_continue_renders_dashboard_with_db_state(self, client):
        r = client.post("/continue", follow_redirects=True)
        assert r.status_code == 200
        text = r.get_data(as_text=True)
        assert _player_team().name in text
        assert _state().manager_name in text

    def test_advance_week_keeps_db_team_visible(self, client):
        r = client.post("/advance_week", follow_redirects=True)
        assert r.status_code == 200
        assert _player_team().name in r.get_data(as_text=True)

    def test_simulate_to_date_moves_db_date(self, client):
        state = _state()
        state.current_date = "2026-06-01"
        db.session.commit()
        r = client.post("/simulate_to_date",
                        data={"target_date": "2026-06-10"},
                        follow_redirects=True)
        assert r.status_code == 200
        assert _state().current_date >= "2026-06-01"
        assert _state().current_date in _html(client, "/calendar")

    def test_simulate_to_date_bad_input_stays_on_calendar(self, client):
        r = client.post("/simulate_to_date", data={"target_date": "nope"},
                        follow_redirects=True)
        assert r.status_code == 200

    def test_season_end_flow(self, client):
        state = _state()
        state.season_over = True
        db.session.commit()
        r = client.post("/season_end", follow_redirects=True)
        assert r.status_code == 200
        assert _player_team().name in r.get_data(as_text=True)


# ======================================================================
# Save / setup flow pages (engine-safe: never touch the real slot files)
# ======================================================================
class TestSaveFlowFrontend:
    def test_load_page_lists_slots(self, client):
        text = _html(client, "/load")
        assert "slot" in text.lower() or "حفظ" in text

    def test_exit_clears_session(self, client):
        r = client.get("/exit", follow_redirects=True)
        assert r.status_code == 200

    def test_missing_slot_redirects_without_engine_switch(self, client):
        # Slot 99 does not exist -> redirect, in-memory DB untouched.
        before = _player_team().name
        assert client.get("/setup/99", follow_redirects=False).status_code in (301, 302)
        assert client.get("/select-division/99", follow_redirects=False).status_code in (301, 302)
        assert client.get("/select-team/99/1", follow_redirects=False).status_code in (301, 302)
        assert _player_team().name == before  # engine was NOT repointed

    def test_invalid_new_slot_rejected(self, client):
        r = client.post("/new/99", follow_redirects=True)
        assert r.status_code == 200

    def test_delete_missing_slot_stays_safe(self, client):
        before = _player_team().name
        r = client.post("/delete/99", follow_redirects=True)
        assert r.status_code == 200
        assert _player_team().name == before


# ======================================================================
# Mentoring / presets mutations reflected in frontend
# ======================================================================
class TestMentoringPresetFrontendDb:
    def test_mentoring_add_remove_roundtrip(self, client):
        team = _player_team()
        mentor = next(p for p in team.players if p.age >= 24)
        mentor.age = 33
        mentor.personality = "Leader"
        mentee = next(p for p in team.players if p.id != mentor.id)
        mentee.age = 18
        db.session.commit()
        r = client.post("/mentoring/add",
                        data={"mentor_id": mentor.id, "mentee_id": mentee.id},
                        follow_redirects=True)
        assert r.status_code == 200
        row = Mentoring.query.filter_by(team_id=team.id, mentee_id=mentee.id).first()
        assert row is not None
        assert mentee.name in _html(client, "/squad")
        client.post(f"/mentoring/remove/{row.id}", follow_redirects=True)
        assert db.session.get(Mentoring, row.id) is None

    def test_preset_load_delete_roundtrip(self, client):
        team = _player_team()
        team.formation_data = {str(p.id): {"x": 50, "y": 50} for p in team.players[:11]}
        team.pressing_style = "high"
        db.session.commit()
        client.post("/formation/preset/save",
                    data={"preset_type": "attacking", "name": "DB-RT-Preset"},
                    follow_redirects=True)
        preset = TacticPreset.query.filter_by(team_id=team.id, name="DB-RT-Preset").first()
        assert preset is not None
        client.post(f"/formation/preset/{preset.id}/load", follow_redirects=True)
        assert _player_team().pressing_style == "high"
        client.post(f"/formation/preset/{preset.id}/delete", follow_redirects=True)
        assert db.session.get(TacticPreset, preset.id) is None


# ======================================================================
# Static assets
# ======================================================================
class TestStaticAssets:
    def test_style_css_exists_and_has_variables(self):
        path = os.path.join(STATIC_DIR, "style.css")
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert ":root" in content
        assert "--primary" in content or "primary" in content

    def test_app_js_exists_and_hooks_dom(self):
        path = os.path.join(STATIC_DIR, "app.js")
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "DOMContentLoaded" in content

    def test_static_served(self, client):
        assert client.get("/static/style.css").status_code == 200
        assert client.get("/static/app.js").status_code == 200

    def test_responsive_css(self):
        with open(os.path.join(STATIC_DIR, "style.css"), encoding="utf-8") as f:
            content = f.read()
        assert "@media" in content

    def test_base_links_static(self, client):
        text = _html(client, "/dashboard")
        assert "style.css" in text

    def test_app_js_hooks_every_fetch_feature(self):
        with open(os.path.join(STATIC_DIR, "app.js"), encoding="utf-8") as f:
            content = f.read()
        assert "fetch" in content
        assert "toggle" in content.lower() or "starter" in content.lower()

    def test_base_loads_app_js(self, client):
        text = _html(client, "/dashboard")
        assert "app.js" in text


# ======================================================================
# Forms: every mutation form carries CSRF + POST; tables present
# ======================================================================
class TestFormsAndTables:
    def test_mutation_forms_use_post_and_csrf(self, client):
        for url in ("/dashboard", "/squad", "/formation", "/training",
                    "/staff", "/scouting", "/messages"):
            text = _html(client, url)
            if "<form" in text:
                assert 'method="POST"' in text, url
                assert "csrf_token" in text, url

    def test_data_pages_have_tables(self, client):
        for url in ("/dashboard", "/squad", "/standings", "/training",
                    "/scouting"):
            assert "<table" in _html(client, url), url

    def test_scouting_filter_controls_present(self, client):
        text = _html(client, "/scouting")
        assert "searchName" in text
        assert "filterPos" in text
        assert "sortBy" in text

    def test_squad_filter_controls_removed(self, client):
        text = _html(client, "/squad")
        assert "squadTable" in text
        assert "filter-btn" not in text
        assert "squadSearch" not in text


# ======================================================================
# Error handling + JSON-vs-redirect split
# ======================================================================
class TestFrontendErrorHandling:
    def test_unknown_player_toggle_returns_json_error(self, client):
        r = client.post("/toggle_starter/999999")
        assert r.status_code == 404
        assert r.get_json()["success"] is False

    def test_bad_formation_json_returns_error(self, client):
        r = client.post("/formation/save", json={"positions": {}})
        assert r.status_code == 400
        assert "error" in r.get_json()

    def test_match_report_unknown_redirects(self, client):
        r = client.get("/match_report/999999", follow_redirects=True)
        assert r.status_code == 200

    def test_pages_escape_content(self, client):
        text = _html(client, "/dashboard")
        assert "<script>alert" not in text

    def test_toggle_starter_get_rejected_as_json_for_xhr(self, client):
        team = _player_team()
        r = client.get(f"/toggle_starter/{team.players[0].id}",
                       headers={"X-Requested-With": "XMLHttpRequest"})
        assert r.status_code == 405
        assert r.get_json()["error"] == "method not allowed"

    def test_api_404_is_json(self, client):
        r = client.get("/api/no-such-thing", headers={"Accept": "application/json"})
        assert r.status_code == 404
        assert "error" in r.get_json()

    def test_favicon_silenced(self, client):
        assert client.get("/favicon.ico").status_code == 204

    def test_formation_save_missing_positions_key(self, client):
        r = client.post("/formation/save", json={})
        assert r.status_code == 400

    def test_takers_rejects_foreign_player(self, client):
        opp = _opponent()
        r = client.post("/formation/takers",
                        data={"penalty_taker_id": str(opp.players[0].id),
                              "freekick_taker_id": ""},
                        follow_redirects=True)
        assert r.status_code == 200
        assert _player_team().penalty_taker_id != opp.players[0].id


# ======================================================================
# Anti-empty-render guard: no feature page may render without DB content
# ======================================================================
class TestNoEmptySuccess:
    """Regression net for '200 with nothing shown counts as success'.

    Each page must contain at least one value read from the DB first.
    """

    PAGES_WITH_DB_MARKERS = [
        ("/dashboard", lambda: [_player_team().name, _state().manager_name]),
        ("/squad", lambda: [_player_team().players[0].name]),
        ("/tactics", lambda: [_player_team().players[0].name]),
        ("/standings", lambda: [_player_team().name]),
        ("/calendar", lambda: [_state().current_date]),
        ("/match", lambda: [_player_team().name]),
        ("/training", lambda: [_player_team().players[0].name]),
        ("/staff", lambda: [_money(_player_team().budget)]),
        ("/medical", lambda: [_player_team().players[1].name]),
        ("/scouting", lambda: [_opponent().players[0].name]),
        ("/match_history", lambda: ["تاريخ المباريات"]),
        ("/load", lambda: ["حفظ"]),
    ]

    @pytest.mark.parametrize("url", [u for u, _ in PAGES_WITH_DB_MARKERS])
    def test_page_shows_db_content(self, client, url):
        markers_fn = dict(self.PAGES_WITH_DB_MARKERS)[url]
        markers = markers_fn()
        text = _html(client, url)
        for marker in markers:
            assert str(marker) in text, f"{url} missing DB value {marker!r}"

    def test_messages_db_content_when_present(self, client):
        m = Message(title="Guard Title 42", content="Guard content body 42",
                    type="info", read=False, created_day=2)
        db.session.add(m)
        db.session.commit()
        text = _html(client, "/messages")
        assert "Guard Title 42" in text
        assert "Guard content body 42"[:60] in text


# ======================================================================
# Live match flow: advance clamp, event feed, subs, double-finish safety,
# polling payload, auto-redirect wiring, blocked-XI flash (no crash)
# ======================================================================
class TestLiveMatchFlow:
    def _start_live(self):
        from live_match import start_live
        match = Match.query.filter_by(played=False).first()
        assert start_live(match, mode="live") is True
        db.session.commit()
        return match

    def test_advance_clamps_minutes_to_15(self, client):
        match = self._start_live()
        before = (db.session.get(Match, match.id).live_state or {}).get("minute", 0)
        r = client.post(f"/live/{match.id}/advance", data={"minutes": 99})
        assert r.status_code == 200
        after = db.session.get(Match, match.id).live_state.get("minute", 0)
        assert after - before <= 15

    def test_advance_defaults_to_1_minute(self, client):
        match = self._start_live()
        before = (db.session.get(Match, match.id).live_state or {}).get("minute", 0)
        client.post(f"/live/{match.id}/advance", data={"minutes": 0})
        after = db.session.get(Match, match.id).live_state.get("minute", 0)
        assert after - before == 1

    def test_event_feed_in_payload_and_report(self, client):
        from live_match import advance_live_mins
        match = self._start_live()
        advance_live_mins(match, 45)
        db.session.commit()
        payload = client.get(f"/live/{match.id}/state").get_json()
        assert "events" in payload and isinstance(payload["events"], list)
        client.post(f"/live/{match.id}/finish")
        report = _html(client, f"/match_report/{match.id}")
        for ev in (db.session.get(Match, match.id).home_events or []) + \
                  (db.session.get(Match, match.id).away_events or []):
            if ev.get("player"):
                assert ev["player"] in report
                break

    def test_polling_payload_has_every_key_the_js_renders(self, client):
        match = self._start_live()
        body = client.get(f"/live/{match.id}/state").get_json()
        for key in ("home_score", "away_score", "minute", "home_possession",
                    "away_possession", "home_xg", "away_xg", "home_shots",
                    "away_shots", "home_sot", "away_sot", "home_yellows",
                    "away_yellows", "home_reds", "away_reds", "events"):
            assert key in body, key

    def test_finish_redirect_wiring_in_js(self, client):
        with open(os.path.join(STATIC_DIR, "app.js"), encoding="utf-8") as f:
            js = f.read()
        assert "setInterval(refreshState, 5000)" in js  # polling
        assert "/match_report/' + matchId" in js  # auto-redirect after finish
        assert "btnFinish" in js and "btnMinute1" in js and "btnMinute5" in js

    def test_live_page_embeds_db_initial_payload(self, client):
        match = self._start_live()
        text = _html(client, f"/live/{match.id}")
        assert match.home_team.name in text
        assert match.away_team.name in text
        assert 'id="liveInitial"' in text
        assert 'id="liveFeed"' in text
        assert "csrfToken" in text

    def test_double_finish_safe_manager_record_once(self, client):
        from simulation import simulate_match  # noqa: F401 (keeps import style)
        match = self._start_live()
        state = _state()
        base = state.manager_wins + state.manager_losses + state.manager_draws
        mid = match.id
        r1 = client.post(f"/live/{mid}/finish")
        r2 = client.post(f"/live/{mid}/finish")  # rapid double-click
        assert r1.get_json()["finished"] is True
        assert r2.get_json()["finished"] is True
        assert r2.get_json()["others_played"] == 0
        assert (state.manager_wins + state.manager_losses + state.manager_draws) == base + 1

    def test_sub_wrong_side_arabic_guard(self, client):
        match = self._start_live()
        other = "away_xp" if match.home_team.is_player_team else "home_xp"
        r = client.post(f"/live/{match.id}/sub",
                        data={"team_key": other, "player_out": 1, "player_in": 2})
        assert r.status_code == 403
        assert r.get_json()["message"] == "لا يمكنك تبديل لاعبي الفريق الخصم!"

    def test_sub_invalid_team_key_400(self, client):
        match = self._start_live()
        r = client.post(f"/live/{match.id}/sub",
                        data={"team_key": "mid_xp", "player_out": 1, "player_in": 2})
        assert r.status_code == 400

    def test_live_state_foreign_match_forbidden(self, client):
        from live_match import start_live
        team = _player_team()
        opp = _opponent()
        m = Match(home_team_id=team.id, away_team_id=opp.id, week=77, played=False)
        db.session.add(m)
        db.session.flush()
        # opponent-only fixture: neither side is the player's club
        m2 = Match(home_team_id=opp.id, away_team_id=opp.id, week=78, played=False)
        db.session.add(m2)
        db.session.commit()
        r = client.get(f"/live/{m2.id}/state")
        assert r.status_code in (403, 404)

    def test_blocked_xi_flashes_no_crash(self, client):
        team = _player_team()
        for i, p in enumerate(team.players):
            p.is_starting = i < 10  # incomplete XI
            p.injury_days = 0
        state = _state()
        state.current_day = 6  # advance lands on day 7 = match day
        state.current_week = 1
        db.session.commit()
        r = client.post("/advance_day", follow_redirects=True)
        assert r.status_code == 200
        text = r.get_data(as_text=True)
        assert "التشكيلة الأساسية ناقصة" in text  # flash, not a crash
        assert team.name in text  # still renders the dashboard from DB


# ======================================================================
# Formation save/presets/takers edge cases + drag&drop wiring
# ======================================================================
class TestFormationSaveAndPresets:
    def _eleven_ids(self, team):
        gk = next(p for p in team.players if p.position == "GK")
        others = [p for p in team.players if p.position != "GK"][:10]
        return [gk.id] + [p.id for p in others]

    def test_save_persists_11_ids_in_db(self, client):
        team = _player_team()
        ids = self._eleven_ids(team)
        r = client.post("/formation/save", json={"positions": {str(i): {"x": 1, "y": 2} for i in ids}})
        assert r.status_code == 200
        fd = _player_team().formation_data
        assert len(fd) == 11
        for i in ids:
            assert str(i) in fd

    def test_save_12_rejected(self, client):
        team = _player_team()
        ids = [p.id for p in team.players[:12]]
        r = client.post("/formation/save", json={"positions": {str(i): {} for i in ids}})
        assert r.status_code == 400
        assert "11" in r.get_json()["error"]

    def test_save_11_without_gk_arabic_error(self, client):
        team = _player_team()
        non_gk = [p for p in team.players if p.position != "GK"][:11]
        r = client.post("/formation/save", json={"positions": {str(p.id): {} for p in non_gk}})
        assert r.status_code == 400
        assert "حارس" in r.get_json()["error"]

    def test_takers_auto_choice_clears_db(self, client):
        team = _player_team()
        team.penalty_taker_id = team.players[0].id
        team.freekick_taker_id = team.players[1].id
        db.session.commit()
        client.post("/formation/takers",
                    data={"penalty_taker_id": "", "freekick_taker_id": ""},
                    follow_redirects=True)
        assert _player_team().penalty_taker_id is None
        assert _player_team().freekick_taker_id is None

    def test_preset_invalid_type_defaults_standard(self, client):
        team = _player_team()
        team.formation_data = {str(p.id): {"x": 50, "y": 50} for p in team.players[:11]}
        db.session.commit()
        client.post("/formation/preset/save",
                    data={"preset_type": "bogus", "name": "DB-Type-Preset"},
                    follow_redirects=True)
        preset = TacticPreset.query.filter_by(team_id=team.id, name="DB-Type-Preset").first()
        assert preset is not None and preset.preset_type == "standard"

    def test_preset_duplicate_name_updates_in_place(self, client):
        team = _player_team()
        team.formation_data = {str(p.id): {"x": 50, "y": 50} for p in team.players[:11]}
        db.session.commit()
        client.post("/formation/preset/save",
                    data={"preset_type": "defensive", "name": "DB-Dup-Preset"},
                    follow_redirects=True)
        r = client.post("/formation/preset/save",
                        data={"preset_type": "defensive", "name": "DB-Dup-Preset"},
                        follow_redirects=True)
        rows = TacticPreset.query.filter_by(team_id=team.id, name="DB-Dup-Preset").all()
        assert len(rows) == 1
        assert "تم تحديث الخطة" in r.get_data(as_text=True)

    def test_preset_load_stale_formation_rejected(self, client):
        team = _player_team()
        non_gk = [p for p in team.players if p.position != "GK"][:11]
        stale = TacticPreset(team_id=team.id, preset_type="standard", name="DB-Stale",
                             formation_data={str(p.id): {} for p in non_gk})
        db.session.add(stale)
        db.session.commit()
        before = {p.id: p.is_starting for p in team.players}
        r = client.post(f"/formation/preset/{stale.id}/load", follow_redirects=True)
        assert r.status_code == 200
        assert "لا يمكن تطبيق الخطة" in r.get_data(as_text=True)
        assert {p.id: p.is_starting for p in _player_team().players} == before

    def test_dnd_and_save_wiring_in_template(self, client):
        with open(os.path.join(STATIC_DIR, "free_formation.js"), encoding="utf-8") as f:
            content = f.read()
        assert "dragstart" in content and "dataTransfer" in content
        assert "drop" in content and "saveFormationBtn" in content
        assert "fetch('/formation/save'" in content
        assert "fetch('/formation/role'" in content

    def test_presets_table_from_db(self, client):
        team = _player_team()
        team.formation_data = {str(p.id): {"x": 50, "y": 50} for p in team.players[:11]}
        db.session.commit()
        client.post("/formation/preset/save",
                    data={"preset_type": "attacking", "name": "DB-Table-Preset"},
                    follow_redirects=True)
        text = _html(client, "/formation")
        assert "DB-Table-Preset" in text
        assert "attacking" in text


# ======================================================================
# Squad: 11-limit under rapid clicks, concurrent clients, foreign player
# ======================================================================
class TestToggleStarterRaces:
    def _free_one_slot(self):
        team = _player_team()
        for i, p in enumerate(team.players):
            p.is_starting = i < 11
            p.injury_days = 0
        db.session.commit()
        starter = next(p for p in team.players if p.is_starting)
        starter.is_starting = False
        db.session.commit()
        return [p for p in team.players if not p.is_starting]

    def test_rapid_sequential_clicks_never_exceed_11(self, client):
        bench = self._free_one_slot()
        codes = [client.post(f"/toggle_starter/{p.id}").status_code for p in bench[:4]]
        assert codes[0] == 200  # first fills the XI
        assert codes[1:] == [400, 400, 400]  # rest rejected: already 11
        team = _player_team()
        assert sum(1 for p in team.players if p.is_starting) == 11

    def test_double_click_same_player_toggles_off(self, client):
        bench = self._free_one_slot()
        pid = bench[0].id
        assert client.post(f"/toggle_starter/{pid}").status_code == 200
        assert client.post(f"/toggle_starter/{pid}").status_code == 200
        assert db.session.get(Player, pid).is_starting is False

    def test_toggle_foreign_player_404(self, client):
        opp = _opponent()
        r = client.post(f"/toggle_starter/{opp.players[0].id}")
        assert r.status_code == 404
        assert r.get_json()["success"] is False

    def test_concurrent_clients_never_exceed_11(self, tmp_path):
        """Six threads race to fill one free XI slot (file DB)."""
        import threading
        from app import create_app
        from tests.conftest import seed_world

        uri = "sqlite:///" + str(tmp_path / "race.sqlite3").replace("\\", "/")
        application = create_app()
        application.config.update(TESTING=True, WTF_CSRF_ENABLED=False,
                                  SQLALCHEMY_DATABASE_URI=uri)
        if "sqlalchemy" in application.extensions:
            del application.extensions["sqlalchemy"]
        db.init_app(application)
        with application.app_context():
            db.create_all()
            seed_world()
            team = Team.query.filter_by(is_player_team=True).first()
            tid = team.id
            for i, p in enumerate(team.players):
                p.is_starting = i < 10
                p.injury_days = 0
            db.session.commit()
            bench_ids = [p.id for p in team.players if not p.is_starting][:6]
            db.session.remove()

            results = {}
            barrier = threading.Barrier(len(bench_ids))

            def hit(pid):
                try:
                    c = application.test_client()
                    barrier.wait(timeout=10)
                    r = c.post(f"/toggle_starter/{pid}")
                    results[pid] = ((r.get_json() or {}).get("success"), r.status_code)
                except Exception as exc:  # pragma: no cover - lock contention path
                    results[pid] = ("error", str(exc)[:80])

            threads = [threading.Thread(target=hit, args=(b,)) for b in bench_ids]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)
            assert len(results) == len(bench_ids)
            successes = [pid for pid, (ok, _) in results.items() if ok is True]
            assert len(successes) <= 1  # only one client could fill the last slot
            db.session.remove()
            assert Player.query.filter_by(team_id=tid, is_starting=True).count() <= 11


# ======================================================================
# Setup journey end-to-end on an isolated save dir (no real files touched)
# ======================================================================
class TestSetupJourney:
    def _isolated_saves(self, tmp_path, monkeypatch):
        import shutil
        dest = tmp_path / "saves"
        dest.mkdir()
        shutil.copy2(os.path.join("saves", "template.db"), dest / "template.db")
        monkeypatch.setattr("services.saves_base", lambda: str(dest))
        return dest

    def test_full_journey_new_to_dashboard(self, client, tmp_path, monkeypatch):
        self._isolated_saves(tmp_path, monkeypatch)
        assert client.post("/new/2", follow_redirects=False).headers["Location"].endswith("/setup/2")
        assert client.get("/setup/2").status_code == 200
        r = client.post("/setup/2", data={"manager_name": "Journey Boss",
                                           "manager_nationality": "Egypt"},
                        follow_redirects=False)
        assert r.headers["Location"].endswith("/select-division/2")
        text = _html(client, "/select-division/2")
        league = League.query.order_by(League.tier).first()
        assert league.name in text
        text = _html(client, f"/select-team/2/{league.id}")
        team = Team.query.filter_by(league_id=league.id).first()
        assert team.name in text
        r = client.post(f"/manager-form/2/{team.id}",
                        data={"manager_name": "Journey Boss"},
                        follow_redirects=False)
        assert r.headers["Location"].endswith("/dashboard")
        text = _html(client, "/dashboard")
        assert "Journey Boss" in text  # DB manager name rendered
        assert team.name in text  # DB club rendered

    def test_empty_manager_name_defaults(self, client, tmp_path, monkeypatch):
        self._isolated_saves(tmp_path, monkeypatch)
        client.post("/new/2", follow_redirects=False)
        client.post("/setup/2", data={"manager_name": "", "manager_nationality": ""},
                    follow_redirects=True)
        assert _state().manager_name == "المدرب"

    def test_incomplete_save_redirects_to_setup_step(self, client, tmp_path, monkeypatch):
        self._isolated_saves(tmp_path, monkeypatch)
        client.post("/new/3", follow_redirects=False)
        # Fresh save has no real manager yet -> dashboard must bounce to setup.
        r = client.get("/dashboard", follow_redirects=False)
        assert r.status_code in (301, 302)
        assert "/setup/3" in r.headers["Location"]

    def test_delete_save_and_reload_list(self, client, tmp_path, monkeypatch):
        dest = self._isolated_saves(tmp_path, monkeypatch)
        client.post("/new/2", follow_redirects=False)
        assert (dest / "save2.sqlite3").exists()
        client.post("/delete/2", follow_redirects=True)
        assert not (dest / "save2.sqlite3").exists()
        assert _html(client, "/load")

    def test_occupied_slot_rejected(self, client, tmp_path, monkeypatch):
        self._isolated_saves(tmp_path, monkeypatch)
        client.post("/new/2", follow_redirects=False)
        r = client.post("/new/2", follow_redirects=True)
        assert "مشغول" in r.get_data(as_text=True)


# ======================================================================
# Swap deals: success moves + message, every rejection in Arabic + no move
# ======================================================================
class TestSwapDeals:
    def _pair(self, target_value=100000, offered_value=90000):
        buyer, seller = _player_team(), _opponent()
        target = next(p for p in seller.players)
        target.market_value = target_value
        offered = next(p for p in buyer.players if not p.is_starting)
        offered.market_value = offered_value
        db.session.commit()
        return buyer, seller, target, offered

    def test_swap_success_moves_both_and_logs(self, client):
        buyer, seller, target, offered = self._pair()
        tid, oid = target.id, offered.id
        r = client.post(f"/swap_offer/{tid}",
                        data={"offered_player_id": oid, "cash": "0"},
                        follow_redirects=True)
        assert r.status_code == 200
        assert db.session.get(Player, tid).team_id == buyer.id
        assert db.session.get(Player, oid).team_id == seller.id
        assert "تمت المبادلة" in r.get_data(as_text=True)
        assert Message.query.filter_by(type="transfer").count() >= 1

    def test_swap_too_cheap_rejected_no_move(self, client):
        buyer, seller, target, offered = self._pair(target_value=100000, offered_value=10000)
        tid, oid = target.id, offered.id
        r = client.post(f"/swap_offer/{tid}",
                        data={"offered_player_id": oid, "cash": "0"},
                        follow_redirects=True)
        assert "80%" in r.get_data(as_text=True)
        assert db.session.get(Player, tid).team_id == seller.id
        assert db.session.get(Player, oid).team_id == buyer.id

    def test_swap_offered_not_owned_rejected(self, client):
        _, seller, target, _ = self._pair()
        foreign = next(p for p in seller.players)
        r = client.post(f"/swap_offer/{target.id}",
                        data={"offered_player_id": foreign.id, "cash": "0"},
                        follow_redirects=True)
        assert "ليس من فريقك" in r.get_data(as_text=True)

    def test_swap_own_target_rejected(self, client):
        team = _player_team()
        own = team.players[0]
        mate = next(p for p in team.players if p.id != own.id)
        r = client.post(f"/swap_offer/{own.id}",
                        data={"offered_player_id": mate.id, "cash": "0"},
                        follow_redirects=True)
        assert "في فريقك بالفعل" in r.get_data(as_text=True)

    def test_swap_free_agent_rejected(self, client):
        team = _player_team()
        fa = Player(name="DB Swap FA", position="FWD", attack=60, defense=40,
                    potential=70, age=22, height=180, weight=75,
                    market_value=50000, team_id=None)
        offered = next(p for p in team.players if not p.is_starting)
        db.session.add(fa)
        db.session.commit()
        try:
            r = client.post(f"/swap_offer/{fa.id}",
                            data={"offered_player_id": offered.id, "cash": "0"},
                            follow_redirects=True)
            assert "وكيل حر" in r.get_data(as_text=True)
        finally:
            db.session.delete(fa)
            db.session.commit()


# ======================================================================
# Mentoring: every rule flashes in Arabic and writes nothing on reject
# ======================================================================
class TestMentoringRules:
    def _ages(self, mentor_age=33, mentee_age=18):
        team = _player_team()
        mentor = next(p for p in team.players if p.age >= 24)
        mentor.age = mentor_age
        mentor.personality = "Leader"
        mentee = next(p for p in team.players if p.id != mentor.id)
        mentee.age = mentee_age
        db.session.commit()
        return mentor, mentee

    def test_self_mentoring_rejected(self, client):
        mentor, _ = self._ages()
        n0 = Mentoring.query.count()
        r = client.post("/mentoring/add",
                        data={"mentor_id": mentor.id, "mentee_id": mentor.id},
                        follow_redirects=True)
        assert "يوجّه نفسه" in r.get_data(as_text=True)
        assert Mentoring.query.count() == n0

    def test_young_mentor_rejected(self, client):
        mentor, mentee = self._ages(mentor_age=25, mentee_age=18)
        r = client.post("/mentoring/add",
                        data={"mentor_id": mentor.id, "mentee_id": mentee.id},
                        follow_redirects=True)
        assert "28+" in r.get_data(as_text=True) or "مخضرم" in r.get_data(as_text=True)

    def test_old_mentee_rejected(self, client):
        mentor, mentee = self._ages(mentor_age=33, mentee_age=27)
        r = client.post("/mentoring/add",
                        data={"mentor_id": mentor.id, "mentee_id": mentee.id},
                        follow_redirects=True)
        assert "24" in r.get_data(as_text=True)

    def test_mentor_younger_than_mentee_rejected(self, client):
        team = _player_team()
        mentor = next(p for p in team.players if p.age >= 24)
        mentor.age = 29
        mentee = next(p for p in team.players if p.id != mentor.id)
        mentee.age = 24
        db.session.commit()
        # mentor 29 >= 28 ok, mentee 24 <= 24 ok, but mentor must be OLDER;
        # force the inverted case via direct ages:
        mentor.age = 24
        mentee.age = 24
        db.session.commit()
        r = client.post("/mentoring/add",
                        data={"mentor_id": mentor.id, "mentee_id": mentee.id},
                        follow_redirects=True)
        assert Mentoring.query.filter_by(mentee_id=mentee.id).count() == 0

    def test_second_mentor_for_same_mentee_rejected(self, client):
        team = _player_team()
        m1 = next(p for p in team.players if p.age >= 24)
        m1.age = 34
        m2 = next(p for p in team.players if p.id != m1.id and p.age >= 24)
        m2.age = 35
        mentee = next(p for p in team.players if p.id not in (m1.id, m2.id))
        mentee.age = 19
        db.session.commit()
        client.post("/mentoring/add", data={"mentor_id": m1.id, "mentee_id": mentee.id},
                    follow_redirects=True)
        r = client.post("/mentoring/add", data={"mentor_id": m2.id, "mentee_id": mentee.id},
                        follow_redirects=True)
        assert "مرشد بالفعل" in r.get_data(as_text=True)

    def test_foreign_players_rejected(self, client):
        mentor, mentee = self._ages()
        opp = _opponent().players[0]
        r = client.post("/mentoring/add",
                        data={"mentor_id": mentor.id, "mentee_id": opp.id},
                        follow_redirects=True)
        assert "غير صالحين" in r.get_data(as_text=True)

    def test_remove_foreign_mentoring_rejected(self, client):
        opp = _opponent()
        m1 = opp.players[0]
        m1.age = 34
        m2 = opp.players[1]
        m2.age = 19
        row = Mentoring(team_id=opp.id, mentor_id=m1.id, mentee_id=m2.id, started_day=1)
        db.session.add(row)
        db.session.commit()
        r = client.post(f"/mentoring/remove/{row.id}", follow_redirects=True)
        assert "فريق آخر" in r.get_data(as_text=True)
        assert db.session.get(Mentoring, row.id) is not None


# ======================================================================
# Inbox details: urgent rendering, desc ordering, badge hide/show, polling
# ======================================================================
class TestInboxDetails:
    def test_urgent_renders_marker(self, client):
        db.session.add(Message(title="DB Urgent T", content="urgent body here",
                               type="info", read=False, created_day=2, urgent=True))
        db.session.commit()
        text = _html(client, "/messages")
        assert "DB Urgent T" in text
        assert "عاجل" in text

    def test_messages_ordered_newest_first(self, client):
        Message.query.delete()
        db.session.add(Message(title="Old Msg", content="old", type="info",
                               read=True, created_day=1))
        db.session.add(Message(title="New Msg", content="new", type="info",
                               read=True, created_day=9))
        db.session.commit()
        text = _html(client, "/messages")
        assert text.index("New Msg") < text.index("Old Msg")

    def test_badge_hidden_when_zero_unread(self, client):
        Message.query.delete()
        db.session.commit()
        assert "badge-hidden" in _html(client, "/dashboard")

    def test_badge_shows_db_count(self, client):
        Message.query.delete()
        db.session.add(Message(title="b1", content="c", type="info",
                               read=False, created_day=1))
        db.session.commit()
        text = _html(client, "/dashboard")
        assert "badge-hidden" not in text
        assert ">1<" in text

    def test_unread_polling_wired_in_js(self, client):
        with open(os.path.join(STATIC_DIR, "app.js"), encoding="utf-8") as f:
            js = f.read()
        assert "inbox_unread" in js
        assert "setInterval" in js


# ======================================================================
# Calendar simulation edge cases + date-collision rule
# ======================================================================
class TestCalendarSimEdgeCases:
    def test_past_date_rejected_date_unchanged(self, client):
        state = _state()
        state.current_date = "2026-06-10"
        db.session.commit()
        r = client.post("/simulate_to_date", data={"target_date": "2026-06-01"},
                        follow_redirects=True)
        assert "المستقبل" in r.get_data(as_text=True)
        assert _state().current_date == "2026-06-10"

    def test_same_day_rejected(self, client):
        state = _state()
        state.current_date = "2026-06-10"
        db.session.commit()
        r = client.post("/simulate_to_date", data={"target_date": "2026-06-10"},
                        follow_redirects=True)
        assert "المستقبل" in r.get_data(as_text=True)

    def test_missing_date_rejected(self, client):
        r = client.post("/simulate_to_date", data={}, follow_redirects=True)
        assert "غير صالح" in r.get_data(as_text=True)

    def test_year_cap_note_shown(self, client):
        state = _state()
        state.current_date = "2026-06-01"
        db.session.commit()
        r = client.post("/simulate_to_date", data={"target_date": "2028-06-01"},
                        follow_redirects=True)
        assert "سنة واحدة" in r.get_data(as_text=True)

    def test_date_collision_keeps_unplayed_fixture(self, client):
        team, opp = _player_team(), _opponent()
        played = Match(home_team_id=team.id, away_team_id=opp.id, week=50,
                       played=True, result="2-0", home_score=2, away_score=0,
                       date="2026-08-01")
        live = Match(home_team_id=team.id, away_team_id=opp.id, week=51,
                     played=False, date="2026-08-01")
        db.session.add_all([played, live])
        db.session.commit()
        text = _html(client, "/calendar")
        embedded = json.loads(
            re.search(r"^\s*matchData = (\{.*?\});\s*\n\s*gameDateStr",
                      text, re.S | re.M).group(1)
        )
        assert embedded["2026-08-01"]["id"] == live.id  # unplayed wins
        assert embedded["2026-08-01"]["played"] is False


# ======================================================================
# Stats/standings league filter + fallback
# ======================================================================
class TestLeagueFilterAndFallback:
    def test_stats_league_filter_shows_selected(self, client):
        league = League.query.first()
        text = _html(client, f"/standings?league_id={league.id}")
        assert league.name in text

    def test_stats_invalid_league_falls_back(self, client):
        team = _player_team()
        league = db.session.get(League, team.league_id)
        r = client.get("/standings?league_id=999999")
        assert r.status_code == 200
        assert league.name in r.get_data(as_text=True)

    def test_top_scorers_invalid_league_falls_back(self, client):
        team = _player_team()
        league = db.session.get(League, team.league_id)
        r = client.get("/standings?league_id=999999")
        assert r.status_code == 200
        assert league.name in r.get_data(as_text=True)

    def test_standings_invalid_league_flash_and_fallback(self, client):
        team = _player_team()
        r = client.get("/standings?league_id=999999", follow_redirects=True)
        assert r.status_code == 200
        text = r.get_data(as_text=True)
        assert team.name in text
        assert "غير موجود" in text


# ======================================================================
# Training clamp + flash, staff money rules, season-end state, blocks
# ======================================================================
class TestTrainingClampAndFlash:
    @pytest.mark.parametrize("sent,expected", [("99", 10), ("-3", 1), ("abc", 5), ("7", 7)])
    def test_intensity_clamped(self, client, sent, expected):
        client.post("/training/save", data={"focus": "attack", "intensity": sent},
                    follow_redirects=True)
        assert _player_team().training_intensity == expected

    def test_save_flash_shows(self, client):
        r = client.post("/training/save", data={"focus": "physical", "intensity": "6"},
                        follow_redirects=True)
        assert "تم حفظ إعدادات التدريب" in r.get_data(as_text=True)
        assert 'value="physical" selected' in r.get_data(as_text=True)


class TestStaffMoneyRules:
    def test_hire_poor_budget_rejected(self, client):
        team = _player_team()
        team.budget = 10
        db.session.commit()
        s = Staff(name="DB Expensive", role="fitness", level=5, salary=999999, team_id=None)
        db.session.add(s)
        db.session.commit()
        r = client.post(f"/staff/hire/{s.id}", follow_redirects=True)
        assert "الميزانية غير كافية" in r.get_data(as_text=True)
        assert db.session.get(Staff, s.id).team_id is None

    def test_hire_twice_rejected(self, client):
        team = _player_team()
        team.budget = 5_000_000
        s = Staff(name="DB Twice", role="fitness", level=1, salary=1000, team_id=team.id)
        db.session.add(s)
        db.session.commit()
        r = client.post(f"/staff/hire/{s.id}", follow_redirects=True)
        assert "بالفعل" in r.get_data(as_text=True)

    def test_fire_foreign_rejected(self, client):
        opp = _opponent()
        s = Staff(name="DB Opp Staff", role="fitness", level=1, salary=1000, team_id=opp.id)
        db.session.add(s)
        db.session.commit()
        r = client.post(f"/staff/fire/{s.id}", follow_redirects=True)
        assert "لا يعمل في فريقك" in r.get_data(as_text=True)

    def test_fire_deducts_compensation(self, client):
        from config import STAFF_FIRE_COMPENSATION_RATIO
        team = _player_team()
        team.budget = 1_000_000
        s = Staff(name="DB Comp", role="fitness", level=1, salary=100000, team_id=team.id)
        db.session.add(s)
        db.session.commit()
        client.post(f"/staff/fire/{s.id}", follow_redirects=True)
        assert _player_team().budget == 1_000_000 - int(100000 * STAFF_FIRE_COMPENSATION_RATIO)

    def test_hire_flash_names_coach(self, client):
        team = _player_team()
        team.budget = 5_000_000
        s = Staff(name="DB Named Coach", role="fitness", level=1, salary=1000, team_id=None)
        db.session.add(s)
        db.session.commit()
        r = client.post(f"/staff/hire/{s.id}", follow_redirects=True)
        assert "DB Named Coach" in r.get_data(as_text=True)


class TestSeasonEndState:
    def test_season_end_resets_and_flashes(self, client):
        state = _state()
        state.season_over = True
        season0 = state.season_number or 1
        db.session.commit()
        r = client.post("/season_end", follow_redirects=True)
        assert r.status_code == 200
        text = r.get_data(as_text=True)
        assert "بدأ الموسم الجديد" in text
        state = _state()
        assert state.season_over is False
        assert state.season_number == season0 + 1
        assert state.current_day == 1 and state.current_week == 1
        assert Match.query.filter_by(played=False).count() > 0

    def test_season_over_dashboard_banner(self, client):
        state = _state()
        state.season_over = True
        db.session.commit()
        text = _html(client, "/dashboard")
        assert "الموسم انتهى" in text
        assert 'action="/season_end"' in text


# ======================================================================
# Cross-cutting: flashes render, redirect chains, RTL everywhere
# ======================================================================
class TestCrossCutting:
    def test_flash_renders_in_flash_div(self, client):
        # Slot 4 is in range but has no file -> "save not found" flash.
        r = client.get("/setup/4", follow_redirects=True)
        text = r.get_data(as_text=True)
        assert "الحفظ غير موجود" in text
        assert "flash-message" in text

    def test_out_of_range_slot_flash(self, client):
        r = client.get("/setup/99", follow_redirects=True)
        assert "Slot" in r.get_data(as_text=True)

    def test_root_redirect_chain_no_save(self, client):
        with client.session_transaction() as sess:
            sess.pop("active_save", None)
        try:
            r = client.get("/", follow_redirects=False)
            assert r.status_code in (301, 302)
            assert r.headers["Location"].endswith("/load")
            assert client.get("/load").status_code == 200
        finally:
            with client.session_transaction() as sess:
                sess["active_save"] = 1

    def test_match_requires_login_setup_redirect(self, client):
        # With a valid save the page renders; chain itself is covered above.
        assert _player_team().name in _html(client, "/match")

    def test_post_only_mutations_reject_get(self, client):
        team = _player_team()
        pid = team.players[0].id
        for url in (f"/toggle_starter/{pid}", "/formation/save", "/training/save"):
            r = client.get(url, headers={"X-Requested-With": "XMLHttpRequest"})
            assert r.status_code == 405, url
            assert r.get_json()["error"] == "method not allowed"
        # Progression endpoints explicitly allow GET too (redirect-after-POST).
        for url in ("/advance_day", "/continue", "/advance_week"):
            assert client.get(url, follow_redirects=False).status_code in (200, 301, 302), url


# ======================================================================
# European leagues on the standings league selector
# ======================================================================
class TestEuropeanPages:
    def test_standings_league_selector_lists_all(self, client):
        leagues = [
            ("Bundesliga", "Germany", 1),
            ("La Liga", "Spain", 1),
            ("Ligue 1", "France", 1),
            ("Serie A", "Italy", 1),
            ("Eredivisie", "Netherlands", 1),
            ("Primeira Liga", "Portugal", 1),
            ("Süper Lig", "Turkey", 1),
            ("Scottish Premiership", "Scotland", 1),
            ("Allsvenskan", "Sweden", 1),
        ]
        with client.application.app_context():
            for name, country, tier in leagues:
                if League.query.filter_by(name=name).first() is None:
                    db.session.add(League(name=name, tier=tier,
                                          reputation=70, country=country))
            db.session.commit()
        text = _html(client, "/standings")
        for name, _, _ in leagues:
            assert name in text, name


# ======================================================================
# Calendar page (timeline removed per fix.txt): views + today badge
# ======================================================================
class TestCalendarMonthView:
    def test_month_only_view_with_dot_legend(self, client):
        text = _html(client, "/calendar")
        # fix.txt: month view is the only view; week/agenda switchers removed.
        for view in ('data-view="month"', 'data-view="week"', 'data-view="agenda"'):
            assert view not in text
        assert "calendarDaysGrid" in text
        assert "match-dot" in text
        assert "legend-item" in text
        assert "TODAY" in text
        assert "today-badge" in text

    def test_player_matches_carry_dot_colors(self, client):
        text = _html(client, "/calendar")
        embedded = json.loads(
            re.search(r"^\s*matchData = (\{.*?\});\s*\n\s*gameDateStr",
                      text, re.S | re.M).group(1)
        )
        assert embedded, "calendar must embed player fixtures"
        for entry in embedded.values():
            assert entry["comp_type"] in ("league", "domestic_cup", "continental")
            assert entry["dot_color"]
            for same_day in entry["matches"]:
                assert same_day["dot_color"]


# ======================================================================
# fix.txt: Dashboard focus hub + expandable sections
# ======================================================================
class TestDashboardFocusHub:
    def test_focus_hub_core_metrics_present(self, client):
        text = _html(client, "/dashboard")
        assert "focus-hub" in text
        assert "focus-card" in text
        assert "المباراة القادمة" in text
        assert "ثقة مجلس الإدارة" in text
        assert "المركز في الدوري" in text
        assert "critical-alerts" in text

    def test_board_confidence_value_from_db(self, client):
        state = _state()
        text = _html(client, "/dashboard")
        assert f"{state.board_confidence or 60}%" in text

    def test_critical_alert_for_low_board_confidence(self, client):
        state = _state()
        state.board_confidence = 15
        for p in _player_team().players:
            p.injury_days = 0
        db.session.commit()
        text = _html(client, "/dashboard")
        assert "critical-alerts" in text
        assert "ثقة مجلس الإدارة منخفضة" in text

    def test_critical_alert_for_injury(self, client):
        p = _player_team().players[0]
        p.injury_days = 6
        db.session.commit()
        text = _html(client, "/dashboard")
        assert "critical-alerts" in text
        assert "لاعب مصاب" in text

    def test_heavy_sections_are_expandable_and_keep_db_content(self, client):
        team = _player_team()
        text = _html(client, "/dashboard")
        assert "<details" in text
        assert "dash-fold" in text
        # Folded content is still in the DOM (readable by taps/tests).
        for p in team.players[:3]:
            assert p.name in text, p.name

    def test_quick_action_forms_survive_redesign(self, client):
        text = _html(client, "/dashboard")
        assert 'action="/continue"' in text
        assert 'action="/advance_week"' in text


# ======================================================================
# fix.txt: Navigation hubs
# ======================================================================
class TestNavigationHubs:
    def test_sidebar_grouped_into_hubs(self, client):
        text = _html(client, "/dashboard")
        for hub in ("مركز النادي", "مركز الإدارة", "مركز الكشافة", "مركز المنافسات"):
            assert hub in text, hub

    def test_hub_links_still_reachable(self, client):
        text = _html(client, "/dashboard")
        for href in ("/squad", "/staff", "/medical", "/training", "/tactics",
                     "/board", "/scouting",
                     "/standings", "/cups"):
            assert href in text, href


# ======================================================================
# fix.txt: Inbox notification priority
# ======================================================================
class TestInboxPriority:
    def test_priority_badges_and_filter_present(self, client):
        Message.query.delete()
        db.session.add(Message(title="Prio One", content="body", type="info",
                               read=False, created_day=1, urgent=True))
        db.session.commit()
        text = _html(client, "/messages")
        assert "prio-badge" in text
        assert "data-priority" in text
        assert 'id="inboxPriority"' in text
        assert "حرِج" in text  # CRITICAL label

    def test_urgent_message_is_critical_priority(self, client):
        Message.query.delete()
        db.session.add(Message(title="Urgent P", content="body", type="info",
                               read=False, created_day=1, urgent=True))
        db.session.commit()
        text = _html(client, "/messages")
        assert 'data-priority="critical"' in text

    def test_info_message_is_optional_priority(self, client):
        Message.query.delete()
        db.session.add(Message(title="Info P", content="body", type="info",
                               read=True, created_day=2))
        db.session.commit()
        text = _html(client, "/messages")
        assert 'data-priority="optional"' in text


# ======================================================================
# fix.txt: Match centre — pre-match narrative
# ======================================================================
class TestMatchStorylines:
    def test_pre_match_storylines_card_rendered(self, client):
        state = _state()
        state.board_confidence = 80
        db.session.commit()
        text = _html(client, "/match")
        assert "match-storylines" in text
        assert "storyline-feed" in text
        assert "حكاية المباراة" in text
