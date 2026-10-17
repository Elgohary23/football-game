"""Full transfer system tests: windows, contracts, clauses, loans, FFP (phases 1-5)."""
import random

from models import (
    BuyBackClause,
    FirstRefusalClause,
    InstallmentDebt,
    PerformanceBonus,
    SellOnClause,
    db,
    GameState,
    Message,
    Player,
    Team,
    TransferOffer,
)


def _player_team():
    return Team.query.filter_by(is_player_team=True).first()


def _opponent():
    return Team.query.filter_by(is_player_team=False).first()


def _state():
    return GameState.query.first()


def _target_from_seller(value=100000):
    seller = _opponent()
    target = next(p for p in seller.players)
    target.market_value = value
    target.contract_days = 700
    target.release_clause = None
    return seller, target


# ------------------------------------------------------- phase 1: structure
class TestTransferWindows:
    def test_window_open_in_summer(self, ctx):
        state = _state()
        state.current_date = "2026-07-15"
        state.transfer_window_override = None
        from services import transfer_window_state
        assert transfer_window_state(state) == "open"

    def test_window_closed_outside(self, ctx):
        state = _state()
        state.current_date = "2026-10-15"
        state.transfer_window_override = None
        from services import transfer_window_state
        assert transfer_window_state(state) == "closed"

    def test_window_override(self, ctx):
        from services import set_transfer_window, transfer_window_state
        state = _state()
        assert "مغلقة" in set_transfer_window(state, "closed")
        assert transfer_window_state(state) == "closed"
        set_transfer_window(state, "auto")
        state.current_date = "2026-07-15"
        assert transfer_window_state(state) == "open"

    def test_closed_window_blocks_purchase(self, ctx):
        from services import purchase_player
        state = _state()
        state.transfer_window_override = "closed"
        buyer = _player_team()
        buyer.budget = 10_000_000
        _, target = _target_from_seller()
        ok, msg = purchase_player(buyer, target, day=1)
        assert ok is False and "مغلقة" in msg
        state.transfer_window_override = None

    def test_deadline_day_flag(self, ctx):
        from services import is_deadline_day
        state = _state()
        state.transfer_window_override = None
        state.current_date = "2026-08-30"
        assert is_deadline_day(state) is True
        state.current_date = "2026-07-15"
        assert is_deadline_day(state) is False

    def test_window_route(self, client):
        r = client.post("/transfer/window/closed", follow_redirects=True)
        assert r.status_code == 200
        assert "مغلقة" in r.get_data(as_text=True) or "نافذة" in r.get_data(as_text=True)
        client.post("/transfer/window/auto", follow_redirects=True)


class TestTransferListAndRequests:
    def test_list_unlist(self, ctx):
        from services import list_transfer, unlist_transfer
        team = _player_team()
        player = team.players[0]
        ok, _ = list_transfer(team, player)
        assert ok is True and player.transfer_listed is True
        ok, _ = unlist_transfer(team, player)
        assert ok is True and player.transfer_listed is False

    def test_list_rejects_foreign_player(self, ctx):
        from services import list_transfer
        ok, _ = list_transfer(_player_team(), _opponent().players[0])
        assert ok is False

    def test_transfer_request(self, ctx):
        from services import request_transfer, withdraw_transfer_request
        team = _player_team()
        player = team.players[1]
        morale_before = player.morale or 50
        ok, _ = request_transfer(team, player, day=1)
        assert ok is True and player.transfer_requested is True
        assert (player.morale or 0) < morale_before
        assert Message.query.filter_by(type="transfer").count() >= 1
        ok, _ = withdraw_transfer_request(team, player)
        assert ok is True and player.transfer_requested is False

    def test_list_route(self, client):
        team = _player_team()
        pid = team.players[0].id
        r = client.post(f"/player/{pid}/list", follow_redirects=True)
        assert r.status_code == 200
        assert db.session.get(Player, pid).transfer_listed is True
        client.post(f"/player/{pid}/unlist", follow_redirects=True)
        assert db.session.get(Player, pid).transfer_listed is False

    def test_auto_list_offers(self, ctx, monkeypatch):
        import random as _r
        from services import auto_transfer_list_offers
        team = _player_team()
        player = team.players[2]
        player.transfer_listed = True
        db.session.flush()
        monkeypatch.setattr(_r, "random", lambda: 0.0)
        monkeypatch.setattr(_r, "choice", lambda seq: seq[0] if seq else None)
        monkeypatch.setattr(_r, "uniform", lambda a, b: 1.0)
        made = auto_transfer_list_offers(_state())
        assert made >= 1
        assert TransferOffer.query.filter_by(player_id=player.id).count() >= 1


# ------------------------------------------------------- phase 2: contracts
class TestContracts:
    def test_purchase_sets_wage_and_years(self, ctx):
        from services import purchase_player
        buyer = _player_team()
        buyer.budget = 10_000_000
        _, target = _target_from_seller()
        ok, _ = purchase_player(buyer, target, day=1, wage=50000, years=4,
                               signing_bonus=5000)
        assert ok is True
        assert target.wage == 50000
        assert target.contract_days == 4 * 365
        assert target.signing_bonus == 5000

    def test_signing_bonus_cap(self, ctx):
        from services import purchase_player
        buyer = _player_team()
        buyer.budget = 50_000_000
        _, target = _target_from_seller(value=100000)
        ok, _ = purchase_player(buyer, target, day=1, signing_bonus=10_000_000)
        assert ok is False

    def test_release_clause_forces_sale(self, ctx):
        from services import trigger_release_clause
        buyer = _player_team()
        buyer.budget = 10_000_000
        _, target = _target_from_seller(value=100000)
        target.release_clause = 250000
        db.session.flush()
        ok, msg = trigger_release_clause(buyer, target, day=1)
        assert ok is True, msg
        assert target.team_id == buyer.id

    def test_release_clause_missing(self, ctx):
        from services import trigger_release_clause
        _, target = _target_from_seller()
        target.release_clause = None
        ok, _ = trigger_release_clause(_player_team(), target, day=1)
        assert ok is False

    def test_expired_contract_frees_player(self, ctx):
        from services import expire_contracts
        team = _player_team()
        player = team.players[3]
        player.contract_days = 1
        db.session.flush()
        count = expire_contracts(_state())
        assert count >= 1
        assert db.session.get(Player, player.id).team_id is None

    def test_precontract_flow(self, ctx):
        from services import activate_precontracts, make_precontract
        buyer = _player_team()
        _, target = _target_from_seller()
        target.contract_days = 100
        db.session.flush()
        ok, _ = make_precontract(buyer, target, day=1, wage=30000, years=2)
        assert ok is True
        offer = TransferOffer.query.filter_by(
            player_id=target.id, is_precontract=True).first()
        assert offer is not None and offer.effective_day == 101
        # Too early: no move yet.
        state = _state()
        state.current_day = 50
        assert activate_precontracts(state) == 0
        assert db.session.get(Player, target.id).team_id != buyer.id
        # Matures: free Bosman move.
        state.current_day = 101
        assert activate_precontracts(state) == 1
        moved = db.session.get(Player, target.id)
        assert moved.team_id == buyer.id
        assert moved.contract_days == 2 * 365

    def test_precontract_rejected_when_long(self, ctx):
        from services import make_precontract
        _, target = _target_from_seller()
        target.contract_days = 700
        ok, _ = make_precontract(_player_team(), target, day=1)
        assert ok is False

    def test_precontract_route(self, client):
        opp = _opponent()
        target = next(p for p in opp.players)
        target.contract_days = 60
        db.session.commit()
        r = client.post(f"/precontract/{target.id}",
                        data={"wage": "10000", "years": "2"},
                        follow_redirects=True)
        assert r.status_code == 200

    def test_clause_and_precontract_render(self, client):
        opp = _opponent()
        target = next(p for p in opp.players)
        target.release_clause = 999999
        target.contract_days = 30
        db.session.commit()
        text = client.get("/scouting").get_data(as_text=True)
        assert "كسر العقد" in text and "عقد مسبق" in text


class TestFreeAgents:
    def test_sign_free_agent_anytime(self, ctx):
        from services import sign_free_agent
        state = _state()
        state.transfer_window_override = "closed"
        team = _player_team()
        team.budget = 10_000_000
        free = Player(name="Free Star", position="FWD", attack=80, defense=50,
                      potential=85, age=25, height=180, weight=75,
                      market_value=200000, team_id=None)
        db.session.add(free)
        db.session.flush()
        ok, msg = sign_free_agent(team, free, day=1, wage=40000, years=3)
        assert ok is True, msg
        assert free.team_id == team.id and free.wage == 40000
        state.transfer_window_override = None

    def test_sign_free_agent_rejects_owned(self, ctx):
        from services import sign_free_agent
        ok, _ = sign_free_agent(_player_team(), _opponent().players[0], day=1)
        assert ok is False

    def test_free_agent_route(self, client):
        free = Player(name="Route Free", position="MID", attack=70, defense=60,
                      potential=80, age=24, height=178, weight=72,
                      market_value=50000, team_id=None)
        db.session.add(free)
        db.session.commit()
        fid = free.id
        r = client.post(f"/free_agent/{fid}/sign",
                        data={"wage": "20000", "years": "2"},
                        follow_redirects=True)
        assert r.status_code == 200
        assert db.session.get(Player, fid).team_id is not None


# ------------------------------------------------------- phase 3: clauses
class TestDealClauses:
    def test_installments_create_debts(self, ctx):
        from services import pay_installments_due, purchase_player
        buyer = _player_team()
        buyer.budget = 10_000_000
        seller = _opponent()
        seller.budget = 1_000_000
        _, target = _target_from_seller(value=400000)
        ok, _ = purchase_player(buyer, target, day=1, installments=2)
        assert ok is True
        debts = InstallmentDebt.query.filter_by(player_id=target.id).all()
        assert len(debts) == 1 and debts[0].paid is False
        # Upfront only 200k + 5% agent fee moved immediately.
        assert buyer.budget == 10_000_000 - 200000 - 20000
        state = _state()
        state.current_day = debts[0].due_day
        assert pay_installments_due(state) >= 1
        assert db.session.get(InstallmentDebt, debts[0].id).paid is True

    def test_buy_back_clause(self, ctx):
        from services import exercise_buy_back, purchase_player
        buyer = _player_team()
        buyer.budget = 10_000_000
        seller = _opponent()
        _, target = _target_from_seller(value=100000)
        ok, _ = purchase_player(buyer, target, day=1, buy_back_price=150000)
        assert ok is True
        clause = BuyBackClause.query.filter_by(player_id=target.id).first()
        assert clause is not None and clause.price == 150000
        # Seller reclaims the player.
        seller.budget = 10_000_000
        ok, _ = exercise_buy_back(seller, target, day=2)
        assert ok is True
        assert db.session.get(Player, target.id).team_id == seller.id
        assert BuyBackClause.query.filter_by(player_id=target.id).count() == 0

    def test_first_refusal_recorded(self, ctx):
        from services import purchase_player
        buyer = _player_team()
        buyer.budget = 10_000_000
        _, target = _target_from_seller()
        ok, _ = purchase_player(buyer, target, day=1, first_refusal=True)
        assert ok is True
        assert FirstRefusalClause.query.filter_by(player_id=target.id).count() == 1

    def test_sell_on_via_purchase(self, ctx):
        from services import purchase_player
        buyer = _player_team()
        buyer.budget = 10_000_000
        _, target = _target_from_seller()
        ok, _ = purchase_player(buyer, target, day=1, sell_on_clause=0.2)
        assert ok is True
        clause = SellOnClause.query.filter_by(player_id=target.id).first()
        assert clause is not None and abs(clause.percentage - 0.2) < 1e-6

    def test_performance_bonus_paid(self, ctx):
        from services import check_performance_bonuses, purchase_player
        buyer = _player_team()
        buyer.budget = 10_000_000
        seller = _opponent()
        seller.budget = 1_000_000
        _, target = _target_from_seller(value=400000)
        ok, _ = purchase_player(buyer, target, day=1,
                               performance_bonus=50000, performance_target=5)
        assert ok is True
        target.goals = 7
        db.session.flush()
        state = _state()
        paid_before = seller.budget
        assert check_performance_bonuses(state) == 1
        assert seller.budget == paid_before + 50000
        bonus = PerformanceBonus.query.filter_by(player_id=target.id).first()
        assert bonus.paid is True

    def test_buyback_route(self, client):
        buyer = _player_team()
        buyer.budget = 10_000_000
        opp = _opponent()
        target = next(p for p in opp.players)
        target.market_value = 100000
        db.session.commit()
        client.post(f"/buy_player/{target.id}",
                    data={"buy_back": "120000"}, follow_redirects=True)
        assert BuyBackClause.query.filter_by(player_id=target.id).count() == 1


# ------------------------------------------------------- phase 4: negotiation
class TestNegotiation:
    def _bid(self, amount=200000):
        from services import create_bid
        buyer = _player_team()
        _, target = _target_from_seller()
        ok, _ = create_bid(buyer, target, amount, day=1, wage=30000, years=3)
        assert ok is True
        return TransferOffer.query.filter_by(
            player_id=target.id, to_team_id=buyer.id).first()

    def test_counter_rounds(self, ctx):
        from services import counter_offer
        offer = self._bid()
        ok, msg = counter_offer(offer.id, 250000, day=1)
        assert ok is True and offer.status == "counter"
        assert offer.counter_amount == 250000 and offer.offer_round == 2
        assert "جولة 2" in msg

    def test_counter_collapse_after_max_rounds(self, ctx):
        from services import counter_offer
        from config import OFFER_MAX_ROUNDS
        offer = self._bid()
        for i in range(OFFER_MAX_ROUNDS):
            counter_offer(offer.id, 200000 + i, day=1)
        db.session.refresh(offer)
        assert offer.status in ("counter", "rejected")
        ok, _ = counter_offer(offer.id, 999999, day=1)
        assert ok is False

    def test_accept_uses_counter_price(self, ctx):
        from services import counter_offer, process_offer_accept
        buyer = _player_team()
        buyer.budget = 10_000_000
        offer = self._bid(amount=200000)
        counter_offer(offer.id, 260000, day=1)
        db.session.flush()
        before = buyer.budget
        ok, _ = process_offer_accept(offer.id)
        assert ok is True
        # 260k + 5% agent fee moved.
        assert buyer.budget == before - 260000 - 13000

    def test_agent_fee_rates(self, ctx):
        from services import calc_agent_fee
        from config import AGENT_FEE_RATIO, AGENT_FEE_STAR_RATIO
        assert calc_agent_fee(100000, None) == int(100000 * AGENT_FEE_RATIO)
        star = Player(name="S", position="FWD", attack=90, defense=60,
                      potential=95, age=24, height=180, weight=75,
                      market_value=10_000_000, team_id=None)
        assert calc_agent_fee(100000, star) == int(100000 * AGENT_FEE_STAR_RATIO)

    def test_dynamic_valuation(self, ctx):
        from services import get_player_valuation
        _, target = _target_from_seller(value=100000)
        target.goals = 0
        target.age = 24
        plain = get_player_valuation(target)
        target.goals = 25
        target.age = 20
        hyped = get_player_valuation(target)
        assert hyped > plain
        target.transfer_listed = True
        assert get_player_valuation(target) < hyped

    def test_counter_route(self, client):
        buyer = _player_team()
        opp = _opponent()
        target = next(p for p in opp.players)
        offer = TransferOffer(player_id=target.id, from_team_id=opp.id,
                              to_team_id=buyer.id, amount=100000,
                              status="pending", created_day=1, deadline_day=50)
        db.session.add(offer)
        db.session.commit()
        oid = offer.id
        r = client.post(f"/transfer_offer/{oid}/counter",
                        data={"counter_amount": "150000"},
                        follow_redirects=True)
        assert r.status_code == 200
        updated = db.session.get(TransferOffer, oid)
        assert updated.status == "counter" and updated.counter_amount == 150000
        text = client.get("/messages").get_data(as_text=True)
        assert "تفاوض" in text

    def test_closed_window_blocks_bid(self, ctx):
        from services import create_bid
        _state().transfer_window_override = "closed"
        _, target = _target_from_seller()
        ok, _ = create_bid(_player_team(), target, 50000, day=1)
        assert ok is False
        _state().transfer_window_override = None


# ------------------------------------------------------- phase 5: loans, rivals, FFP
class TestLoans:
    def _loan_offer(self, fee=10000, days=180, buy_option=None):
        from services import create_bid
        buyer = _player_team()
        _, target = _target_from_seller()
        ok, _ = create_bid(buyer, target, 0, day=1, is_loan=True,
                           loan_days=days, loan_fee=fee, buy_option=buy_option)
        assert ok is True
        return TransferOffer.query.filter_by(
            player_id=target.id, to_team_id=buyer.id).first()

    def test_loan_accept_and_return(self, ctx):
        from services import process_offer_accept, return_expired_loans
        buyer = _player_team()
        buyer.budget = 10_000_000
        seller = _opponent()
        offer = self._loan_offer()
        target_id = offer.player_id
        ok, _ = process_offer_accept(offer.id)
        assert ok is True
        player = db.session.get(Player, target_id)
        assert player.team_id == buyer.id
        assert player.loan_from_team_id == seller.id
        # Expire the spell: player goes home.
        state = _state()
        state.current_day = (player.loan_until_day or 0) + 1
        assert return_expired_loans(state) >= 1
        assert db.session.get(Player, target_id).team_id == seller.id

    def test_loan_to_buy(self, ctx):
        from services import exercise_loan_buy, process_offer_accept
        buyer = _player_team()
        buyer.budget = 10_000_000
        offer = self._loan_offer(buy_option=300000)
        target_id = offer.player_id
        assert process_offer_accept(offer.id)[0] is True
        player = db.session.get(Player, target_id)
        before = buyer.budget
        ok, _ = exercise_loan_buy(buyer, player, day=2)
        assert ok is True
        assert buyer.budget == before - 300000
        assert db.session.get(Player, target_id).loan_buy_option is None

    def test_loan_without_option_rejected(self, ctx):
        from services import exercise_loan_buy, process_offer_accept
        buyer = _player_team()
        buyer.budget = 10_000_000
        offer = self._loan_offer()
        assert process_offer_accept(offer.id)[0] is True
        player = db.session.get(Player, offer.player_id)
        ok, _ = exercise_loan_buy(buyer, player, day=2)
        assert ok is False

    def test_loan_offer_route(self, client):
        opp = _opponent()
        target = next(p for p in opp.players)
        r = client.post(f"/loan_offer/{target.id}",
                        data={"loan_days": "180", "loan_fee": "5000",
                              "buy_option": "200000"},
                        follow_redirects=True)
        assert r.status_code == 200
        assert TransferOffer.query.filter_by(
            player_id=target.id, is_loan=True).count() >= 1


class TestRivalsAndFFP:
    def test_rival_hijack(self, ctx, monkeypatch):
        import random as _r
        from services import maybe_rival_bid
        buyer = _player_team()
        buyer.reputation = 50
        rich = Team(name="Rich Giants", morale=70, budget=50_000_000,
                    reputation=90, is_player_team=False,
                    league_id=buyer.league_id)
        db.session.add(rich)
        db.session.flush()
        _, target = _target_from_seller(value=100000)
        db.session.add(TransferOffer(
            player_id=target.id, from_team_id=target.team_id,
            to_team_id=buyer.id, amount=100000, status="pending",
            created_day=1, deadline_day=50))
        db.session.flush()
        monkeypatch.setattr(_r, "random", lambda: 0.0)
        monkeypatch.setattr(_r, "uniform", lambda a, b: 1.1)
        assert maybe_rival_bid(_state()) >= 1
        assert TransferOffer.query.filter_by(
            player_id=target.id, to_team_id=rich.id).count() >= 1

    def test_player_snubs_small_club(self, ctx):
        from services import purchase_player
        buyer = _player_team()
        buyer.reputation = 40
        buyer.budget = 50_000_000
        big = Team(name="Euro Giants", morale=70, budget=50_000_000,
                   reputation=95, is_player_team=False,
                   league_id=buyer.league_id)
        db.session.add(big)
        db.session.flush()
        _, target = _target_from_seller(value=100000)
        db.session.add(TransferOffer(
            player_id=target.id, from_team_id=target.team_id,
            to_team_id=big.id, amount=50000, status="pending",
            created_day=1, deadline_day=50))
        db.session.flush()
        ok, msg = purchase_player(buyer, target, day=1)
        assert ok is False and "يفضل" in msg

    def test_ffp_blocks_overspend(self, ctx):
        from services import ffp_check, purchase_player
        team = _player_team()
        team.season_spend = 4_900_000
        team.season_income = 0
        ok, _ = ffp_check(team, 500000)
        assert ok is False
        team.budget = 50_000_000
        _, target = _target_from_seller(value=100000)
        ok, msg = purchase_player(team, target, day=1)
        assert ok is False and "FFP" in msg

    def test_ffp_allows_normal_deal(self, ctx):
        from services import ffp_check
        ok, _ = ffp_check(_player_team(), 100000)
        assert ok is True

    def test_status_banner_renders(self, client):
        text = client.get("/scouting").get_data(as_text=True)
        assert "نافذة الانتقالات" in text

    def test_squad_transfer_card_renders(self, client):
        text = client.get("/squad").get_data(as_text=True)
        assert "قائمة اللاعبين" in text
