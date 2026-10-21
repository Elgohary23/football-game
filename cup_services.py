"""Cup & continental tournament services (draw engine, scheduling, progression).

Domestic cups (FA Cup, League Cup, National Cup, BSM Trophy, Super Cup) and
the three continental competitions (Champions League, Europa League,
Conference League) live here so route handlers stay thin.

Design notes:

- Cup fixtures are ``Match`` rows with ``week == CUP_MATCH_WEEK`` (0). The
  league engine only queries ``week >= 1``, so cups never leak into league
  tables, and ``simulate_match`` / ``finish_live`` skip the league-table
  update for cup matches (guarded centrally, see ``is_cup_match``).
- Knockout draws are unseeded and random before every round. Draws are
  resolved by extra time / penalties (random winner) so every tie has a
  winner; two-legged ties use the aggregate score.
- Non-power-of-2 draws grant byes to the highest-reputation teams. A bye is
  a ``TournamentFixture`` with ``match_id IS NULL`` and ``winner_team_id``
  set, so "round winners" is always "fixtures of the round with a winner".
- Continental competitions use the modern Swiss / league-phase format:
  a single 36-team table, 8 matches per team (6 for the Conference League),
  1-8 straight to the Round of 16, 9-24 via two-legged play-offs.
"""

import logging
import random
from datetime import datetime, timedelta

from config import (
    CONFERENCE_LEAGUE_MATCHES,
    CONTINENTAL_DIRECT_SPOTS,
    CONTINENTAL_PLAYOFF_SPOTS,
    CONTINENTAL_SIZE,
    CUP_LEG_SPACING_DAYS,
    CUP_MATCH_WEEK,
    CUP_NAMES,
    CUP_ROUND_SPACING_DAYS,
    CUP_SCOPES,
    CUP_TYPES,
    CONTINENTAL_PHASE_SPACING_DAYS,
    UCL_LEAGUE_MATCHES,
    UEL_LEAGUE_MATCHES,
)
from models import (
    db,
    ContinentalStandings,
    GameState,
    League,
    Match,
    Message,
    Team,
    Tournament,
    TournamentFixture,
)

logger = logging.getLogger(__name__)


ROUND_LABELS = {
    "Preliminary Round": "الدور التمهيدي",
    "Play-off": "الملحق",
    "Round of 64": "دور الـ64",
    "Round of 32": "دور الـ32",
    "Round of 16": "دور الـ16",
    "Quarter-final": "ربع النهائي",
    "Semi-final": "نصف النهائي",
    "Final": "النهائي",
    "Group Stage": "دور المجموعات",
    "League Phase": "مرحلة الدوري",
    "Round 1": "الدور الأول",
    "Complete": "انتهت",
}


def round_label(round_name: str) -> str:
    return ROUND_LABELS.get(round_name or "", round_name or "")


def cup_name(cup_type: str) -> str:
    return CUP_NAMES.get(cup_type or "", cup_type or "")


# --------------------------------------------------------------------------
# Basics
# --------------------------------------------------------------------------
def is_cup_match(match: Match) -> bool:
    """A match is a cup fixture when it lives in week 0 or is linked to a
    tournament. Central guard so cup results never touch league tables."""
    try:
        if match is None:
            return False
        if (getattr(match, "week", None) or 0) == CUP_MATCH_WEEK:
            return True
        return (
            TournamentFixture.query.filter_by(match_id=match.id).first()
            is not None
        )
    except Exception:
        return False


def next_power_of_two(n: int) -> int:
    p = 1
    while p < max(1, n):
        p *= 2
    return p


def round_name_for(team_count: int) -> str:
    mapping = {
        2: "Final",
        4: "Semi-final",
        8: "Quarter-final",
        16: "Round of 16",
        32: "Round of 32",
        64: "Round of 64",
    }
    return mapping.get(team_count, f"Round of {team_count}")


def _shift_date(base: str | None, days: int) -> str:
    try:
        d = datetime.strptime(base, "%Y-%m-%d").date() if base else datetime.today().date()
    except Exception:
        d = datetime.today().date()
    return (d + timedelta(days=days)).isoformat()


def _current_iso_date() -> str:
    try:
        state = GameState.query.first()
        if state and state.current_date:
            return state.current_date
    except Exception:
        pass
    return datetime.today().date().isoformat()


def _competition_type(tournament: Tournament | None) -> str:
    """Match ``comp_type`` bucket: league / domestic_cup / continental."""
    return "continental" if getattr(tournament, "scope", "") == "continental" else "domestic_cup"


def _cup_fixture_date(value: str | None) -> str | None:
    """Force cup/European ties onto a legal midweek slot (FIFA-break aware)."""
    try:
        from services import midweek_fixture_date
        return midweek_fixture_date(value)
    except Exception:
        return value


def _reputation(team: Team) -> int:
    try:
        return int(team.reputation or 0)
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------
# Draw engine (spec API)
# --------------------------------------------------------------------------
def generate_knockout_round(tournament_id, qualified_team_ids, round_name,
                            match_date, bye_team_ids=None):
    """Generates random knockout pairings for a given round.

    ``bye_team_ids`` (optional list) collects teams handed a bye when the
    entrant count is odd — callers carry them into the next round.
    """
    ids = list(dict.fromkeys(qualified_team_ids))
    random.shuffle(ids)
    created_fixtures = []

    if len(ids) % 2 == 1 and ids:
        bye = max(ids, key=lambda tid: _reputation(db.session.get(Team, tid)))
        ids.remove(bye)
        if bye_team_ids is not None:
            bye_team_ids.append(bye)

    tournament = db.session.get(Tournament, tournament_id)
    for i in range(0, len(ids) - 1, 2):
        home_id = ids[i]
        away_id = ids[i + 1]
        match = Match(
            home_team_id=home_id,
            away_team_id=away_id,
            played=False,
            date=match_date,
            week=CUP_MATCH_WEEK,
            comp_type=_competition_type(tournament),
        )
        db.session.add(match)
        db.session.flush()

        fixture = TournamentFixture(
            tournament_id=tournament_id,
            match_id=match.id,
            round_name=round_name,
        )
        db.session.add(fixture)
        created_fixtures.append(fixture)

    db.session.flush()
    return created_fixtures


def process_cup_match_winner(match):
    """Determines the winner of a cup match, resolving draws via penalty
    shootout logic."""
    if match.home_score > match.away_score:
        return match.home_team_id
    elif match.away_score > match.home_score:
        return match.away_team_id
    else:
        # Resolve ties randomly or via penalty shootout simulation
        return random.choice([match.home_team_id, match.away_team_id])


# --------------------------------------------------------------------------
# Eligibility
# --------------------------------------------------------------------------
def _teams_in(country: str | None, tiers: tuple[int, ...]) -> list[Team]:
    q = Team.query.join(League, Team.league_id == League.id)
    if country:
        q = q.filter(League.country == country)
    if tiers:
        q = q.filter(League.tier.in_(tiers))
    return q.all()


def eligible_teams(cup_type: str, country: str | None = None) -> list[Team]:
    """Teams allowed to enter a competition (spec section 1 + 2)."""
    if cup_type == "fa_cup":
        return _teams_in(country or "England", (1, 2, 3, 4))
    if cup_type == "league_cup":
        return _teams_in(country or "England", (1,))
    if cup_type == "national_cup":
        return _teams_in(country, ())
    if cup_type == "bsm":
        return _teams_in(country or "England", (3, 4))
    if cup_type in ("champions_league", "europa_league", "conference_league"):
        return _continental_pool(cup_type)
    return []


def _tier1_by_reputation(limit: int, exclude_ids: set[int] | None = None) -> list[Team]:
    q = (
        Team.query.join(League, Team.league_id == League.id)
        .filter(League.tier == 1)
        .order_by(Team.reputation.desc(), Team.id)
    )
    teams = q.all()
    if exclude_ids:
        teams = [t for t in teams if t.id not in exclude_ids]
    return teams[:limit]


def _continental_pool(cup_type: str) -> list[Team]:
    """36-team fields: England's qualifiers plus the best tier-1 clubs
    across Europe by reputation (no club in two continentals)."""
    eng1 = (
        Team.query.join(League, Team.league_id == League.id)
        .filter(League.country == "England", League.tier == 1)
        .order_by(Team.reputation.desc(), Team.id)
        .all()
    )
    if cup_type == "champions_league":
        eng = [t.id for t in eng1[:4]]
    elif cup_type == "europa_league":
        eng = [t.id for t in eng1[4:7]]
    else:
        eng = [t.id for t in eng1[7:8]]
    picked = list(eng)
    exclude = set(picked)
    for t in _tier1_by_reputation(CONTINENTAL_SIZE, exclude):
        picked.append(t.id)
        if len(picked) >= CONTINENTAL_SIZE:
            break
    teams = [db.session.get(Team, tid) for tid in picked]
    return [t for t in teams if t is not None]


def _league_phase_matches(cup_type: str) -> int:
    if cup_type == "conference_league":
        return CONFERENCE_LEAGUE_MATCHES
    if cup_type == "europa_league":
        return UEL_LEAGUE_MATCHES
    return UCL_LEAGUE_MATCHES


# --------------------------------------------------------------------------
# Tournament creation / seeding
# --------------------------------------------------------------------------
def create_tournament(cup_type: str, season: int,
                      country: str | None = None) -> Tournament:
    scope = CUP_SCOPES.get(cup_type, "domestic")
    name = cup_name(cup_type)
    if cup_type == "national_cup" and country and country != "England":
        name = f"كأس {country}"
    t = Tournament(
        name=name,
        type=cup_type,
        scope=scope,
        tier_restriction={"fa_cup": "1,2,3,4", "league_cup": "1",
                          "bsm": "3,4"}.get(cup_type),
        current_round="Round 1",
        is_active=True,
        season=season,
        country=country,
    )
    db.session.add(t)
    db.session.flush()
    return t


def _record_bye(tournament_id: int, team_id: int, round_name: str) -> TournamentFixture:
    f = TournamentFixture(
        tournament_id=tournament_id,
        match_id=None,
        round_name=round_name,
        winner_team_id=team_id,
    )
    db.session.add(f)
    db.session.flush()
    return f


def seed_knockout(tournament: Tournament, team_ids: list[int],
                  base_date: str) -> None:
    """Seed a single-elimination bracket with byes for non-power-of-2 fields.

    Lower tiers enter early: tiers 3-4 play the preliminary round while
    tiers 1-2 join the main draw (FA Cup seeding rule).
    """
    ids = list(dict.fromkeys(team_ids))
    if len(ids) < 2:
        if ids:
            tournament.winner_team_id = ids[0]
            tournament.is_active = False
            tournament.current_round = "Complete"
        return
    target = next_power_of_two(len(ids))
    byes = target - len(ids)

    team_map = {t.id: t for t in Team.query.filter(Team.id.in_(ids)).all()}
    if tournament.type == "fa_cup":
        def _tier(tid):
            t = team_map.get(tid)
            try:
                return t.league.tier if t and t.league else 9
            except Exception:
                return 9
        # Tiers 3-4 start in the preliminary round; tiers 1-2 join later.
        early = sorted([i for i in ids if _tier(i) >= 3],
                       key=lambda i: _reputation(team_map.get(i)))
        late = [i for i in ids if _tier(i) < 3]
        prelim_need = len(ids) - byes - len(late)
        prelim_need = max(0, min(len(early), prelim_need if prelim_need % 2 == 0 else prelim_need + 1))
        if prelim_need >= 2:
            prelim = early[:prelim_need]
            bye_early = early[prelim_need:]
            main_byes = late + bye_early
            prelim_date = _cup_fixture_date(_shift_date(base_date, CUP_ROUND_SPACING_DAYS))
            generate_knockout_round(tournament.id, prelim, "Preliminary Round", prelim_date)
            for tid in main_byes:
                _record_bye(tournament.id, tid, "Preliminary Round")
            tournament.current_round = "Preliminary Round"
            db.session.flush()
            return
    # Generic path: byes go to the highest-reputation teams.
    ordered = sorted(ids, key=lambda i: _reputation(team_map.get(i)), reverse=True)
    bye_ids = ordered[:byes]
    players = ordered[byes:]
    first_date = _cup_fixture_date(_shift_date(base_date, CUP_ROUND_SPACING_DAYS))
    if players:
        first_round = round_name_for(target) if byes else round_name_for(len(ids))
        if byes:
            # Prelim trims the field down to the main-draw size.
            generate_knockout_round(tournament.id, players, "Preliminary Round", first_date)
            for tid in bye_ids:
                _record_bye(tournament.id, tid, "Preliminary Round")
            tournament.current_round = "Preliminary Round"
        else:
            generate_knockout_round(tournament.id, players, first_round, first_date)
            tournament.current_round = first_round
    elif bye_ids:
        nxt = round_name_for(len(bye_ids))
        for tid in bye_ids:
            _record_bye(tournament.id, tid, "Preliminary Round")
        tournament.current_round = nxt
    db.session.flush()


def seed_bsm_groups(tournament: Tournament, team_ids: list[int],
                    base_date: str) -> None:
    """Group stage (4-team North/South groups) then knockout for the top two
    of each group."""
    ids = list(dict.fromkeys(team_ids))
    if len(ids) < 2:
        if ids:
            tournament.winner_team_id = ids[0]
            tournament.is_active = False
            tournament.current_round = "Complete"
        return
    random.shuffle(ids)
    groups: list[list[int]] = []
    for i in range(0, len(ids), 4):
        groups.append(ids[i:i + 4])
    if len(groups) > 1 and len(groups[-1]) < 3:
        leftover = groups.pop()
        for j, tid in enumerate(leftover):
            groups[j % len(groups)].append(tid)
    halves = ["North", "South"]
    for gi, group in enumerate(groups):
        gname = f"{halves[gi % 2]} {chr(65 + gi // 2)}" if len(groups) > 2 else f"Group {chr(65 + gi)}"
        for a in range(len(group)):
            for b in range(a + 1, len(group)):
                home_id, away_id = group[a], group[b]
                if (a + b) % 2 == 1:
                    home_id, away_id = away_id, home_id
                m = Match(
                    home_team_id=home_id,
                    away_team_id=away_id,
                    played=False,
                    date=_cup_fixture_date(
                        _shift_date(base_date, CUP_ROUND_SPACING_DAYS + (a * 7 + b))
                    ),
                    week=CUP_MATCH_WEEK,
                    comp_type=_competition_type(tournament),
                )
                db.session.add(m)
                db.session.flush()
                db.session.add(TournamentFixture(
                    tournament_id=tournament.id,
                    match_id=m.id,
                    round_name="Group Stage",
                    group_name=gname,
                ))
    tournament.current_round = "Group Stage"
    db.session.flush()


def seed_continental_league_phase(tournament: Tournament, team_ids: list[int],
                                  base_date: str) -> None:
    """Swiss / league phase: every team plays N different opponents."""
    ids = list(dict.fromkeys(team_ids))
    per_team = _league_phase_matches(tournament.type)
    if len(ids) < 2:
        if ids:
            tournament.winner_team_id = ids[0]
            tournament.is_active = False
            tournament.current_round = "Complete"
        return
    per_team = min(per_team, len(ids) - 1)
    for tid in ids:
        db.session.add(ContinentalStandings(tournament_id=tournament.id, team_id=tid))
    db.session.flush()

    # Circle method: with an even field, the first K rounds never repeat a
    # pairing, so every team gets K distinct opponents.
    circle = list(ids)
    if len(circle) % 2 == 1:
        circle.append(None)
    n = len(circle)
    met: set[tuple[int, int]] = set()
    rounds_done = 0
    rotation = list(circle)
    while rounds_done < per_team:
        # Matchdays 1-8 span September → January: two-week European
        # rhythm, aligned to Tuesday/Wednesday and skipping FIFA breaks.
        week_date = _cup_fixture_date(
            _shift_date(base_date, CONTINENTAL_PHASE_SPACING_DAYS * (rounds_done + 2))
        )
        matchday = rounds_done + 1
        for i in range(n // 2):
            a, b = rotation[i], rotation[n - 1 - i]
            if a is None or b is None:
                continue
            key = (min(a, b), max(a, b))
            if key in met:
                continue
            met.add(key)
            home_id, away_id = (a, b) if rounds_done % 2 == 0 else (b, a)
            m = Match(
                home_team_id=home_id,
                away_team_id=away_id,
                played=False,
                date=week_date,
                week=CUP_MATCH_WEEK,
                comp_type="continental",
                matchday=matchday,
            )
            db.session.add(m)
            db.session.flush()
            db.session.add(TournamentFixture(
                tournament_id=tournament.id,
                match_id=m.id,
                round_name="League Phase",
            ))
        rotation = [rotation[0]] + [rotation[-1]] + rotation[1:-1]
        rounds_done += 1
    tournament.current_round = "League Phase"
    db.session.flush()


def seed_super_cup(tournament: Tournament, team_a_id: int, team_b_id: int,
                   base_date: str) -> None:
    """Season-opening single match: champion vs cup winner (or runner-up
    when one club won both — the double-winner rule)."""
    m = Match(
        home_team_id=team_a_id,
        away_team_id=team_b_id,
        played=False,
        date=_cup_fixture_date(_shift_date(base_date, 3)),
        week=CUP_MATCH_WEEK,
        comp_type=_competition_type(tournament),
    )
    db.session.add(m)
    db.session.flush()
    db.session.add(TournamentFixture(
        tournament_id=tournament.id,
        match_id=m.id,
        round_name="Final",
    ))
    tournament.current_round = "Final"
    db.session.flush()


# --------------------------------------------------------------------------
# Season setup
# --------------------------------------------------------------------------
def _season() -> int:
    try:
        state = GameState.query.first()
        return int(state.season_number or 1)
    except Exception:
        return 1


def seed_initial_cups(season: int | None = None,
                       base_date: str | None = None) -> list[Tournament]:
    """Create every competition for a fresh world. Idempotent per season."""
    season = season or _season()
    base_date = base_date or _current_iso_date()
    created: list[Tournament] = []
    if Tournament.query.filter_by(season=season).first():
        return created

    for cup_type in ("fa_cup", "league_cup", "bsm"):
        teams = eligible_teams(cup_type)
        if len(teams) < 2:
            continue
        t = create_tournament(cup_type, season, country="England")
        if cup_type == "bsm":
            seed_bsm_groups(t, [x.id for x in teams], base_date)
        else:
            seed_knockout(t, [x.id for x in teams], base_date)
        created.append(t)

    # National cups for every other registered country.
    try:
        countries = [r[0] for r in db.session.query(League.country).distinct().all()]
    except Exception:
        countries = []
    for country in countries:
        if not country or country == "England":
            continue
        teams = eligible_teams("national_cup", country=country)
        if len(teams) < 2:
            continue
        t = create_tournament("national_cup", season, country=country)
        seed_knockout(t, [x.id for x in teams], base_date)
        created.append(t)

    for cup_type in ("champions_league", "europa_league", "conference_league"):
        teams = eligible_teams(cup_type)
        if len(teams) < 2:
            continue
        t = create_tournament(cup_type, season)
        seed_continental_league_phase(t, [x.id for x in teams], base_date)
        created.append(t)

    _seed_super_cup(season, base_date, created)
    return created


def _seed_super_cup(season: int, base_date: str,
                    created: list[Tournament]) -> None:
    """Season opener: league champion vs FA Cup winner.

    No previous season exists for season 1, so the two strongest tier-1
    English clubs open the campaign instead (double-winner rule: same club
    winning both faces the league runner-up)."""
    prev_champ = prev_cup = None
    if season > 1:
        prev_league = (
            Tournament.query.filter_by(type="super_cup", season=season - 1).first()
        )
        _ = prev_league
        champ_t = (
            Tournament.query.filter_by(type="fa_cup", season=season - 1, is_active=False).first()
        )
        if champ_t and champ_t.winner_team_id:
            prev_cup = champ_t.winner_team_id
        try:
            eng1 = (
                League.query.filter_by(country="England", tier=1).first()
            )
            if eng1:
                table = Team.table_sort_pipeline(
                    Team.query.filter_by(league_id=eng1.id).all()
                )
                if table:
                    prev_champ = table[0].id
        except Exception:
            pass
    if prev_champ is None or prev_cup is None:
        eng1 = _teams_in("England", (1,))
        if len(eng1) < 2:
            return
        ordered = sorted(eng1, key=_reputation, reverse=True)
        prev_champ, prev_cup = ordered[0].id, ordered[1].id
    if prev_champ == prev_cup:
        # Double winner: face the league runner-up.
        eng1 = sorted(_teams_in("England", (1,)), key=_reputation, reverse=True)
        runner = next((t.id for t in eng1 if t.id != prev_champ), prev_cup)
        prev_cup = runner
    t = create_tournament("super_cup", season, country="England")
    seed_super_cup(t, prev_champ, prev_cup, base_date)
    created.append(t)


def ensure_cups_for_state() -> list[Tournament]:
    """Idempotent per-season bootstrap for saves created before cups existed
    (or tiny test worlds). Creates only when the season has no tournaments."""
    season = _season()
    if Tournament.query.filter_by(season=season).first():
        return []
    # Continental draws need a real pyramid; tiny fixtures still work.
    return seed_initial_cups(season=season)


def close_season_tournaments(season: int, state: GameState | None = None) -> None:
    """Crown whatever is decided and deactivate every edition of a season.

    Called at season rollover BEFORE leftover unplayed fixtures are wiped,
    so decided champions are still recorded. The caller owns commit.
    """
    try:
        tournaments = Tournament.query.filter_by(season=season, is_active=True).all()
    except Exception:
        return
    for t in tournaments:
        try:
            _try_crown(t, state)
        except Exception:
            logger.exception("cup rollover crown failed for %s", t.id)
        t.is_active = False


def start_new_season_cups(state: GameState) -> list[Tournament]:
    """Rollover: crown anything still undecided, close the season, and draw
    the next season's editions (continental qualification uses the final
    league tables, so call AFTER promotion/relegation)."""
    for t in Tournament.query.filter_by(season=state.season_number or 1,
                                        is_active=True).all():
        try:
            _try_crown(t, state)
        except Exception:
            logger.exception("cup rollover crown failed for %s", t.id)
        t.is_active = False
    db.session.flush()
    new_season = (state.season_number or 1) + 1
    base = getattr(state, "current_date", None) or _current_iso_date()
    return seed_initial_cups(season=new_season, base_date=base)


# --------------------------------------------------------------------------
# Playing cup fixtures
# --------------------------------------------------------------------------
def _involves_player(match: Match) -> bool:
    try:
        return bool(
            (match.home_team and match.home_team.is_player_team)
            or (match.away_team and match.away_team.is_player_team)
        )
    except Exception:
        return False


def _play_one(match: Match, state: GameState, background: bool) -> bool:
    if match.played:
        return False
    if _involves_player(match) and not background:
        return False
    if _involves_player(match):
        try:
            from live_match import run_auto_match
            ok = run_auto_match(match)
        except Exception:
            ok = False
        if ok:
            try:
                from simulation import update_manager_record
                update_manager_record(match, state)
            except Exception:
                pass
        return bool(ok)
    try:
        from simulation import match_director
        return bool(match_director(match, state))
    except Exception:
        logger.exception("cup match %s failed", getattr(match, "id", "?"))
        return False


def get_current_cup_match(state: GameState) -> Match | None:
    """Next unplayed cup fixture involving the player's club that is due."""
    try:
        today = state.current_date or ""
        q = (
            Match.query.join(TournamentFixture, TournamentFixture.match_id == Match.id)
            .join(Tournament, Tournament.id == TournamentFixture.tournament_id)
            .filter(Match.played.is_(False),
                    Tournament.is_active.is_(True))
            .order_by(Match.date, Match.id)
            .all()
        )
        for m in q:
            if m.date and m.date > today:
                continue
            if _involves_player(m):
                return m
        return None
    except Exception:
        return None


def play_due_cup_fixtures(state: GameState,
                          background: bool = False) -> dict:
    """Play every due cup fixture.

    Foreground (daily step-through) leaves the manager's own ties for live
    play and returns them as ``pending``; background fast-forward plays
    everything. The caller owns commit.
    """
    res = {"played": 0, "pending": []}
    try:
        today = state.current_date or ""
        fixtures = (
            TournamentFixture.query.join(Match, TournamentFixture.match_id == Match.id)
            .join(Tournament, Tournament.id == TournamentFixture.tournament_id)
            .filter(Match.played.is_(False), Tournament.is_active.is_(True))
            .order_by(Match.date, Match.id)
            .all()
        )
    except Exception:
        return res
    for fx in fixtures:
        try:
            m = fx.match
            if m is None or m.played:
                continue
            if m.date and today and m.date > today:
                continue
            if _involves_player(m) and not background:
                res["pending"].append(m)
                continue
            savepoint = db.session.begin_nested()
            try:
                if _play_one(m, state, background=True):
                    res["played"] += 1
                after_match_played(m, state)
                savepoint.commit()
            except Exception:
                try:
                    savepoint.rollback()
                except Exception:
                    pass
                logger.exception("cup fixture %s failed", getattr(fx, "id", "?"))
        except Exception:
            continue
    return res


def _continental_row(tournament_id: int, team_id: int) -> ContinentalStandings:
    row = ContinentalStandings.query.filter_by(
        tournament_id=tournament_id, team_id=team_id).first()
    if row is None:
        row = ContinentalStandings(tournament_id=tournament_id, team_id=team_id)
        db.session.add(row)
        db.session.flush()
    return row


def update_continental_standings(tournament: Tournament, match: Match) -> None:
    hs, aws = match.home_score or 0, match.away_score or 0
    home = _continental_row(tournament.id, match.home_team_id)
    away = _continental_row(tournament.id, match.away_team_id)
    for row, gf, ga in ((home, hs, aws), (away, aws, hs)):
        row.played += 1
        row.goals_for += gf
        row.goals_against += ga
    if hs > aws:
        home.wins += 1
        home.points += 3
        away.losses += 1
    elif aws > hs:
        away.wins += 1
        away.points += 3
        home.losses += 1
    else:
        home.draws += 1
        away.draws += 1
        home.points += 1
        away.points += 1


def continental_table(tournament_id: int) -> list:
    rows = ContinentalStandings.query.filter_by(tournament_id=tournament_id).all()
    return sorted(
        rows,
        key=lambda r: (r.points, (r.goals_for or 0) - (r.goals_against or 0),
                       r.goals_for or 0),
        reverse=True,
    )


def _sibling_leg(fixture: TournamentFixture) -> TournamentFixture | None:
    if not fixture.group_name or (fixture.leg or 1) not in (1, 2):
        return None
    return (
        TournamentFixture.query.filter(
            TournamentFixture.tournament_id == fixture.tournament_id,
            TournamentFixture.round_name == fixture.round_name,
            TournamentFixture.group_name == fixture.group_name,
            TournamentFixture.id != fixture.id,
        ).first()
    )


def _resolve_two_legged(fixture: TournamentFixture) -> int | None:
    """Aggregate winner once both legs are played (penalties on level)."""
    other = _sibling_leg(fixture)
    if other is None or other.match is None or fixture.match is None:
        return None
    if not other.match.played or not fixture.match.played:
        return None
    first = fixture if (fixture.leg or 1) == 1 else other
    second = other if (fixture.leg or 1) == 1 else fixture
    home_id = first.match.home_team_id
    agg_home = (first.match.home_score or 0) + (second.match.away_score or 0)
    agg_away = (first.match.away_score or 0) + (second.match.home_score or 0)
    first.aggregate_home_score = agg_home
    first.aggregate_away_score = agg_away
    second.aggregate_home_score = agg_home
    second.aggregate_away_score = agg_away
    if agg_home > agg_away:
        winner = home_id
    elif agg_away > agg_home:
        winner = second.match.home_team_id
    else:
        winner = random.choice([home_id, second.match.home_team_id])
    first.winner_team_id = winner
    second.winner_team_id = winner
    return winner


def _cup_message(title: str, content: str, day: int, urgent: bool = False) -> None:
    try:
        db.session.add(Message(title=title, content=content, type="cup",
                               created_day=day, urgent=urgent))
    except Exception:
        pass


def after_match_played(match: Match, state: GameState | None = None) -> int | None:
    """Record the winner of a finished cup tie and advance the tournament.

    Idempotent: replaying a decided fixture returns its winner without
    side effects. Returns the winning team id (or None while a two-legged
    tie still awaits its second leg).
    """
    fixture = TournamentFixture.query.filter_by(match_id=match.id).first()
    if fixture is None or not match.played:
        return None
    if fixture.winner_team_id:
        return fixture.winner_team_id
    tournament = fixture.tournament
    if tournament is None:
        return None
    rnd = fixture.round_name or ""

    if rnd == "League Phase" and tournament.scope == "continental":
        try:
            update_continental_standings(tournament, match)
        except Exception:
            logger.exception("continental standings update failed")

    winner = None
    if rnd in ("Group Stage", "League Phase"):
        winner = process_cup_match_winner(match)
        fixture.winner_team_id = winner
    elif (fixture.leg or 1) == 2 or _sibling_leg(fixture) is not None:
        winner = _resolve_two_legged(fixture)
        if winner is None:
            return None
    else:
        winner = process_cup_match_winner(match)
        fixture.winner_team_id = winner

    # Small morale swing for knockout football.
    try:
        wteam = db.session.get(Team, winner) if winner else None
        loser_id = (match.away_team_id if winner == match.home_team_id
                    else match.home_team_id)
        lteam = db.session.get(Team, loser_id)
        if wteam is not None:
            wteam.morale = min(100, (wteam.morale or 50) + 3)
        if lteam is not None:
            lteam.morale = max(0, (lteam.morale or 50) - 2)
    except Exception:
        pass

    try:
        day = state.current_day if state else 0
        if _involves_player(match):
            wname = (db.session.get(Team, winner).name if winner else "?")
            _cup_message(
                f"{tournament.name}",
                f"فريقك {'تأهل' if winner and db.session.get(Team, winner).is_player_team else 'خرج'} "
                f"({match.home_team.name} {match.home_score}-{match.away_score} "
                f"{match.away_team.name}) — الفائز: {wname}",
                day,
            )
    except Exception:
        pass

    try:
        _maybe_advance(tournament, state)
    except Exception:
        logger.exception("cup advance failed for tournament %s", tournament.id)
    return winner


def _round_fixtures(tournament_id: int, round_name: str) -> list[TournamentFixture]:
    return TournamentFixture.query.filter_by(
        tournament_id=tournament_id, round_name=round_name).all()


def _round_complete(tournament_id: int, round_name: str) -> bool:
    for fx in _round_fixtures(tournament_id, round_name):
        if fx.match_id is None:
            if not fx.winner_team_id:
                return False
            continue
        m = fx.match
        if m is None or not m.played or not fx.winner_team_id:
            # Second legs resolve only when both legs are done.
            if m is not None and m.played and _sibling_leg(fx) is not None:
                return False
            if m is None or not m.played:
                return False
            return False
    return True


def _round_winners(tournament_id: int, round_name: str) -> list[int]:
    winners = []
    for fx in _round_fixtures(tournament_id, round_name):
        if fx.winner_team_id and fx.winner_team_id not in winners:
            # Two-legged ties store the winner on both legs — dedupe.
            winners.append(fx.winner_team_id)
    return winners


def _next_date(state: GameState | None, days: int = CUP_ROUND_SPACING_DAYS) -> str:
    base = (getattr(state, "current_date", None) if state else None) or _current_iso_date()
    return _cup_fixture_date(_shift_date(base, days))


def _maybe_advance(tournament: Tournament, state: GameState | None) -> None:
    rnd = tournament.current_round or ""
    if not tournament.is_active or rnd in ("", "Complete"):
        return
    if rnd == "Group Stage":
        if _round_complete(tournament.id, "Group Stage"):
            _advance_from_groups(tournament, state)
        return
    if rnd == "League Phase":
        if _round_complete(tournament.id, "League Phase"):
            _advance_from_league_phase(tournament, state)
        return
    if rnd == "Play-off":
        if _round_complete(tournament.id, "Play-off"):
            _advance_from_playoff(tournament, state)
        return
    # Knockout rounds (Preliminary / Round of N / Final, incl. two-legged).
    if _round_complete(tournament.id, rnd):
        winners = _round_winners(tournament.id, rnd)
        if rnd == "Final":
            _crown(tournament, winners[0] if winners else None, state)
        elif len(winners) <= 1:
            _crown(tournament, winners[0] if winners else None, state)
        else:
            nxt = round_name_for(len(winners))
            two_legged = tournament.scope == "continental" and len(winners) <= 4
            date = _next_date(state)
            if two_legged:
                _draw_two_legged(tournament, winners, nxt, date)
            else:
                generate_knockout_round(tournament.id, winners, nxt, date)
            tournament.current_round = nxt
            db.session.flush()


def _draw_two_legged(tournament: Tournament, team_ids: list[int],
                     round_name: str, first_leg_date: str) -> None:
    ids = list(team_ids)
    random.shuffle(ids)
    first_leg_date = _cup_fixture_date(first_leg_date)
    second_leg_date = _cup_fixture_date(_shift_date(first_leg_date, CUP_LEG_SPACING_DAYS))
    comp_type = _competition_type(tournament)
    for i in range(0, len(ids) - 1, 2):
        home_id, away_id = ids[i], ids[i + 1]
        tie = f"{round_name}-{i // 2}"
        m1 = Match(home_team_id=home_id, away_team_id=away_id, played=False,
                   date=first_leg_date, week=CUP_MATCH_WEEK, comp_type=comp_type)
        db.session.add(m1)
        db.session.flush()
        db.session.add(TournamentFixture(tournament_id=tournament.id, match_id=m1.id,
                                         round_name=round_name, leg=1,
                                         group_name=tie))
        m2 = Match(home_team_id=away_id, away_team_id=home_id, played=False,
                   date=second_leg_date, week=CUP_MATCH_WEEK, comp_type=comp_type)
        db.session.add(m2)
        db.session.flush()
        db.session.add(TournamentFixture(tournament_id=tournament.id, match_id=m2.id,
                                         round_name=round_name, leg=2,
                                         group_name=tie))
    db.session.flush()


def _advance_from_groups(tournament: Tournament, state: GameState | None) -> None:
    groups: dict[str, list[TournamentFixture]] = {}
    for fx in _round_fixtures(tournament.id, "Group Stage"):
        groups.setdefault(fx.group_name or "Group A", []).append(fx)
    advancers: list[int] = []
    for gname, fixtures in groups.items():
        pts: dict[int, dict] = {}
        for fx in fixtures:
            m = fx.match
            if m is None or not m.played:
                continue
            hs, aws = m.home_score or 0, m.away_score or 0
            for tid, gf, ga in ((m.home_team_id, hs, aws), (m.away_team_id, aws, hs)):
                row = pts.setdefault(tid, {"p": 0, "gf": 0, "ga": 0})
                row["gf"] += gf
                row["ga"] += ga
                row["p"] += 3 if gf > ga else (1 if gf == ga else 0)
        ordered = sorted(pts.items(),
                         key=lambda kv: (kv[1]["p"], kv[1]["gf"] - kv[1]["ga"], kv[1]["gf"]),
                         reverse=True)
        advancers.extend([tid for tid, _ in ordered[:2]])
    if len(advancers) < 2:
        _crown(tournament, advancers[0] if advancers else None, state)
        return
    nxt = round_name_for(len(advancers)) if len(advancers) in (2, 4, 8, 16, 32, 64) else "Semi-final"
    generate_knockout_round(tournament.id, advancers, nxt, _next_date(state))
    tournament.current_round = nxt
    db.session.flush()


def _advance_from_league_phase(tournament: Tournament, state: GameState | None) -> None:
    table = continental_table(tournament.id)
    if len(table) < 2:
        _crown(tournament, table[0].team_id if table else None, state)
        return
    direct = [r.team_id for r in table[:CONTINENTAL_DIRECT_SPOTS]]
    playoff = [r.team_id for r in table[CONTINENTAL_DIRECT_SPOTS:CONTINENTAL_PLAYOFF_SPOTS]]
    if not playoff:
        # Small field (e.g. test worlds): top teams go straight to knockout.
        need = next_power_of_two(len(direct))
        while len(direct) < need and len(direct) < len(table):
            direct.append(table[len(direct)].team_id)
        generate_knockout_round(tournament.id, direct[:need],
                                round_name_for(min(need, len(direct))),
                                _next_date(state))
        tournament.current_round = round_name_for(min(need, len(direct)))
        db.session.flush()
        return
    # Play-offs: 9v24, 10v23, ... two legs. Winners join the top 8 in R16.
    ordered = playoff
    pairs = [(ordered[i], ordered[len(ordered) - 1 - i])
             for i in range(len(ordered) // 2)]
    date = _cup_fixture_date(_next_date(state, CONTINENTAL_PHASE_SPACING_DAYS))
    second = _cup_fixture_date(_shift_date(date, CUP_LEG_SPACING_DAYS))
    for i, (a, b) in enumerate(pairs):
        tie = f"playoff-{i}"
        m1 = Match(home_team_id=a, away_team_id=b, played=False,
                   date=date, week=CUP_MATCH_WEEK, comp_type="continental")
        db.session.add(m1)
        db.session.flush()
        db.session.add(TournamentFixture(tournament_id=tournament.id, match_id=m1.id,
                                         round_name="Play-off", leg=1,
                                         group_name=tie))
        m2 = Match(home_team_id=b, away_team_id=a, played=False,
                   date=second, week=CUP_MATCH_WEEK, comp_type="continental")
        db.session.add(m2)
        db.session.flush()
        db.session.add(TournamentFixture(tournament_id=tournament.id, match_id=m2.id,
                                         round_name="Play-off", leg=2,
                                         group_name=tie))
    # Stash the direct qualifiers as R16 byes-in-waiting.
    for tid in direct:
        _record_bye(tournament.id, tid, "Play-off")
    tournament.current_round = "Play-off"
    db.session.flush()


def _advance_from_playoff(tournament: Tournament, state: GameState | None) -> None:
    winners = _round_winners(tournament.id, "Play-off")
    if len(winners) < 2:
        _crown(tournament, winners[0] if winners else None, state)
        return
    nxt = round_name_for(len(winners)) if len(winners) in (2, 4, 8, 16) else "Round of 16"
    if len(winners) > 4:
        generate_knockout_round(tournament.id, winners, nxt, _next_date(state))
    else:
        _draw_two_legged(tournament, winners, nxt, _next_date(state))
    tournament.current_round = nxt
    db.session.flush()


def _try_crown(tournament: Tournament, state: GameState | None) -> None:
    """Best-effort champion from whatever is decided (season rollover)."""
    if tournament.winner_team_id:
        tournament.is_active = False
        return
    for rnd in ("Final", "Semi-final", "Quarter-final", "Round of 16",
                "Round of 32", "Round of 64", "Play-off",
                "Preliminary Round", "Group Stage", "League Phase"):
        winners = _round_winners(tournament.id, rnd)
        if len(winners) == 1 and (rnd == "Final" or rnd == tournament.current_round):
            _crown(tournament, winners[0], state)
            return
    if tournament.scope == "continental":
        table = continental_table(tournament.id)
        if table:
            _crown(tournament, table[0].team_id, state)
            return


def _crown(tournament: Tournament, winner_id: int | None,
           state: GameState | None) -> None:
    tournament.winner_team_id = winner_id
    tournament.is_active = False
    tournament.current_round = "Complete"
    try:
        day = state.current_day if state else 0
        if winner_id:
            wteam = db.session.get(Team, winner_id)
            wname = wteam.name if wteam else "?"
            _cup_message(f"بطل {tournament.name}",
                         f"توج {wname} بلقب {tournament.name}!",
                         day, urgent=True)
            if wteam is not None and wteam.is_player_team and state is not None:
                state.trophies = (state.trophies or 0) + 1
    except Exception:
        pass
    db.session.flush()


# --------------------------------------------------------------------------
# UI helpers
# --------------------------------------------------------------------------
def get_user_cup_statuses() -> list[dict]:
    """Active competitions involving the player's club (dashboard strip)."""
    try:
        player = Team.query.filter_by(is_player_team=True).first()
        if player is None:
            return []
        out = []
        for t in Tournament.query.filter_by(is_active=True).order_by(Tournament.id).all():
            involved = (
                TournamentFixture.query.join(
                    Match, TournamentFixture.match_id == Match.id)
                .filter(TournamentFixture.tournament_id == t.id)
                .filter((Match.home_team_id == player.id)
                        | (Match.away_team_id == player.id))
                .first()
            )
            bye = TournamentFixture.query.filter_by(
                tournament_id=t.id, match_id=None,
                winner_team_id=player.id).first()
            if involved is None and bye is None:
                continue
            if t.winner_team_id == player.id:
                status = "البطل"
            elif involved is not None and involved.match and involved.match.played:
                if involved.winner_team_id == player.id:
                    status = f"تأهل — {round_label(t.current_round)}"
                else:
                    status = "خرج من البطولة"
            else:
                status = round_label(t.current_round)
            out.append({"tournament": t, "status": status})
        return out
    except Exception:
        return []


def get_tournament_bracket(tournament_id: int) -> dict:
    """Fixtures grouped by round (ordered) for the knockout-bracket view."""
    order = ["Preliminary Round", "League Phase", "Group Stage", "Play-off",
             "Round of 64", "Round of 32", "Round of 16",
             "Quarter-final", "Semi-final", "Final", "Complete"]
    try:
        fixtures = TournamentFixture.query.filter_by(
            tournament_id=tournament_id).all()
    except Exception:
        return {}
    rounds: dict[str, list] = {}
    for fx in fixtures:
        rounds.setdefault(fx.round_name or "Round 1", []).append(fx)
    for fx_list in rounds.values():
        fx_list.sort(key=lambda f: ((f.match.date or "") if f.match else "", f.id or 0))
    return {r: rounds[r] for r in order if r in rounds
            for r in [r]} | {k: v for k, v in rounds.items() if k not in order}


def active_tournaments(season: int | None = None) -> list[Tournament]:
    q = Tournament.query
    if season is not None:
        q = q.filter_by(season=season)
    try:
        return q.order_by(Tournament.scope, Tournament.id).all()
    except Exception:
        return []


def fixture_competition(match: Match) -> dict | None:
    """Badge info for the calendar: cup name + colour, else None (league)."""
    try:
        from config import CALENDAR_DOT_COLORS, CUP_BADGE_COLORS
        comp_type = (getattr(match, "comp_type", None) or "league").lower()
        if comp_type not in ("league", "domestic_cup", "continental"):
            comp_type = "league"
        dot_color = CALENDAR_DOT_COLORS.get(comp_type, CALENDAR_DOT_COLORS["league"])
        fx = TournamentFixture.query.filter_by(match_id=match.id).first()
        if fx is None or fx.tournament is None:
            return None
        if comp_type == "league":
            comp_type = _competition_type(fx.tournament)
            dot_color = CALENDAR_DOT_COLORS.get(comp_type, CALENDAR_DOT_COLORS["league"])
        round_name = round_label(fx.round_name)
        matchday = getattr(match, "matchday", None)
        if matchday and fx.round_name == "League Phase":
            round_name = f"{round_name} — الجولة {matchday}"
        return {
            "name": fx.tournament.name,
            "type": fx.tournament.type,
            "comp_type": comp_type,
            "round": round_name,
            "matchday": matchday,
            "color": CUP_BADGE_COLORS.get(fx.tournament.type, "#e74c3c"),
            "dot_color": dot_color,
        }
    except Exception:
        return None
