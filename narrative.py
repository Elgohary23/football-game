"""Dynamic narrative engine (fix.txt).

Turns the existing simulation data into the stories a manager cares about:
season storylines, a season-overview snapshot, a full calendar event feed
(matches, transfers, injuries, contracts, board, training, milestones),
pre-match storylines and notification priorities.

Everything here is read-only data shaping on top of ``models`` /
``services`` / ``simulation``. Every public function is *best effort*: on
any failure it returns an empty/neutral value instead of raising, so the
dashboard / calendar / match / inbox hubs can never be broken by a
narrative bug. No gameplay system is changed or duplicated.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from models import GameState, Match, Message, Player, Team, TransferOffer

# ---------------------------------------------------------------------------
# Category metadata (fix.txt colour coding)
# League = Blue, Cup = Purple, Continental = Gold, Transfer = Orange,
# Board = Green, Medical = Red. Extra buckets keep training/milestones
# visually distinct.
# ---------------------------------------------------------------------------
EVENT_CATEGORY_META: dict[str, dict[str, str]] = {
    "match": {"label": "الدوري", "icon": "fa-futbol", "color": "#3498db"},
    "cup": {"label": "الكؤوس", "icon": "fa-trophy", "color": "#9b59b6"},
    "continental": {"label": "قارية", "icon": "fa-globe-europe", "color": "#f1c40f"},
    "transfer": {"label": "الانتقالات", "icon": "fa-exchange-alt", "color": "#e67e22"},
    "injury": {"label": "الطبية", "icon": "fa-briefcase-medical", "color": "#e74c3c"},
    "contract": {"label": "العقود", "icon": "fa-file-signature", "color": "#2ecc71"},
    "board": {"label": "مجلس الإدارة", "icon": "fa-clipboard-check", "color": "#27ae60"},
    "training": {"label": "التدريب", "icon": "fa-dumbbell", "color": "#95a5a6"},
    "milestone": {"label": "محطات", "icon": "fa-star", "color": "#f39c12"},
}

# Live notification priorities (fix.txt INBOX / NOTIFICATION SYSTEM).
PRIORITY_CRITICAL = "critical"
PRIORITY_IMPORTANT = "important"
PRIORITY_OPTIONAL = "optional"

PRIORITY_META: dict[str, dict[str, str]] = {
    PRIORITY_CRITICAL: {"label": "حرِج", "color": "#e74c3c", "icon": "fa-exclamation-triangle"},
    PRIORITY_IMPORTANT: {"label": "مهم", "color": "#f39c12", "icon": "fa-bell"},
    PRIORITY_OPTIONAL: {"label": "اختياري", "color": "#7f8c8d", "icon": "fa-info-circle"},
}

_KEYWORDS_INJURY = ("إصابة", "مصاب", "إيقاف", "بطاقة حمراء")
_KEYWORDS_BOARD_WARN = ("تحذير", "غاضب", "إنذار", "غضب", "استياء", "تهديد")
_KEYWORDS_CONTRACT = ("عقد", "تجديد", "انتقال حر", "عقد مسبق")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


def _parse_date(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except Exception:
        return None


def _iso(value: Any) -> Optional[str]:
    d = _parse_date(value)
    return d.strftime("%Y-%m-%d") if d else None


def _event(date: Any, category: str, title: str, detail: str = "", *,
           link: str = "", extra: Optional[dict] = None) -> dict:
    meta = EVENT_CATEGORY_META.get(category, EVENT_CATEGORY_META["match"])
    item = {
        "date": _iso(date),
        "category": category,
        "title": title,
        "detail": detail,
        "icon": meta["icon"],
        "color": meta["color"],
        "link": link,
    }
    if extra:
        item.update(extra)
    return item


def _event_sort_key(ev: dict) -> tuple:
    return (ev.get("date") or "9999-99-99", ev.get("category") or "")


# ---------------------------------------------------------------------------
# season overview panel (fix.txt SEASON OVERVIEW PANEL)
# ---------------------------------------------------------------------------
def compute_form(matches: Iterable[Match], team_id: int, limit: int = 5) -> list[str]:
    """Most-recent-first W/D/L form guide for ``team_id`` (already ordered)."""
    form: list[str] = []
    for m in matches:
        if len(form) >= limit:
            break
        if not m.played:
            continue
        if m.home_team_id == team_id:
            gf, ga = (m.home_score or 0), (m.away_score or 0)
        elif m.away_team_id == team_id:
            gf, ga = (m.away_score or 0), (m.home_score or 0)
        else:
            continue
        form.append("W" if gf > ga else ("D" if gf == ga else "L"))
    return form


def _form_streak(form: list[str]) -> tuple[str, int]:
    """(streak_char, length) for the most recent run in a most-recent-first list."""
    if not form:
        return ("", 0)
    last = form[0]
    n = 0
    for r in form:
        if r == last:
            n += 1
        else:
            break
    return (last, n)


def season_overview(state: GameState, player_team: Team, team_rank: Optional[int],
                    form: Optional[list[str]] = None,
                    events: Optional[list[dict]] = None) -> dict:
    """Everything the calendar's season-overview panel renders."""
    overview: dict[str, Any] = {
        "progress": 0,
        "position": team_rank,
        "position_display": "—",
        "matches_remaining": 0,
        "form": list(form or []),
        "form_streak": ("", 0),
        "window": "closed",
        "window_label": "مغلقة",
        "deadline": False,
        "board_value": 60,
        "board_label": "متوسطة",
        "board_tone": "mid",
        "next_event": None,
    }
    if not state or not player_team:
        return overview

    form = list(form or [])
    overview["form"] = form
    overview["form_streak"] = _form_streak(form)

    # --- league position ---
    if team_rank:
        overview["position_display"] = _ordinal_ar(team_rank)

    # --- season progress % ---
    def _rounds():
        from services import get_season_rounds
        return get_season_rounds(player_team.league_id)

    rounds = _safe(_rounds, 0) or 0
    current_week = int(state.current_week or 1)
    if rounds:
        overview["progress"] = max(0, min(100, round(current_week / rounds * 100)))

    # --- matches remaining for the manager's club ---
    overview["matches_remaining"] = _safe(
        lambda: Match.query.filter(
            ((Match.home_team_id == player_team.id) | (Match.away_team_id == player_team.id)),
            Match.played.is_(False),
        ).count(),
        0,
    )

    # --- transfer window status ---
    def _window_state():
        from services import transfer_window_state
        return transfer_window_state(state)

    def _deadline_day():
        from services import is_deadline_day
        return is_deadline_day(state)

    window = _safe(_window_state, "closed")
    deadline = _safe(_deadline_day, False)
    overview["window"] = window
    overview["deadline"] = bool(deadline)
    if deadline:
        overview["window_label"] = "يوم الإغلاق"
    elif window == "open":
        overview["window_label"] = "مفتوحة"
    else:
        overview["window_label"] = "مغلقة"

    # --- board confidence ---
    conf = state.board_confidence if state.board_confidence is not None else 60
    overview["board_value"] = conf
    if conf >= 70:
        overview["board_label"], overview["board_tone"] = "مرتفعة", "good"
    elif conf >= 40:
        overview["board_label"], overview["board_tone"] = "متوسطة", "mid"
    else:
        overview["board_label"], overview["board_tone"] = "منخفضة", "bad"

    # --- next critical event ---
    today = _parse_date(state.current_date)
    if today and events:
        upcoming = [e for e in events if (e.get("date") or "") > _iso(today)]
        upcoming.sort(key=_event_sort_key)
        if upcoming:
            nxt = dict(upcoming[0])
            nd = _parse_date(nxt.get("date"))
            nxt["days_away"] = (nd - today).days if nd else None
            overview["next_event"] = nxt

    return overview


def _ordinal_ar(n: int) -> str:
    return f"{n}"


# ---------------------------------------------------------------------------
# calendar event feed (fix.txt CALENDAR REDESIGN)
# ---------------------------------------------------------------------------
def _competition_category(comp_type: str) -> str:
    ct = (comp_type or "").lower()
    if ct in ("continental", "champions_league", "europa_league", "conference_league"):
        return "continental"
    if ct and ct != "league":
        return "cup"
    return "match"


def build_calendar_events(state: GameState, player_team: Team,
                          matches: Iterable[Match],
                          competition_for=None) -> list[dict]:
    """The full season-timeline feed (matches + every narrative event)."""
    events: list[dict] = []
    if not state or not player_team:
        return events

    today = _parse_date(state.current_date)
    horizon_end = (today + timedelta(days=180)) if today else None

    def within(date_value: Any) -> bool:
        d = _parse_date(date_value)
        if not d:
            return False
        if not horizon_end:
            return True
        return today is None or d <= horizon_end

    # --- fixtures (league / cup / continental) ---
    for m in matches or []:
        if not m or not m.date:
            continue
        comp = _safe(lambda m=m: competition_for(m), None) if competition_for else None
        comp_type = (comp or {}).get("type") or "league"
        name = (comp or {}).get("name") or "الدوري"
        rnd = (comp or {}).get("round") or ""
        opponent = m.away_team if (m.home_team and m.home_team.is_player_team) else m.home_team
        home = bool(m.home_team and m.home_team.is_player_team)
        title = f"{'ضد' if home else 'خارج أرضك'} {opponent.name if opponent else '؟'}"
        detail = f"{name}{(' — ' + rnd) if rnd else ''}"
        events.append(_event(
            m.date,
            _competition_category(comp_type),
            title,
            detail,
            link=(f"/match_report/{m.id}" if m.played else "/match"),
            extra={"played": bool(m.played), "home": home, "match_id": m.id},
        ))

    # --- transfer window markers ---
    try:
        from config import TRANSFER_DEADLINE_DAYS
    except Exception:
        TRANSFER_DEADLINE_DAYS = 3
    for label, date_value in _window_markers(state, TRANSFER_DEADLINE_DAYS):
        if not within(date_value):
            continue
        events.append(_event(date_value, "transfer", label,
                             "نافذة الانتقالات", extra={"deadline": "إغلاق" in label}))

    # --- injury recovery dates ---
    players = _safe(lambda: Player.query.filter_by(team_id=player_team.id).all(), [])
    for p in players or []:
        days = getattr(p, "injury_days", 0) or 0
        if days <= 0 or not today:
            continue
        events.append(_event(
            today + timedelta(days=int(days)), "injury",
            f"عودة {p.name} من الإصابة",
            f"غياب {int(days)} يوم",
            link="/medical",
        ))

    # --- expiring contracts ---
    for p in players or []:
        contract_days = getattr(p, "contract_days", None)
        if not contract_days or contract_days > 180 or not today:
            continue
        events.append(_event(
            today + timedelta(days=int(contract_days)), "contract",
            f"عقد {p.name} ينتهي",
            f"متبقٍ {int(contract_days)} يوم",
            link="/squad",
        ))

    # --- board review + milestone markers ---
    for ev in _board_and_milestone_events(state, player_team, players or []):
        if within(ev.get("date")):
            events.append(ev)

    events.sort(key=_event_sort_key)
    return events


def _window_markers(state: GameState, deadline_days: int) -> list[tuple[str, str]]:
    """(label, iso_date) for the summer/winter window boundaries."""
    markers: list[tuple[str, str]] = []
    try:
        from config import TRANSFER_WINDOW_SUMMER_MONTHS, TRANSFER_WINDOW_WINTER_MONTHS
    except Exception:
        TRANSFER_WINDOW_SUMMER_MONTHS = (6, 7, 8)
        TRANSFER_WINDOW_WINTER_MONTHS = (1,)
    today = _parse_date(state.current_date)
    if not today:
        return markers
    year = today.year
    windows = []
    for months in (TRANSFER_WINDOW_SUMMER_MONTHS, TRANSFER_WINDOW_WINTER_MONTHS):
        if not months:
            continue
        start_month = min(months)
        end_month = max(months)
        for y in (year - 1, year, year + 1):
            start = datetime(y, start_month, 1)
            end = datetime(y, end_month, 28) + timedelta(days=4)
            end = end.replace(day=1) - timedelta(days=1)
            windows.append((start, end))
    for start, end in windows:
        if start > today:
            markers.append(("فتح نافذة الانتقالات", start.strftime("%Y-%m-%d")))
        if end >= today:
            markers.append(("إغلاق نافذة الانتقالات", end.strftime("%Y-%m-%d")))
            deadline_day = end - timedelta(days=max(0, int(deadline_days) - 1))
            if deadline_day >= today:
                markers.append(("اقتراب إغلاق نافذة الانتقالات", deadline_day.strftime("%Y-%m-%d")))
    return markers


def _board_and_milestone_events(state: GameState, player_team: Team,
                                players: list[Player]) -> list[dict]:
    events: list[dict] = []
    today = _parse_date(state.current_date)
    if not today:
        return events

    # Monthly board review (narrative device tied to the existing board system).
    events.append(_event(today + timedelta(days=28), "board",
                         "مراجعة مجلس الإدارة", "تقييم الأداء والهدف الموسمي",
                         link="/board"))

    # Season halfway milestone.
    rounds = 0
    try:
        from services import get_season_rounds
        rounds = get_season_rounds(player_team.league_id) or 0
    except Exception:
        rounds = 0
    current_week = int(state.current_week or 1)
    if rounds:
        half = max(1, rounds // 2)
        if current_week < half:
            events.append(_event(today + timedelta(days=(half - current_week) * 7),
                                 "milestone", "منتصف الموسم", "تقييم منتصف الموسم"))
        remaining = max(0, rounds - current_week) * 7
        if remaining:
            events.append(_event(today + timedelta(days=remaining),
                                 "milestone", "نهاية الموسم", "آخر جولة في الدوري"))

    # Golden-boot milestone for a hot striker.
    hot = [p for p in players if (getattr(p, "goals", 0) or 0) >= 10]
    if hot:
        best = max(hot, key=lambda p: p.goals or 0)
        events.append(_event(today + timedelta(days=7), "milestone",
                             f"{best.name} يقترب من الحذاء الذهبي",
                             f"{best.goals} هدف حتى الآن", link="/stats/top-scorers"))

    # Next training week marker (narrative, not a new system).
    events.append(_event(today + timedelta(days=1), "training",
                         "أسبوع تدريبي جديد", "تحسين اللياقة والتكتيك", link="/training"))
    return events


# ---------------------------------------------------------------------------
# season storytelling system (fix.txt SEASON STORYTELLING SYSTEM)
# ---------------------------------------------------------------------------
def season_storylines(state: GameState, player_team: Team, *,
                      team_rank: Optional[int] = None,
                      league_size: int = 0,
                      league_table: Optional[list] = None,
                      squad: Optional[list[Player]] = None,
                      form: Optional[list[str]] = None,
                      top_scorers: Optional[list] = None) -> list[dict]:
    """Generate the dynamic football stories that appear across every hub."""
    stories: list[dict] = []
    if not state or not player_team:
        return stories
    form = list(form or [])
    squad = list(squad or [])
    streak_char, streak_len = _form_streak(form)

    if streak_len >= 3 and streak_char == "W":
        stories.append({"icon": "fa-fire", "tone": "positive",
                        "title": f"سلسلة انتصارات ({streak_len})",
                        "body": f"{player_team.name} لم يُهزم في آخر {streak_len} مباريات. الزخم في صالحك."})
    if streak_len >= 3 and streak_char == "L":
        stories.append({"icon": "fa-arrow-down", "tone": "negative",
                        "title": f"دوامة الخسائر ({streak_len})",
                        "body": "النتائج الأخيرة تضع المدرب تحت ضغط. الفريق يحتاج نقطة تحول."})
    if streak_len >= 4 and streak_char == "W":
        stories.append({"icon": "fa-bolt", "tone": "positive",
                        "title": "لا يُوقف",
                        "body": f"سلسلة {streak_len} انتصارات متتالية جعلت الفريق حديث الجميع."})

    if team_rank and team_rank <= 3 and form.count("W") >= 2:
        stories.append({"icon": "fa-trophy", "tone": "positive",
                        "title": "سباق اللقب",
                        "body": f"{player_team.name} في المركز {team_rank} — أنت في قلب المنافسة على اللقب."})
    if team_rank and league_size and team_rank >= max(1, league_size - 3):
        stories.append({"icon": "fa-shield-alt", "tone": "negative",
                        "title": "معركة البقاء",
                        "body": "الفارق ضيّق في قاع الجدول. كل نقطة قد تُحدد المصير."})

    if squad:
        hot = [p for p in squad if (getattr(p, "goals", 0) or 0) >= 5]
        if hot:
            best = max(hot, key=lambda p: p.goals or 0)
            stories.append({"icon": "fa-shoe-prints", "tone": "positive",
                            "title": "سباق الحذاء الذهبي",
                            "body": f"{best.name} سجل {best.goals} أهداف هذا الموسم ويقود هجوم الفريق."})

        young = [p for p in squad if (p.age or 99) <= 21 and (getattr(p, "goals", 0) or 0) >= 3]
        if young:
            star = max(young, key=lambda p: (p.goals or 0))
            stories.append({"icon": "fa-seedling", "tone": "positive",
                            "title": "نجم شاب صاعد",
                            "body": f"{star.name} ({star.age}) يفرض نفسه في التشكيلة الأساسية."})

        legends = [p for p in squad if (p.age or 0) >= 34]
        if legends:
            old = max(legends, key=lambda p: p.age or 0)
            stories.append({"icon": "fa-crown", "tone": "neutral",
                            "title": "أسطورة تقترب من الاعتزال",
                            "body": f"{old.name} ({old.age} سنة) يعيش فصوله الأخيرة في الملاعب."})

        expiring = [p for p in squad if (getattr(p, "contract_days", None) or 9999) <= 90]
        if expiring:
            stories.append({"icon": "fa-file-signature", "tone": "negative",
                            "title": "دراما العقود",
                            "body": f"{len(expiring)} لاعب يقترب عقده من الانتهاء — تفاوض أو اتركه يرحل."})

    conf = state.board_confidence if state.board_confidence is not None else 60
    if conf < 40:
        stories.append({"icon": "fa-newspaper", "tone": "negative",
                        "title": "ضغط إعلامي",
                        "body": "الصحافة تشكك في مستقبل المدرب بعد تراجع ثقة مجلس الإدارة."})
    elif conf >= 80 and form.count("W") >= 2:
        stories.append({"icon": "fa-thumbs-up", "tone": "positive",
                        "title": "دعم كامل",
                        "body": "مجلس الإدارة يثق في المشروع — الوقت مناسب للبناء طويل الأمد."})

    # Giant killing: a recent win over a much stronger club.
    try:
        recent = (Match.query.filter(
            ((Match.home_team_id == player_team.id) | (Match.away_team_id == player_team.id)),
            Match.played.is_(True)).order_by(Match.id.desc()).limit(5).all())
        for m in recent:
            if m.home_team_id == player_team.id:
                won = (m.home_score or 0) > (m.away_score or 0)
                rival = m.away_team
            else:
                won = (m.away_score or 0) > (m.home_score or 0)
                rival = m.home_team
            if won and rival and (rival.reputation or 0) >= (player_team.reputation or 0) + 10:
                stories.append({"icon": "fa-bomb", "tone": "positive",
                                "title": "إسقاط عملاق",
                                "body": f"فوز مثير على {rival.name} الأقوى تصنيفاً يرفع أسهم الفريق."})
                break
    except Exception:
        pass

    return stories


def top_storyline(*args, **kwargs) -> Optional[dict]:
    stories = season_storylines(*args, **kwargs)
    return stories[0] if stories else None


# ---------------------------------------------------------------------------
# focus hub (fix.txt DASHBOARD REDESIGN — "what needs my attention")
# ---------------------------------------------------------------------------
def critical_notifications(state: GameState, player_team: Team) -> list[dict]:
    """Short, prioritised list of things the manager must act on now."""
    items: list[dict] = []
    if not state or not player_team:
        return items

    # Injured players.
    injured = _safe(
        lambda: Player.query.filter_by(team_id=player_team.id).filter(
            Player.injury_days > 0
        ).all(),
        [],
    )
    if injured:
        items.append({
            "icon": "fa-briefcase-medical", "tone": "bad",
            "priority": PRIORITY_CRITICAL,
            "title": f"{len(injured)} لاعب مصاب",
            "body": "، ".join(p.name for p in injured[:3]),
            "link": "/medical",
        })

    # Unread urgent messages.
    urgent = _safe(
        lambda: Message.query.filter_by(read=False, urgent=True).count(), 0
    )
    if urgent:
        items.append({
            "icon": "fa-envelope-open-text", "tone": "bad",
            "priority": PRIORITY_CRITICAL,
            "title": f"{urgent} رسالة عاجلة غير مقروءة",
            "body": "راجع صندوق الوارد فوراً",
            "link": "/messages",
        })

    # Pending transfer offers involving the club.
    offers = _safe(
        lambda: TransferOffer.query.filter(
            (TransferOffer.status.in_(["pending", "counter"]))
            & (
                (TransferOffer.from_team_id == player_team.id)
                | (TransferOffer.to_team_id == player_team.id)
            )
        ).count(),
        0,
    )
    if offers:
        items.append({
            "icon": "fa-exchange-alt", "tone": "warn",
            "priority": PRIORITY_IMPORTANT,
            "title": f"{offers} عرض انتقال معلّق",
            "body": "قرار مطلوب قبل انتهاء المهلة",
            "link": "/messages",
        })

    # Low board confidence.
    conf = state.board_confidence if state.board_confidence is not None else 60
    if conf < 40:
        items.append({
            "icon": "fa-clipboard-check", "tone": "bad",
            "priority": PRIORITY_CRITICAL,
            "title": f"ثقة مجلس الإدارة منخفضة ({conf}%)",
            "body": "النتائج القادمة قد تحدّد مستقبلك",
            "link": "/board",
        })

    # Transfer deadline day.
    if _safe(lambda: _deadline_day(state), False):
        items.append({
            "icon": "fa-hourglass-end", "tone": "warn",
            "priority": PRIORITY_CRITICAL,
            "title": "يوم إغلاق نافذة الانتقالات",
            "body": "أكمل صفقاتك قبل نهاية اليوم",
            "link": "/transfers_center",
        })

    # Contracts expiring soon.
    expiring = _safe(
        lambda: Player.query.filter_by(team_id=player_team.id).filter(
            Player.contract_days > 0, Player.contract_days <= 30
        ).count(),
        0,
    )
    if expiring:
        items.append({
            "icon": "fa-file-signature", "tone": "warn",
            "priority": PRIORITY_IMPORTANT,
            "title": f"{expiring} عقد ينتهي قريباً",
            "body": "جدّد أو استعد للرحيل",
            "link": "/squad",
        })

    order = {PRIORITY_CRITICAL: 0, PRIORITY_IMPORTANT: 1, PRIORITY_OPTIONAL: 2}
    items.sort(key=lambda i: order.get(i.get("priority"), 3))
    return items


def _deadline_day(state: GameState) -> bool:
    from services import is_deadline_day
    return bool(is_deadline_day(state))


# ---------------------------------------------------------------------------
# pre-match storylines (fix.txt MATCH DAY EXPERIENCE)
# ---------------------------------------------------------------------------
def pre_match_storylines(match: Match, state: GameState,
                         player_team: Team) -> list[str]:
    lines: list[str] = []
    if not match or not player_team:
        return lines
    try:
        opponent = (match.away_team if match.home_team and match.home_team.is_player_team
                    else match.home_team)
    except Exception:
        opponent = None

    # Opponent recent form.
    if opponent:
        try:
            opp_matches = (Match.query.filter(
                ((Match.home_team_id == opponent.id) | (Match.away_team_id == opponent.id)),
                Match.played.is_(True)).order_by(Match.week.desc()).limit(5).all())
            opp_form = compute_form(opp_matches, opponent.id, 5)
            unbeaten = 0
            for r in opp_form:
                if r == "L":
                    break
                unbeaten += 1
            if unbeaten >= 3:
                lines.append(f"{opponent.name} لم يخسر في آخر {unbeaten} مباريات.")
            elif opp_form and opp_form[0] == "L":
                lines.append(f"{opponent.name} خسر مباراته الأخيرة — فرصة لاستغلال الارتباك.")
        except Exception:
            pass

    # Your striker hot streak.
    try:
        squad = Player.query.filter_by(team_id=player_team.id).all()
        hot = [p for p in squad if (getattr(p, "goals", 0) or 0) >= 5]
        if hot:
            best = max(hot, key=lambda p: p.goals or 0)
            lines.append(f"مهاجمك {best.name} سجل {best.goals} أهداف هذا الموسم.")
    except Exception:
        pass

    # Board expectation.
    conf = state.board_confidence if state and state.board_confidence is not None else 60
    if conf < 40:
        lines.append("مجلس الإدارة يتوقع نتيجة إيجابية اليوم.")
    elif conf >= 75:
        lines.append("مجلس الإدارة واثق — الجماهير تنتظر عرضاً هجومياً.")

    # Derby hint.
    if opponent and (opponent.league_id == player_team.league_id):
        lines.append("مباراة قمة محلية — التنافس متجدد على النقاط.")

    return lines


# ---------------------------------------------------------------------------
# notification priority (fix.txt INBOX / NOTIFICATION SYSTEM)
# ---------------------------------------------------------------------------
def notification_priority(msg: Message) -> str:
    if not msg:
        return PRIORITY_OPTIONAL
    if getattr(msg, "urgent", False):
        return PRIORITY_CRITICAL
    text = f"{getattr(msg, 'title', '') or ''} {getattr(msg, 'content', '') or ''}"
    mtype = (getattr(msg, "type", "") or "").lower()
    if mtype in ("board",) and any(k in text for k in _KEYWORDS_BOARD_WARN):
        return PRIORITY_CRITICAL
    if any(k in text for k in _KEYWORDS_INJURY):
        return PRIORITY_CRITICAL
    if mtype in ("transfer", "cup", "fa_cup", "super_cup"):
        return PRIORITY_IMPORTANT
    if mtype == "board" or any(k in text for k in _KEYWORDS_CONTRACT):
        return PRIORITY_IMPORTANT
    if mtype in ("info", "media", "world", "table", "dynamics", "season", "youth"):
        return PRIORITY_OPTIONAL
    return PRIORITY_OPTIONAL


def priority_meta(priority: str) -> dict[str, str]:
    return PRIORITY_META.get(priority, PRIORITY_META[PRIORITY_OPTIONAL])


__all__ = [
    "EVENT_CATEGORY_META",
    "PRIORITY_META",
    "PRIORITY_CRITICAL",
    "PRIORITY_IMPORTANT",
    "PRIORITY_OPTIONAL",
    "compute_form",
    "season_overview",
    "build_calendar_events",
    "season_storylines",
    "top_storyline",
    "critical_notifications",
    "pre_match_storylines",
    "notification_priority",
    "priority_meta",
]
