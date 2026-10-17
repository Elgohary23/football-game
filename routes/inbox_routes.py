"""Inbox / messages blueprint (Tip 28 authorization in services)."""

from flask import Blueprint, flash, jsonify, redirect, render_template

bp = Blueprint("inbox", __name__)

from models import db, GameState, Message, Team, TransferOffer
from services import process_offer_accept, process_offer_reject
from simulation import get_unread_count, mark_message_read
from narrative import PRIORITY_META, notification_priority, priority_meta


@bp.route("/messages")
def messages():
    state = None
    player_team = None
    try:
        state = GameState.query.first()
        player_team = Team.query.filter_by(is_player_team=True).first()
    except Exception:
        pass

    msgs = Message.query.order_by(Message.created_day.desc()).limit(200).all()
    if player_team is None:
        offers = []
    else:
        offers = (
            TransferOffer.query.filter(
                (TransferOffer.status.in_(["pending", "counter"]))
                & (
                    (TransferOffer.from_team_id == player_team.id)
                    | (TransferOffer.to_team_id == player_team.id)
                )
            ).all()
        )

    first_msg = msgs[0] if msgs else None

    try:
        priorities = {m.id: notification_priority(m) for m in msgs}
    except Exception:
        priorities = {}

    return render_template(
        "messages.html",
        state=state,
        player_team=player_team,
        messages=msgs,
        pending_offers=offers,
        first_msg=first_msg,
        priorities=priorities,
        priority_meta=priority_meta,
        priority_levels=PRIORITY_META,
    )


@bp.route("/inbox_unread")
def inbox_unread():
    try:
        unread = get_unread_count()
    except Exception as exc:
        import logging

        logging.getLogger(__name__).exception("inbox_unread failed: %s", exc)
        unread = 0
    return jsonify({"unread": unread})


@bp.route("/inbox_api/<int:message_id>")
def inbox_api(message_id):
    msg = db.session.get(Message, message_id)
    if not msg:
        return {"error": "not found"}, 404
    mark_message_read(message_id)
    return {
        "id": msg.id,
        "title": msg.title,
        "content": msg.content,
        "type": msg.type,
        "read": True,
        "urgent": msg.urgent,
        "created_day": msg.created_day,
    }


@bp.route("/message_read/<int:message_id>")
def message_read(message_id):
    mark_message_read(message_id)
    return redirect("/messages")


@bp.route("/transfer_offer/<int:offer_id>/accept", methods=["POST"])
def accept_transfer(offer_id):
    ok, message = process_offer_accept(offer_id)
    if not ok:
        db.session.rollback()
        flash(message)
        return redirect("/messages")
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("حدث خطأ في معالجة العرض!")
        return redirect("/messages")
    flash(message)
    return redirect("/messages")


@bp.route("/transfer_offer/<int:offer_id>/reject", methods=["POST"])
def reject_transfer(offer_id):
    ok, message = process_offer_reject(offer_id)
    if not ok:
        db.session.rollback()
        flash(message)
        return redirect("/messages")
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("حدث خطأ في معالجة العرض!")
        return redirect("/messages")
    flash(message)
    return redirect("/messages")


@bp.route("/transfer_offer/<int:offer_id>/counter", methods=["POST"])
def counter_transfer(offer_id):
    from flask import request
    from models import GameState
    from services import counter_offer

    state = GameState.query.first()
    day = state.current_day if state else 0
    new_amount = request.form.get("counter_amount", type=int) or 0
    ok, message = counter_offer(offer_id, new_amount, day)
    if not ok:
        db.session.rollback()
        flash(message)
        return redirect("/messages")
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("حدث خطأ في التفاوض!")
        return redirect("/messages")
    flash(message)
    return redirect("/messages")