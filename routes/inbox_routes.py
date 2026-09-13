"""Inbox / messages blueprint (Tip 28 authorization in services)."""

from flask import Blueprint, flash, redirect, render_template

bp = Blueprint("inbox", __name__)

from models import db, GameState, Message, Team, TransferOffer
from services import process_offer_accept, process_offer_reject
from simulation import get_unread_count, mark_message_read


@bp.route("/messages")
def messages():
    state = None
    player_team = None
    try:
        state = GameState.query.first()
        player_team = Team.query.filter_by(is_player_team=True).first()
    except Exception:
        pass

    msgs = Message.query.order_by(Message.created_day.desc()).all()
    offers = (
        TransferOffer.query.filter(
            (TransferOffer.status == "pending")
            & (
                (TransferOffer.from_team_id == (player_team.id if player_team else 0))
                | (TransferOffer.to_team_id == (player_team.id if player_team else 0))
            )
        ).all()
    )

    first_msg = msgs[0] if msgs else None

    return render_template(
        "messages.html",
        state=state,
        player_team=player_team,
        messages=msgs,
        pending_offers=offers,
        first_msg=first_msg,
    )


@bp.route("/inbox_unread")
def inbox_unread():
    try:
        unread = get_unread_count()
    except Exception:
        unread = 0
    return {"unread": unread}


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
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("حدث خطأ في معالجة العرض!")
        return redirect("/messages")
    flash(message)
    return redirect("/messages")