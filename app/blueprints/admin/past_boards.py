"""Admin → Past Boards: archive current committee, view/edit/delete past terms."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from flask import flash, redirect, render_template, request, url_for

from . import admin_bp
from ...extensions import db
from ...models import CommitteeMember, PastBoard, PastBoardMember
from ...models.content import get_site_settings
from ...security import requires_permission, audit
from ...services.uploads import UploadError, remove_upload, save_image


@admin_bp.route("/past-boards")
@requires_permission("committee.edit_any", "committee.edit_self")
def past_boards_index():
    boards = (
        PastBoard.query
        .order_by(PastBoard.display_order.desc())
        .all()
    )
    return render_template("admin/past_boards.html", boards=boards)


@admin_bp.route("/past-boards/archive", methods=["POST"])
@requires_permission("committee.edit_any")
def past_boards_archive():
    """Snapshot the current committee into a new past board."""
    current = CommitteeMember.visible_in_order()
    if not current:
        flash("No current committee members to archive.", "error")
        return redirect(url_for("admin.past_boards_index"))

    settings = get_site_settings()
    term_start = settings.board_term_start or date.today()
    interval = settings.board_term_interval_months or 12

    # Calculate term end: start + interval months - 1 day
    ey = term_start.year + (term_start.month + interval - 1) // 12
    em = (term_start.month + interval - 1) % 12 + 1
    ed = min(term_start.day, [31, 29 if (ey % 4 == 0 and ey % 100 != 0) or ey % 400 == 0 else 28,
                               31, 30, 31, 30, 31, 31, 30, 31, 30, 31][em - 1])
    term_end = date(ey, em, ed)

    board = PastBoard(
        label=f"{term_start.year}–{term_end.year} Board",
        term_start=term_start,
        term_end=term_end,
        display_order=len(PastBoard.query.all()) + 1,
    )
    db.session.add(board)
    db.session.flush()

    for i, m in enumerate(current):
        db.session.add(PastBoardMember(
            past_board_id=board.id,
            full_name=m.full_name,
            title=m.title,
            role=m.role,
            affiliation=m.affiliation,
            position=m.position,
            interests=m.interests,
            orcid=m.orcid,
            scholar_url=m.scholar_url,
            website_url=m.website_url,
            portrait_filename=m.portrait_filename,
            portrait_alt_text=m.portrait_alt_text,
            display_order=i * 10,
        ))

    # Advance the term start to the day after term_end
    nd = term_end + timedelta(days=1)
    settings.board_term_start = nd
    settings.board_last_archived_at = datetime.utcnow()

    db.session.commit()
    audit.record("past_board.archived",
                 target_kind="past_board", target_id=board.id,
                 summary=f"Archived {len(current)} members to '{board.label}'")
    flash(f"Archived {len(current)} members to '{board.label}'.", "success")
    return redirect(url_for("admin.past_boards_index"))


@admin_bp.route("/past-boards/<int:bid>/edit", methods=["GET", "POST"])
@requires_permission("committee.edit_any")
def past_board_edit(bid):
    board = PastBoard.query.get_or_404(bid)
    if request.method == "POST":
        board.label = (request.form.get("label") or board.label).strip()
        if request.form.get("term_start"):
            try:
                board.term_start = date.fromisoformat(request.form["term_start"])
            except ValueError:
                pass
        if request.form.get("term_end"):
            try:
                board.term_end = date.fromisoformat(request.form["term_end"])
            except ValueError:
                pass
        try:
            board.display_order = int(request.form.get("display_order") or 0)
        except ValueError:
            pass

        # Update existing members
        for m in list(board.members):
            if request.form.get(f"pm_delete_{m.id}"):
                if m.portrait_filename:
                    from flask import current_app
                    remove_upload(current_app.config["UPLOAD_FOLDER"],
                                  f"committee/{m.portrait_filename}")
                db.session.delete(m)
                continue
            del_name = request.form.get(f"pm_name_{m.id}")
            if del_name:
                m.full_name = del_name.strip()
                m.title = (request.form.get(f"pm_title_{m.id}") or "").strip()
                m.role = (request.form.get(f"pm_role_{m.id}") or "").strip()
                m.affiliation = (request.form.get(f"pm_affil_{m.id}") or "").strip()
                m.position = (request.form.get(f"pm_position_{m.id}") or "").strip()
                m.interests = (request.form.get(f"pm_interests_{m.id}") or "").strip()
                m.orcid = (request.form.get(f"pm_orcid_{m.id}") or "").strip()
                m.scholar_url = (request.form.get(f"pm_scholar_{m.id}") or "").strip()
                m.website_url = (request.form.get(f"pm_website_{m.id}") or "").strip()
                m.portrait_alt_text = (request.form.get(f"pm_alt_{m.id}") or "").strip()
                try:
                    m.display_order = int(request.form.get(f"pm_order_{m.id}") or 0)
                except ValueError:
                    pass

                # Portrait upload
                from flask import current_app
                f = request.files.get(f"pm_portrait_{m.id}")
                if f and f.filename:
                    try:
                        rel = save_image(
                            f, upload_folder=current_app.config["UPLOAD_FOLDER"],
                            subdir="committee", prefix=f"past-{m.id}",
                            max_bytes=current_app.config["MAX_HERO_BYTES"],
                            square_crop=True, target_size=600,
                        )
                        if m.portrait_filename:
                            remove_upload(current_app.config["UPLOAD_FOLDER"],
                                          f"committee/{m.portrait_filename}")
                        m.portrait_filename = rel.split("/", 1)[-1]
                    except UploadError as e:
                        flash(str(e), "error")

        # Add new members
        new_names = request.form.getlist("new_pm_name[]")
        new_titles = request.form.getlist("new_pm_title[]")
        new_roles = request.form.getlist("new_pm_role[]")
        new_affils = request.form.getlist("new_pm_affil[]")
        new_orders = request.form.getlist("new_pm_order[]")
        for i, name in enumerate(new_names):
            name = name.strip()
            if not name:
                continue
            m = PastBoardMember(
                past_board_id=board.id,
                full_name=name,
                title=(new_titles[i] if i < len(new_titles) else "").strip(),
                role=(new_roles[i] if i < len(new_roles) else "").strip(),
                affiliation=(new_affils[i] if i < len(new_affils) else "").strip(),
                display_order=int(new_orders[i] or 0) if i < len(new_orders) else 0,
            )
            db.session.add(m)

        db.session.commit()
        audit.record("past_board.updated",
                     target_kind="past_board", target_id=board.id,
                     summary=f"Updated past board '{board.label}'")
        flash("Past board updated.", "success")
        return redirect(url_for("admin.past_boards_index"))

    return render_template("admin/past_board_edit.html", board=board)


@admin_bp.route("/past-boards/<int:bid>/delete", methods=["POST"])
@requires_permission("committee.edit_any")
def past_board_delete(bid):
    board = PastBoard.query.get_or_404(bid)
    label = board.label
    for m in board.members:
        if m.portrait_filename:
            from flask import current_app
            remove_upload(current_app.config["UPLOAD_FOLDER"],
                          f"committee/{m.portrait_filename}")
    db.session.delete(board)
    db.session.commit()
    audit.record("past_board.deleted",
                 target_kind="past_board", target_id=bid,
                 summary=f"Deleted past board '{label}'")
    flash(f"Deleted past board '{label}'.", "success")
    return redirect(url_for("admin.past_boards_index"))
