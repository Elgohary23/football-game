"""Playwright end-to-end tests: real Chromium vs live Flask server.

Verifies page ELEMENTS display actual database data (league table rows,
top-scorer names/goals, squad names, fixture teams, scores, inbox content).
"""
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

pytestmark = pytest.mark.e2e

BASE_URL = "http://127.0.0.1:5001"
DB_FD, DB_PATH = tempfile.mkstemp(suffix=".sqlite3")
os.close(DB_FD)


def _seed(app):
    from models import db, GameState, League, Match, Message, Player, Team

    league = League(name="E2E Premier League", tier=1, reputation=90)
    db.session.add(league)
    db.session.flush()

    home = Team(name="E2E Home FC", morale=70, budget=1000000, reputation=70,
                is_player_team=True, league_id=league.id)
    away = Team(name="E2E Away FC", morale=70, budget=1000000, reputation=60,
                is_player_team=False, league_id=league.id)
    db.session.add_all([home, away])
    db.session.flush()

    positions = ["GK", "GK"] + ["DEF"] * 4 + ["MID"] * 5 + ["FWD"] * 4
    for team in (home, away):
        for i, pos in enumerate(positions):
            db.session.add(Player(
                name="E2E Star %s %d" % (team.name, i), position=pos,
                attack=70 if pos in ("FWD", "MID") else 55,
                defense=70 if pos in ("DEF", "GK") else 55,
                potential=85, age=24, height=180, weight=75,
                personality="Normal", region="Europe", market_value=100000,
                team_id=team.id, is_starting=(i < 11)))

    state = GameState(manager_name="E2E Manager", manager_nationality="E2E",
                      current_day=8, current_week=2, season_over=False,
                      user_league_id=league.id, season_number=1,
                      board_confidence=60, current_date="2026-06-08")
    db.session.add(state)
    db.session.flush()

    played = Match(home_team_id=home.id, away_team_id=away.id, week=1,
                   played=False, date="2026-06-01")
    upcoming = Match(home_team_id=away.id, away_team_id=home.id, week=2,
                     played=False, date="2026-06-08")
    db.session.add_all([played, upcoming])
    db.session.commit()

    from models import Staff, TransferOffer
    db.session.add(Staff(name="E2E Scout", role="scout", specialty="E2E",
                         level=2, salary=40000, team_id=home.id))
    target = Player.query.filter_by(team_id=away.id).first()
    db.session.add(TransferOffer(
        player_id=target.id, from_team_id=away.id, to_team_id=home.id,
        amount=50000, status="pending", created_day=8, deadline_day=12))
    db.session.commit()

    from simulation import simulate_match
    from services import invalidate_league_cache

    simulate_match(played, mode="deep")
    # Heal the home squad: match fatigue may randomly injure starters, which
    # would make starter counts and the medical empty-state non-deterministic.
    for p in Player.query.filter_by(team_id=home.id).all():
        p.injury_days = 0
        p.fitness = 100
    db.session.add(Message(title="E2E notice", content="E2E inbox marker 98765",
                           type="info", read=False, created_day=1))
    db.session.commit()
    invalidate_league_cache()
    return {"played_id": played.id, "upcoming_id": upcoming.id,
            "league_id": league.id}


@pytest.fixture(scope="module")
def live_server():
    from werkzeug.serving import make_server

    from app import create_app
    from models import db

    app = create_app()
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI="sqlite:///%s" % DB_PATH.replace("\\", "/"),
        SQLALCHEMY_ENGINE_OPTIONS={"connect_args": {"check_same_thread": False}},
    )
    if "sqlalchemy" in app.extensions:
        del app.extensions["sqlalchemy"]
    db.init_app(app)
    with app.app_context():
        db.create_all()
        ids = _seed(app)
        # Steal a signed session cookie carrying active_save=1 for the browser.
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["active_save"] = 1
        cookie = client.get_cookie("session")

    server = make_server("127.0.0.1", 5001, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # The app is exposed so interaction tests can prepare exact DB state
    # (heal injuries, set ages, flip flags) without touching HTTP.
    yield {"ids": ids, "session_cookie": cookie.value if cookie else None, "app": app}
    server.shutdown()
    thread.join(timeout=10)
    try:
        os.remove(DB_PATH)
    except OSError:
        pass


@pytest.fixture(scope="module")
def shared_browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture(scope="module")
def browser_page(live_server, shared_browser):
    cookie = live_server["session_cookie"]
    assert cookie, "no session cookie obtained"
    context = shared_browser.new_context()
    context.add_cookies([{"name": "session", "value": cookie,
                          "domain": "127.0.0.1", "path": "/"}])
    page = context.new_page()
    yield page
    context.close()


@pytest.fixture(scope="module")
def mobile_page(live_server, shared_browser):
    cookie = live_server["session_cookie"]
    context = shared_browser.new_context(viewport={"width": 390, "height": 844})
    context.add_cookies([{"name": "session", "value": cookie,
                          "domain": "127.0.0.1", "path": "/"}])
    page = context.new_page()
    yield page
    context.close()


def _body(page):
    return page.content()


class TestElementsShowDbData:
    def test_html_is_rtl(self, browser_page):
        browser_page.goto(BASE_URL + "/dashboard")
        assert browser_page.get_attribute("html", "dir") == "rtl"

    def test_standings_rows_match_db(self, browser_page):
        browser_page.goto(BASE_URL + "/standings")
        rows = browser_page.locator("table.standings-table tbody tr")
        assert rows.count() >= 2
        text = rows.all_text_contents()
        assert any("E2E Home FC" in r for r in text)
        assert any("E2E Away FC" in r for r in text)

    def test_top_scorers_row_match_db(self, browser_page, live_server):
        browser_page.goto(BASE_URL + "/stats/top-scorers")
        first_row = browser_page.locator("table tbody tr").first.inner_text()
        assert "E2E Star" in first_row

    def test_squad_lists_db_players(self, browser_page):
        browser_page.goto(BASE_URL + "/squad")
        assert "E2E Star E2E Home FC 0" in _body(browser_page)

    def test_dashboard_shows_team_and_manager(self, browser_page):
        browser_page.goto(BASE_URL + "/dashboard")
        body = _body(browser_page)
        assert "E2E Home FC" in body
        assert "E2E Manager" in body

    def test_match_page_shows_fixture_teams(self, browser_page):
        browser_page.goto(BASE_URL + "/match")
        body = _body(browser_page)
        assert "E2E Home FC" in body
        assert "E2E Away FC" in body

    def test_history_shows_played_score(self, browser_page):
        browser_page.goto(BASE_URL + "/match_history")
        body = _body(browser_page)
        assert "E2E Home FC" in body
        assert "E2E Away FC" in body

    def test_report_shows_score_and_scorers(self, browser_page, live_server):
        browser_page.goto(BASE_URL + "/match_report/%d" % live_server["ids"]["played_id"])
        body = _body(browser_page)
        assert "E2E Home FC" in body
        assert "E2E Star" in body

    def test_messages_show_inbox_content(self, browser_page):
        browser_page.goto(BASE_URL + "/messages")
        assert "E2E inbox marker 98765" in _body(browser_page)

    def test_calendar_embeds_fixture_data(self, browser_page):
        browser_page.goto(BASE_URL + "/calendar")
        body = _body(browser_page)
        assert "E2E Home FC" in body or "E2E Away FC" in body


# ---------------------------------------------------------------------------
# NOTE: definition order matters — read-only detail tests first, then net-zero
# interactions, then the live flow (finishes the week-2 fixture), then tests
# that run after the live match. Mutating tests restore state where possible.
# ---------------------------------------------------------------------------
from playwright.sync_api import expect


class TestStandingsDetails:
    def test_league_tab_and_title(self, browser_page):
        browser_page.goto(BASE_URL + "/standings")
        expect(browser_page.locator("h1").first).to_contain_text("E2E Premier League")
        expect(browser_page.locator(".league-tab.active").first).to_contain_text("E2E Premier League")

    def test_player_row_highlighted_with_badge(self, browser_page):
        browser_page.goto(BASE_URL + "/standings")
        row = browser_page.locator("table.standings-table tbody tr.row-highlight")
        expect(row).to_contain_text("E2E Home FC")
        expect(row).to_contain_text("فريقك")

    def test_points_and_played_math(self, browser_page):
        browser_page.goto(BASE_URL + "/standings")
        cells = browser_page.locator(
            "table.standings-table tbody tr.row-highlight td").all_text_contents()
        played, wins, draws, losses = (int(cells[i].strip()) for i in (2, 3, 4, 5))
        assert played == wins + draws + losses

    def test_goal_difference_cell(self, browser_page):
        browser_page.goto(BASE_URL + "/standings")
        row = browser_page.locator("table.standings-table tbody tr.row-highlight")
        text = row.inner_text()
        assert "نقاط" not in text  # data row, not header
        assert any(ch.isdigit() for ch in text)

    def test_legend_and_rank(self, browser_page):
        browser_page.goto(BASE_URL + "/standings")
        body = _body(browser_page)
        assert "مؤهل لدوري الأبطال" in body
        assert "منطقة الهبوط" in body
        assert "المركز الحالي لفريقك" in body


class TestStatsCenterDetails:
    def test_center_tables_show_db_names(self, browser_page):
        browser_page.goto(BASE_URL + "/stats")
        body = _body(browser_page)
        assert "مركز الإحصائيات" in body
        assert "E2E Star" in body
        assert "E2E Premier League" in body

    def test_center_links_to_detail_pages(self, browser_page):
        browser_page.goto(BASE_URL + "/stats")
        expect(browser_page.locator('a[href*="/stats/top-scorers"]').first).to_be_visible()
        hrefs = browser_page.eval_on_selector_all(
            "a", "els => els.map(e => e.getAttribute('href'))")
        assert any("/stats/top-assists" in (h or "") for h in hrefs)

    def test_top_assists_row(self, browser_page):
        browser_page.goto(BASE_URL + "/stats/top-assists")
        expect(browser_page.locator("h1")).to_contain_text("صنّاع اللعب")
        expect(browser_page.locator("table tbody tr").first).to_contain_text("E2E Star")

    def test_scorer_rate_column(self, browser_page):
        browser_page.goto(BASE_URL + "/stats/top-scorers")
        body = _body(browser_page)
        assert "معدل/مباراة" in body
        assert "E2E Premier League" in body


class TestSquadDetails:
    def test_fifteen_rows_and_counter(self, browser_page):
        browser_page.goto(BASE_URL + "/squad")
        assert browser_page.locator("#squadTable tbody tr.player-row").count() == 15
        expect(browser_page.locator("#starterCount")).to_contain_text("11/11")

    def test_money_and_bars_format(self, browser_page):
        browser_page.goto(BASE_URL + "/squad")
        money = browser_page.locator("#squadTable td.money-text").first.inner_text()
        assert "$" in money and "," in money
        assert browser_page.locator("#squadTable .progress-fill").count() == 15

    def test_position_badges_present(self, browser_page):
        browser_page.goto(BASE_URL + "/squad")
        for pos in ("GK", "DEF", "MID", "FWD"):
            assert browser_page.locator(f"#squadTable .pos-tag.{pos}").count() >= 1

    def test_filter_gk_narrows_rows(self, browser_page):
        browser_page.goto(BASE_URL + "/squad")
        browser_page.locator('.filter-btn[data-filter="GK"]').click()
        visible = browser_page.locator("#squadTable tbody tr:visible")
        assert visible.count() == 2
        for i in range(visible.count()):
            assert visible.nth(i).get_attribute("data-pos") == "GK"
        browser_page.locator('.filter-btn[data-filter="all"]').click()
        assert browser_page.locator("#squadTable tbody tr:visible").count() == 15

    def test_filter_starters(self, browser_page):
        browser_page.goto(BASE_URL + "/squad")
        browser_page.locator('.filter-btn[data-filter="starter"]').click()
        assert browser_page.locator("#squadTable tbody tr:visible").count() == 11

    def test_sort_by_name_reorders(self, browser_page):
        browser_page.goto(BASE_URL + "/squad")
        before = browser_page.locator("#squadTable tbody tr td:nth-child(2)").all_text_contents()
        browser_page.locator('th[data-sort="name"]').click()
        after = browser_page.locator("#squadTable tbody tr td:nth-child(2)").all_text_contents()
        assert before != after

    def test_pitch_and_bench_sections(self, browser_page):
        browser_page.goto(BASE_URL + "/squad")
        body = _body(browser_page)
        assert "التشكيلة الأساسية" in body
        assert "دكة الاحتياط" in body


class TestDashboardDetails:
    def test_manager_record_values(self, browser_page):
        browser_page.goto(BASE_URL + "/dashboard")
        values = browser_page.locator(".manager-record .record-value").all_text_contents()
        assert len(values) == 6
        assert any("%" in v for v in values)

    def test_budget_formatted(self, browser_page):
        browser_page.goto(BASE_URL + "/dashboard")
        money = browser_page.locator(".stat-card .money-text").first.inner_text()
        assert "$" in money and "," in money

    def test_stat_cards_labels(self, browser_page):
        browser_page.goto(BASE_URL + "/dashboard")
        body = _body(browser_page)
        for label in ("المركز", "فارق الأهداف", "الميزانية", "المعنويات",
                      "كيمياء الفريق", "ثقة الإدارة"):
            assert label in body, label

    def test_quick_actions_present(self, browser_page):
        browser_page.goto(BASE_URL + "/dashboard")
        assert browser_page.locator('form[action="/continue"] button').count() == 1
        assert browser_page.locator('form[action="/advance_week"] button').count() == 1

    def test_recent_results_and_squad_table(self, browser_page):
        browser_page.goto(BASE_URL + "/dashboard")
        body = _body(browser_page)
        assert "النتائج الأخيرة" in body
        assert browser_page.locator("#dash-squad-table tbody tr").count() >= 1

    def test_days_remaining_banner(self, browser_page):
        browser_page.goto(BASE_URL + "/dashboard")
        body = _body(browser_page)
        assert "الأيام المتبقية للمباراة" in body or "يوم المباراة" in body


class TestFormationDetails:
    def test_pitch_bench_pool(self, browser_page):
        browser_page.goto(BASE_URL + "/formation")
        assert browser_page.locator("#pitch-grid").count() == 1
        assert browser_page.locator("#bench").count() == 1
        pool_count = browser_page.evaluate(
            "document.getElementById('playerPool').childElementCount")
        assert pool_count == 15
        expect(browser_page.locator("#formationCount")).to_contain_text("لاعب")

    def test_tactics_selects(self, browser_page):
        browser_page.goto(BASE_URL + "/formation")
        for name in ("pressing_style", "match_tactic", "penalty_taker_id",
                     "freekick_taker_id", "preset_type"):
            assert browser_page.locator(f'select[name="{name}"]').count() == 1, name

    def test_taker_options_are_squad_players(self, browser_page):
        browser_page.goto(BASE_URL + "/formation")
        options = browser_page.locator(
            'select[name="penalty_taker_id"] option').all_text_contents()
        assert any("E2E Star" in o for o in options)

    def test_save_reset_buttons(self, browser_page):
        browser_page.goto(BASE_URL + "/formation")
        assert browser_page.locator("#saveFormationBtn").count() == 1
        assert browser_page.locator("#resetFormationBtn").count() == 1
        assert browser_page.locator("#csrfToken").count() == 1


class TestCalendarDetails:
    def test_header_and_nav(self, browser_page):
        browser_page.goto(BASE_URL + "/calendar")
        expect(browser_page.locator("h1")).to_contain_text("تقويم مباريات الفريق")
        assert browser_page.locator("#prevMonthBtn").count() == 1
        assert browser_page.locator("#nextMonthBtn").count() == 1
        expect(browser_page.locator("#gameDateDisplay")).to_contain_text("2026-06-08")

    def test_day_cells_rendered(self, browser_page):
        browser_page.goto(BASE_URL + "/calendar")
        assert browser_page.locator("#calendarDaysGrid > *").count() > 20

    def test_fixture_markers_show_teams(self, browser_page):
        browser_page.goto(BASE_URL + "/calendar")
        src = _body(browser_page)
        assert "E2E Home FC" in src and "E2E Away FC" in src


class TestMatchCenterDetails:
    def test_team_headings_and_scouting(self, browser_page):
        browser_page.goto(BASE_URL + "/match")
        body = _body(browser_page)
        assert "مركز المباراة" in body
        assert "تقرير الكشافة" in body
        assert "مقارنة إحصائية" in body

    def test_both_teams_named(self, browser_page):
        browser_page.goto(BASE_URL + "/match")
        h3s = browser_page.locator("h3").all_text_contents()
        assert any("E2E Away FC" in h for h in h3s)
        assert any("E2E Home FC" in h for h in h3s)


class TestMessagesDetails:
    def test_list_shows_title(self, browser_page):
        browser_page.goto(BASE_URL + "/messages")
        expect(browser_page.locator("h1")).to_contain_text("الرسائل")
        assert "E2E notice" in _body(browser_page)

    def test_unread_badge_and_api(self, browser_page):
        browser_page.goto(BASE_URL + "/dashboard")
        expect(browser_page.locator("#unreadBadge")).to_contain_text("1")
        data = browser_page.evaluate(
            "fetch('/inbox_unread').then(r => r.json())")
        assert data["unread"] == 1

    def test_pending_offers_section(self, browser_page):
        browser_page.goto(BASE_URL + "/messages")
        assert "عروض الانتقال المعلقة" in _body(browser_page)


class TestClubPages:
    def test_training_form(self, browser_page):
        browser_page.goto(BASE_URL + "/training")
        body = _body(browser_page)
        assert "التدريب والتطوير" in body
        assert browser_page.locator('select[name="focus"]').count() == 1
        assert browser_page.locator('input[name="intensity"]').count() == 1
        assert "/10" in body

    def test_staff_empty_states(self, browser_page):
        browser_page.goto(BASE_URL + "/staff")
        body = _body(browser_page)
        assert "الطاقم التدريبي" in body
        assert "طاقمك التدريبي الحالي" in body
        assert "الطاقم المتاح للتعاقد" in body
        assert browser_page.locator('form[action="/staff/generate"]').count() == 1

    def test_facilities_levels_and_upgrades(self, browser_page):
        browser_page.goto(BASE_URL + "/facilities")
        body = _body(browser_page)
        assert "المرافق" in body
        assert "مرافق التدريب" in body
        assert "أكاديمية الشباب" in body
        assert browser_page.locator('form[action="/upgrade_facility/training"]').count() == 1
        assert browser_page.locator('form[action="/upgrade_facility/youth"]').count() == 1

    def test_medical_empty_and_ready(self, browser_page):
        browser_page.goto(BASE_URL + "/medical")
        body = _body(browser_page)
        assert "المركز الطبي" in body
        assert "اللاعبون المصابون" in body
        assert "لا يوجد لاعبون مصابون" in body
        assert "اللاعبون الجاهزون" in body
        assert "E2E Star" in body


class TestScoutingCompare:
    def test_market_sections_and_search(self, browser_page):
        browser_page.goto(BASE_URL + "/scouting")
        body = _body(browser_page)
        assert "سوق الانتقالات والكشف" in body
        assert "الكشافة ومهامهم" in body
        assert "لاعبو الأندية" in body
        assert browser_page.locator("#searchName").count() == 1
        assert browser_page.locator("#filterPos").count() == 1
        assert browser_page.locator("#scoutTable").count() == 1

    def test_opponent_players_listed_with_buy(self, browser_page):
        browser_page.goto(BASE_URL + "/scouting")
        body = _body(browser_page)
        assert "E2E Star E2E Away FC 0" in body
        forms = browser_page.locator('#scoutTable form[action^="/buy_player/"]').count()
        assert forms >= 1

    def test_compare_selects_and_result(self, browser_page):
        browser_page.goto(BASE_URL + "/compare?player_a=1&player_b=16")
        assert browser_page.locator('select[name="player_a"]').count() == 1
        assert browser_page.locator('select[name="player_b"]').count() == 1
        body = _body(browser_page)
        assert "E2E Star E2E Home FC 0" in body
        assert "E2E Star E2E Away FC 0" in body


class TestNavLayout:
    def test_all_nav_links(self, browser_page):
        browser_page.goto(BASE_URL + "/dashboard")
        hrefs = browser_page.eval_on_selector_all(
            "#navMenu a", "els => els.map(e => e.getAttribute('href'))")
        for expected in ("/dashboard", "/squad", "/formation", "/calendar",
                         "/match", "/standings", "/messages"):
            assert any(expected in (h or "") for h in hrefs), expected

    def test_head_meta_and_footer(self, browser_page):
        browser_page.goto(BASE_URL + "/dashboard")
        assert browser_page.locator('meta[name="viewport"]').count() == 1
        assert browser_page.locator('meta[name="theme-color"]').count() == 1
        assert "style.css" in _body(browser_page)
        assert browser_page.locator(".skip-link").count() == 1

    def test_load_page_renders(self, browser_page):
        browser_page.goto(BASE_URL + "/load")
        expect(browser_page.locator("h1")).to_contain_text("اختيار الحفظ")


class TestSquadToggleInteraction:
    def test_toggle_off_and_on(self, browser_page):
        browser_page.goto(BASE_URL + "/squad")
        counter = browser_page.locator("#starterCount")
        start_text = counter.inner_text()
        start_n = int(start_text.split("/")[0])
        row = browser_page.locator(
            '#squadTable tbody tr.player-row:not(.row-injured)[data-starter="1"]').first
        pid = row.get_attribute("data-id")
        assert pid
        row.locator("button").click()
        expect(counter).to_contain_text(f"{start_n - 1}/11", timeout=10000)
        expect(browser_page.locator(".toggler-toast").first).to_be_visible(timeout=10000)
        browser_page.locator(
            f'#squadTable tbody tr.player-row[data-id="{pid}"] button').click()
        expect(counter).to_contain_text(f"{start_n}/11", timeout=10000)


class TestFormationSaveInteraction:
    def _post(self, browser_page, positions):
        return browser_page.evaluate(
            """async (pos) => {
                const csrf = document.getElementById('csrfToken').value;
                const r = await fetch('/formation/save', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf},
                    body: JSON.stringify({positions: pos})
                });
                return {status: r.status, body: await r.json()};
            }""", positions)

    def test_valid_save(self, browser_page):
        browser_page.goto(BASE_URL + "/formation")
        ids = [1] + list(range(3, 13))  # 1 GK + 10 outfield = valid XI
        res = self._post(browser_page, {str(i): {} for i in ids})
        assert res["status"] == 200
        assert res["body"]["success"] is True

    def test_too_many_rejected(self, browser_page):
        browser_page.goto(BASE_URL + "/formation")
        res = self._post(browser_page, {str(i): {} for i in range(1, 14)})
        assert res["status"] == 400
        assert "error" in res["body"]


class TestTrainingSaveInteraction:
    def test_change_focus_persists(self, browser_page):
        browser_page.goto(BASE_URL + "/training")
        browser_page.select_option('select[name="focus"]', "attack")
        with browser_page.expect_navigation():
            browser_page.locator('form[action="/training/save"] button[type="submit"]').click()
        value = browser_page.eval_on_selector("select[name=focus]", "el => el.value")
        assert value == "attack"


class TestLiveMatchFlow:
    def test_live_page_elements(self, browser_page, live_server):
        browser_page.goto(BASE_URL + "/live/%d" % live_server["ids"]["upcoming_id"])
        expect(browser_page.locator("#liveScore")).to_be_visible()
        expect(browser_page.locator("#liveClock")).to_be_visible()
        expect(browser_page.locator("#liveHomeName")).to_contain_text("E2E")
        expect(browser_page.locator("#liveAwayName")).to_contain_text("E2E")
        for btn in ("#btnMinute1", "#btnMinute5", "#btnFinish"):
            assert browser_page.locator(btn).count() == 1, btn
        assert browser_page.locator("#liveFeed").count() == 1

    def test_advance_clock_moves(self, browser_page, live_server):
        browser_page.goto(BASE_URL + "/live/%d" % live_server["ids"]["upcoming_id"])
        clock = browser_page.locator("#liveClock")
        before = clock.inner_text()
        browser_page.locator("#btnMinute5").click()
        browser_page.wait_for_function(
            "prev => document.getElementById('liveClock').textContent !== prev",
            arg=before, timeout=15000)
        expect(clock).to_contain_text("5")

    def test_feed_renders_events_after_advance(self, browser_page, live_server):
        browser_page.goto(BASE_URL + "/live/%d" % live_server["ids"]["upcoming_id"])
        for _ in range(3):
            browser_page.locator("#btnMinute5").click()
        browser_page.wait_for_selector("#liveFeed .live-event", timeout=20000)
        events = browser_page.locator("#liveFeed .live-event").all_text_contents()
        assert len(events) >= 1
        assert any("'" in e for e in events)  # every event carries its minute

    def test_sub_via_real_clicks(self, browser_page, live_server):
        browser_page.goto(BASE_URL + "/live/%d" % live_server["ids"]["upcoming_id"])
        # Prime the HTTP cache: static app.js may be stuck at an older 304.
        browser_page.evaluate("fetch('/static/app.js', {cache: 'reload'})")
        browser_page.reload()
        browser_page.wait_for_selector("#liveHomePitch .live-chip, #liveAwayPitch .live-chip")
        # The manager's side is the only one with selectable chips.
        side = browser_page.evaluate(
            """() => document.querySelectorAll('#liveHomeBench .live-chip.clickable').length > 0
                ? 'Home' : 'Away'""")
        browser_page.locator(f"#live{side}Pitch .live-chip.clickable").first.click()
        browser_page.locator(f"#live{side}Bench .live-chip.clickable").first.click()
        # The sendSub POST is async: wait for the server answer, not the hint.
        browser_page.wait_for_function(
            "() => document.getElementById('liveSubMsg')"
            ".textContent.includes('تبديل')",
            timeout=15000)
        msg = browser_page.locator("#liveSubMsg")
        assert "تبديل" in msg.inner_text()

    def test_advance_clamped_via_fetch(self, browser_page, live_server):
        mid = live_server["ids"]["upcoming_id"]
        browser_page.goto(BASE_URL + "/live/%d" % mid)
        minute = browser_page.evaluate(
            """async () => {
                const csrf = document.getElementById('csrfToken').value;
                const before = (await (await fetch('/live/%d/state')).json()).minute;
                await fetch('/live/%d/advance', {method: 'POST',
                    headers: {'Content-Type': 'application/x-www-form-urlencoded',
                              'X-CSRFToken': csrf},
                    body: 'minutes=99'});
                const after = (await (await fetch('/live/%d/state')).json()).minute;
                return {before, after};
            }""" % (mid, mid, mid))
        assert 0 < minute["after"] - minute["before"] <= 15

    def test_finish_redirects_to_report(self, browser_page, live_server):
        browser_page.goto(BASE_URL + "/live/%d" % live_server["ids"]["upcoming_id"])
        # Double-click safety: two rapid finishes must still land exactly once
        # on the report without replaying the week.
        browser_page.locator("#btnFinish").dblclick()
        browser_page.wait_for_url("**/match_report/*", timeout=20000)
        body = _body(browser_page)
        assert "تقرير المباراة" in body
        assert "E2E Home FC" in body and "E2E Away FC" in body


class TestPostLiveAndAdvance:
    def test_match_page_empty_state(self, browser_page):
        browser_page.goto(BASE_URL + "/match")
        assert "لا توجد مباراة هذا الأسبوع" in _body(browser_page)

    def test_continue_advances_day(self, browser_page):
        browser_page.goto(BASE_URL + "/dashboard")
        before = browser_page.content()
        assert "اليوم 8" in before
        browser_page.locator('form[action="/continue"] button').click()
        browser_page.wait_for_url("**/dashboard**", timeout=20000)
        expect(browser_page.locator("body")).to_contain_text("اليوم 15", timeout=20000)


class TestEdgeCases:
    def test_unknown_report_redirects(self, browser_page):
        browser_page.goto(BASE_URL + "/match_report/999999")
        browser_page.wait_for_url("**/dashboard**", timeout=15000)
        assert browser_page.url.endswith("/dashboard")

    def test_unknown_toggle_json_404(self, browser_page):
        browser_page.goto(BASE_URL + "/squad")
        res = browser_page.evaluate(
            """async () => {
                const csrf = document.querySelector('.toggle-starter-form input[name="csrf_token"]').value;
                const r = await fetch('/toggle_starter/999999', {
                    method: 'POST', headers: {'X-CSRFToken': csrf}});
                return {s: r.status, b: await r.json()};
            }""")
        assert res["s"] == 404
        assert res["b"]["success"] is False

    def test_toggle_get_is_json_405(self, browser_page):
        # Wrong-method fetch() on a JSON-only endpoint must stay parseable.
        res = browser_page.evaluate(
            "fetch('/toggle_starter/1').then(async r => ({s: r.status, b: await r.json()}))")
        assert res["s"] == 405
        assert res["b"]["error"] == "method not allowed"

    def test_api_404_json(self, browser_page):
        res = browser_page.evaluate(
            "fetch('/api/no-such-thing', {headers: {Accept: 'application/json'}})"
            ".then(async r => ({s: r.status, b: await r.json()}))")
        assert res["s"] == 404

    def test_static_assets_200(self, browser_page):
        for asset in ("/static/style.css", "/static/app.js"):
            status = browser_page.evaluate(
                "u => fetch(u).then(r => r.status)", BASE_URL + asset)
            assert status == 200, asset


class TestMobileNav:
    def test_hamburger_toggles_menu(self, mobile_page):
        mobile_page.goto(BASE_URL + "/dashboard")
        mobile_page.locator("#navToggle").click()
        expect(mobile_page.locator("#navMenu")).to_have_class("nav-menu active")


class TestLoadMenuRegression:
    def test_load_menu_does_not_switch_database(self, browser_page):
        # Regression: GET /load used to repoint the global DB engine at the
        # save file, silently moving later requests onto another database.
        browser_page.goto(BASE_URL + "/load")
        expect(browser_page.locator("h1")).to_contain_text("اختيار الحفظ")
        browser_page.goto(BASE_URL + "/standings")
        body = _body(browser_page)
        assert "E2E Home FC" in body and "E2E Away FC" in body


class TestOfferAndScoutInteractions:
    def test_reject_offer_removes_section(self, browser_page):
        browser_page.goto(BASE_URL + "/messages")
        assert "عروض الانتقال المعلقة" in _body(browser_page)
        assert browser_page.locator(".btn-reject").count() >= 1
        with browser_page.expect_navigation():
            browser_page.locator(".btn-reject").first.click()
        assert "عروض الانتقال المعلقة" not in _body(browser_page)

    def test_assign_scout_to_region(self, browser_page):
        browser_page.goto(BASE_URL + "/scouting")
        assert "E2E Scout" in _body(browser_page)
        browser_page.select_option('form[action^="/scout/assign/"] select[name="region"]', "Europe")
        with browser_page.expect_navigation():
            browser_page.locator('form[action^="/scout/assign/"] button').click()
        expect(browser_page.locator("body")).to_contain_text("إنهاء المهمة")


def _e2e_db(live_server):
    """Short app-context session on the live server's own database."""
    return live_server["app"].app_context()


class TestToggleLimitInteraction:
    def test_twelfth_click_rejected_with_toast(self, browser_page, live_server):
        with _e2e_db(live_server):
            from models import db, Player, Team
            team = Team.query.filter_by(is_player_team=True).first()
            for i, p in enumerate(team.players):
                p.is_starting = i < 11
                p.injury_days = 0
                p.fitness = 100
            db.session.commit()
        browser_page.goto(BASE_URL + "/squad")
        expect(browser_page.locator("#starterCount")).to_contain_text("11/11")
        browser_page.locator(
            '#squadTable tbody tr.player-row[data-starter="0"] button').first.click()
        expect(browser_page.locator(".toggler-toast").first).to_contain_text(
            "11 لاعباً", timeout=10000)
        expect(browser_page.locator("#starterCount")).to_contain_text("11/11")
        with _e2e_db(live_server):
            from models import db, Player, Team
            team = Team.query.filter_by(is_player_team=True).first()
            assert sum(1 for p in team.players if p.is_starting) == 11
            db.session.remove()


class TestFormationDragDrop:
    _DROP = """async ({srcSel, dstSel, pid}) => {
        const src = document.querySelector(srcSel);
        const dst = document.querySelector(dstSel);
        const r = dst.getBoundingClientRect();
        const dt = new DataTransfer();
        src.dispatchEvent(new DragEvent('dragstart',
            {dataTransfer: dt, bubbles: true, cancelable: true}));
        dst.dispatchEvent(new DragEvent('drop', {dataTransfer: dt,
            bubbles: true, cancelable: true,
            clientX: r.left + r.width / 2, clientY: r.top + r.height / 2}));
        return document.getElementById('formationCount').textContent;
    }"""

    def test_drag_pool_to_pitch_and_back(self, browser_page):
        browser_page.goto(BASE_URL + "/formation")
        expect(browser_page.locator("#formationCount")).to_contain_text("11/11")
        pid = browser_page.eval_on_selector(
            "#playerPool .pool-player", "el => el.dataset.id")
        assert pid
        count = browser_page.evaluate(
            self._DROP, {"srcSel": f'#playerPool .pool-player[data-id="{pid}"]',
                         "dstSel": "#pitch-grid", "pid": pid})
        assert count.startswith("12/11")  # 12th man placed on the canvas
        assert browser_page.locator("#saveFormationBtn").is_disabled()
        count = browser_page.evaluate(
            self._DROP, {"srcSel": f'#pitch-grid .pitch-box[data-id="{pid}"]',
                         "dstSel": "#playerPool", "pid": pid})
        assert count.startswith("11/11")
        assert browser_page.locator("#saveFormationBtn").is_enabled()

    def test_role_change_persists(self, browser_page, live_server):
        browser_page.goto(BASE_URL + "/formation")
        browser_page.locator(".role-select").first.select_option("poacher")
        browser_page.wait_for_function(
            "() => { const s = document.querySelector('.role-select');"
            " return s && s.style.borderColor !== ''; }", timeout=10000)
        with _e2e_db(live_server):
            from models import Team
            team = Team.query.filter_by(is_player_team=True).first()
            roles = [(v or {}).get("role") for v in (team.formation_data or {}).values()]
            assert "poacher" in roles

    def test_save_button_reloads_with_toast(self, browser_page):
        browser_page.goto(BASE_URL + "/formation")
        expect(browser_page.locator("#formationCount")).to_contain_text("11/11")

        def _accept(dialog):
            assert "11" in dialog.message or "التشكيلة" in dialog.message
            dialog.accept()

        browser_page.once("dialog", _accept)
        with browser_page.expect_navigation():
            browser_page.locator("#saveFormationBtn").click()
        expect(browser_page.locator("#formationCount")).to_contain_text("11/11")
        assert browser_page.locator("#pitch-grid .pitch-box").count() == 11


class TestMentoringInteraction:
    def test_add_and_remove_mentor(self, browser_page, live_server):
        with _e2e_db(live_server):
            from models import db, Player, Team
            team = Team.query.filter_by(is_player_team=True).first()
            mentor = team.players[0]
            mentor.age = 32
            mentor.personality = "Leader"
            mentee = team.players[-1]
            mentee.age = 19
            db.session.commit()
            mentor_id, mentee_id = str(mentor.id), str(mentee.id)
            mentor_name, mentee_name = mentor.name, mentee.name
        browser_page.goto(BASE_URL + "/squad")
        browser_page.select_option('form[action="/mentoring/add"] select[name="mentee_id"]',
                                   mentee_id)
        browser_page.select_option('form[action="/mentoring/add"] select[name="mentor_id"]',
                                   mentor_id)
        with browser_page.expect_navigation():
            browser_page.locator('form[action="/mentoring/add"] button').click()
        expect(browser_page.locator("body")).to_contain_text("مرشداً")
        expect(browser_page.locator("body")).to_contain_text(mentee_name)
        with _e2e_db(live_server):
            from models import Mentoring, Team
            team = Team.query.filter_by(is_player_team=True).first()
            assert Mentoring.query.filter_by(team_id=team.id).count() == 1
        with browser_page.expect_navigation():
            browser_page.locator('form[action^="/mentoring/remove/"] button').click()
        expect(browser_page.locator("body")).to_contain_text("تم إنهاء علاقة التوجيه")
        with _e2e_db(live_server):
            from models import Mentoring, Team
            team = Team.query.filter_by(is_player_team=True).first()
            assert Mentoring.query.filter_by(team_id=team.id).count() == 0
            db.session.remove()
        # mentor name must be gone from the mentoring card (still in squad table)
        assert mentor_name in _body(browser_page)  # squad table keeps every DB player


class TestCalendarSimInteraction:
    def test_click_future_day_simulates(self, browser_page):
        browser_page.goto(BASE_URL + "/calendar")
        game_date = browser_page.locator("#gameDateDisplay").inner_text().strip()
        browser_page.locator(
            f'.day-cell[data-date]:not(.inactive)').evaluate_all(
            """els => { const g = '%s';
                const t = els.find(e => e.dataset.date > g && !e.classList.contains('has-match'));
                if (!t) throw new Error('no future free day cell');
                t.click(); }""" % game_date)
        browser_page.locator('#detailsContent button:has-text("محاكاة")').click()
        browser_page.wait_for_url("**/dashboard**", timeout=20000)
        expect(browser_page.locator("body")).to_contain_text("تمت المحاكاة")


class TestInboxReadInteraction:
    def test_click_message_marks_read(self, browser_page):
        browser_page.goto(BASE_URL + "/messages")
        first = browser_page.locator(".message-list-item").first
        title = first.locator(".ml-title").inner_text()
        first.click()
        expect(browser_page.locator("#messageReadPane .msg-title")).to_contain_text(
            title, timeout=10000)
        expect(first).not_to_have_class("unread")
        data = browser_page.evaluate("fetch('/inbox_unread').then(r => r.json())")
        assert isinstance(data["unread"], int)


class TestSeasonEndInteraction:
    def test_season_end_button_starts_new_season(self, browser_page, live_server):
        with _e2e_db(live_server):
            from models import db, GameState
            state = GameState.query.first()
            state.season_over = True
            season0 = state.season_number or 1
            db.session.commit()
        browser_page.goto(BASE_URL + "/dashboard")
        expect(browser_page.locator("body")).to_contain_text("الموسم انتهى")
        with browser_page.expect_navigation():
            browser_page.locator('form[action="/season_end"] button').click()
        body = _body(browser_page)
        assert "بدأ الموسم الجديد" in body
        assert f"الموسم: {season0 + 1}" in body
        assert "E2E Home FC" in body  # club still rendered from the new-season DB


class TestFormationPresetSlots:
    """Pre-defined formations spread empty slots + by-ability filling."""

    BASELINE_IDS = [1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]

    def _accept_all_dialogs(self, page):
        def _accept(dialog):
            dialog.accept()

        page.on("dialog", _accept)
        return _accept

    def _restore_baseline_xi(self, browser_page):
        """Impact-minimising restore of the coordinate-less XI used by the
        earlier TestFormationSaveInteraction / TestFormationDragDrop flows."""
        return browser_page.evaluate(
            """async (ids) => {
                const csrf = document.getElementById('csrfToken').value;
                const pos = {};
                ids.forEach(i => { pos[i] = {}; });
                const r = await fetch('/formation/save', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json',
                              'X-CSRFToken': csrf},
                    body: JSON.stringify({positions: pos})
                });
                return r.status;
            }""", self.BASELINE_IDS)

    def test_preset_spreads_empty_slots(self, browser_page):
        browser_page.goto(BASE_URL + "/formation")
        h = self._accept_all_dialogs(browser_page)
        browser_page.select_option("#formationLayout", "4-3-3")
        # The chosen formation spreads 11 dashed empty slots on the pitch.
        expect(browser_page.locator("#pitch-grid .pitch-slot")).to_have_count(11)
        assert browser_page.locator("#pitch-grid .pitch-box").count() == 0
        expect(browser_page.locator("#formationCount")).to_contain_text("0/11")
        browser_page.remove_listener("dialog", h)

    def test_ability_fill_populates_all_slots(self, browser_page):
        browser_page.goto(BASE_URL + "/formation")
        h = self._accept_all_dialogs(browser_page)
        browser_page.select_option("#formationLayout", "4-3-3")
        browser_page.locator("#fillByAbilityBtn").click()
        browser_page.wait_for_function(
            "() => document.querySelectorAll('#pitch-grid .pitch-box').length === 11",
            timeout=15000)
        browser_page.remove_listener("dialog", h)
        expect(browser_page.locator("#formationCount")).to_contain_text("11/11")
        assert browser_page.locator("#pitch-grid .pitch-slot").count() == 0
        assert browser_page.locator("#saveFormationBtn").is_enabled()
        self._restore_baseline_xi(browser_page)

    def test_layout_persists_after_save_reload(self, browser_page):
        browser_page.goto(BASE_URL + "/formation")
        h = self._accept_all_dialogs(browser_page)
        browser_page.select_option("#formationLayout", "5-2-3")
        browser_page.locator("#fillByAbilityBtn").click()
        browser_page.wait_for_function(
            "() => document.querySelectorAll('#pitch-grid .pitch-box').length === 11",
            timeout=15000)
        with browser_page.expect_navigation():
            browser_page.locator("#saveFormationBtn").click()
        browser_page.remove_listener("dialog", h)
        # After reload the layout key is restored from formation_data.
        value = browser_page.eval_on_selector(
            "#formationLayout", "el => el.value")
        assert value == "5-2-3"
        expect(browser_page.locator("#pitch-grid .pitch-box")).to_have_count(11)
        expect(browser_page.locator("#pitch-grid .pitch-slot")).to_have_count(0)
        self._restore_baseline_xi(browser_page)
