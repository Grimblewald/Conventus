"""Member-area routes: dashboard, profile, conference registration, abstract
submission. All require login.
"""
from __future__ import annotations

import re
import secrets
from io import BytesIO
from pathlib import Path
from datetime import datetime, timedelta

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template,
    request, send_file, send_from_directory, url_for,
)
from flask_login import current_user, login_required

from ...extensions import db
from ...models import (
    Abstract, Conference, OTPCode, Registration, ReviewAssignment,
    record_payment_event,
)
from ...models.content import get_site_settings
from ...security import audit
from ...services.invoice import _reg_merchant_reference
from ...services.mail import send_mail
from ...services.payments import (
    initiate_payment, payments_open_to_members, send_payment_email,
    send_registration_confirmation,
)
from ...services.uploads import UploadError, remove_upload, save_figure, save_image
from ...services.form_renderer import validate_form
from ...services.citations import fetch_metadata, format_reference, normalize_doi


member_bp = Blueprint("member", __name__)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@member_bp.route("/dashboard")
@login_required
def dashboard():
    regs = (
        Registration.query
        .filter_by(user_id=current_user.id)
        .filter(Registration.deleted_at.is_(None))
        .order_by(Registration.created_at.desc())
        .all()
    )
    abs_ = (
        Abstract.query
        .filter_by(user_id=current_user.id)
        .filter(Abstract.deleted_at.is_(None))
        .options(db.joinedload(Abstract.registration))
        .order_by(Abstract.created_at.desc())
        .all()
    )
    my_reviews = (
        ReviewAssignment.query
        .filter_by(reviewer_id=current_user.id)
        .filter(ReviewAssignment.status != "declined")
        .options(db.joinedload(ReviewAssignment.abstract))
        .order_by(ReviewAssignment.created_at.desc())
        .all()
    )
    return render_template("member/dashboard.html",
                           regs=regs, abstracts=abs_, my_reviews=my_reviews)


@member_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        current_user.full_name = (request.form.get("full_name") or "").strip()
        current_user.affiliation = (request.form.get("affiliation") or "").strip()

        # First-time profile completion graduates `unregistered` → `member`.
        if current_user.role_name == "unregistered" and current_user.full_name:
            current_user.role_name = "member"
            audit.record("user.role_changed",
                         target_kind="user", target_id=current_user.id,
                         summary=f"{current_user.email}: unregistered → member")

        db.session.commit()
        flash("Profile saved.", "success")
        return redirect(url_for("member.dashboard"))

    return render_template("member/profile.html")


# ---------------------------------------------------------------------------
# Conference registration
# ---------------------------------------------------------------------------

def _settled_by_payment(reg) -> bool:
    """True when money actually arrived for this registration.

    Answered from the ledger, because the two things that look like evidence
    of payment are not. `transaction_id` is stamped on the registration by any
    checkout attempt, including one the payer abandoned; and `payment.*` covers
    `payment.created` and `payment.cancelled` as readily as `payment.captured`.
    A registration with a cancelled attempt in its past satisfied both while
    nobody had paid a cent — so switching to a fee-waived tier and back left it
    marked paid and never billed.

    Narrower than `status == "paid"` for the same reason it always was: a
    zero-fee tier produces that status with nothing received.
    """
    from ...models.payment_event import amount_received

    if reg.id is None:
        return False
    return amount_received(reg.id) > 0


# A hosted checkout is good for this long; Worldline expires the session
# afterwards. The edit lock is held for the same window, so an abandoned
# payment releases it rather than freezing the registration forever.
CHECKOUT_LOCK_MINUTES = 120


def payment_in_flight(reg):
    """The unsettled checkout blocking edits, or None.

    A checkout is minted against the amount owed at that moment. If the tier
    changes while the payer is on the gateway's page, the capture that comes
    back settles a price that no longer exists — and by then the money has
    moved and we have no refund API. Rather than reconcile that afterwards,
    the edit is refused while a payment is in the air.

    Only the last two hours count: the session expires, and a member who
    closed the tab must not be locked out of their own registration.
    """
    from datetime import timedelta

    from ...models import PaymentEvent

    if reg is None or reg.id is None:
        return None
    cutoff = datetime.utcnow() - timedelta(minutes=CHECKOUT_LOCK_MINUTES)
    started = (PaymentEvent.query
               .filter(PaymentEvent.registration_id == reg.id,
                       PaymentEvent.event_type == "checkout.created",
                       PaymentEvent.created_at >= cutoff)
               .order_by(PaymentEvent.id.desc())
               .first())
    if started is None:
        return None
    # Anything terminal for that same attempt releases the lock early.
    settled = (PaymentEvent.query
               .filter(PaymentEvent.registration_id == reg.id,
                       PaymentEvent.merchant_reference == started.merchant_reference,
                       PaymentEvent.event_type != "checkout.created",
                       PaymentEvent.id > started.id)
               .first())
    return None if settled else started


def checkout_lock_expires_at(started):
    """When the edit lock held by *started* lifts."""
    from datetime import timedelta

    return started.created_at + timedelta(minutes=CHECKOUT_LOCK_MINUTES)


@member_bp.route("/conferences/<slug>/register", methods=["GET", "POST"])
@login_required
def register_conf(slug):
    c = (Conference.query
         .filter_by(slug=slug)
         .filter(Conference.deleted_at.is_(None))
         .first_or_404())
    if c.is_draft:
        abort(404)
    if not c.accepts_registrations and not c.external_registration_url:
        flash("Registration is not open for this conference.", "error")
        return redirect(url_for("public.conference_detail", slug=c.slug))
    existing = (
        Registration.query
        .filter_by(user_id=current_user.id, conference_id=c.id)
        .filter(Registration.deleted_at.is_(None))
        .first()
    )

    tiers = list(c.price_tiers)
    schema = c.registration_form_schema
    sub_events = list(c.sub_events)

    if request.method == "POST":
        # First, before any of the form is looked at: a registration with a
        # payment in the air must not change, and that cannot depend on the
        # rest of the submission being valid. Server-side because the disabled
        # button is only the polite half — the form may have been rendered in
        # another tab before the payment was started.
        in_flight = payment_in_flight(existing)
        if in_flight is not None:
            flash("A payment for this registration is in progress, so it "
                  "can't be changed right now. If you didn't complete the "
                  "payment, you can edit again after "
                  f"{checkout_lock_expires_at(in_flight):%H:%M} UTC, "
                  "or as soon as the payment goes through.", "error")
            return redirect(url_for("member.dashboard"))

        tier_name = (request.form.get("tier") or "").strip()
        tier = next((t for t in tiers if t.name == tier_name), None)
        if not tier:
            flash("Please choose a registration tier.", "error")
            return render_template("member/register_conference.html",
                                   c=c, tiers=tiers, existing=existing,
                                   schema=schema, sub_events=sub_events)

        # Collect custom field data from schema
        custom_data: dict = {}
        if schema:
            for section in schema.get("sections", []):
                for field in section.get("fields", []):
                    key = field.get("key", "")
                    val = request.form.getlist(key) if field.get("type") == "checkbox-group" else request.form.get(key, "")
                    custom_data[key] = val

        # Collect sub-event registration data
        sub_event_data: dict = {}
        for se in sub_events:
            sekey = se.name.lower().replace(" ", "_")
            attending = request.form.get(f"_sub_event_{sekey}_attending") == "yes"
            entry = {"attending": attending}
            if attending and se.preference_schema:
                for pf in se.preference_schema.get("fields", []):
                    pfkey = pf.get("key", "")
                    pval = request.form.getlist(f"_sub_event_{sekey}_{pfkey}") if pf.get("type") == "checkbox-group" else request.form.get(f"_sub_event_{sekey}_{pfkey}", "")
                    entry[pfkey] = pval
            sub_event_data[sekey] = entry

        # Validate custom fields against schema
        if schema:
            form_errors = validate_form(schema, request.form)
            if form_errors:
                for err in form_errors:
                    flash(err, "error")
                return render_template("member/register_conference.html",
                                       c=c, tiers=tiers, existing=existing,
                                       schema=schema, sub_events=sub_events)

        reg = existing or Registration(user_id=current_user.id, conference_id=c.id)
        # The price is struck when the tier is chosen and then left alone. It
        # used to be re-derived on every save, which meant a member who paid
        # the early-bird rate and later came back to correct a dietary note —
        # after the early-bird deadline had passed — was silently re-priced at
        # the full rate, billed the difference, and emailed a demand for money
        # they could not pay. What someone was charged is a fact about their
        # registration, not something to recompute from today's date.
        tier_changed = existing is None or existing.tier_name != tier.name
        if tier_changed:
            reg.amount = (
                tier.early_bird_amount
                if tier.early_bird_amount and c.early_bird_deadline
                and c.early_bird_deadline >= datetime.utcnow().date()
                else tier.amount)
        amount = reg.amount or 0
        reg.tier_name = tier.name
        reg.dietary = (request.form.get("dietary") or "").strip()
        reg.accessibility = (request.form.get("accessibility") or "").strip()
        reg.custom_data = custom_data if custom_data else None
        reg.sub_events = sub_event_data if any(v.get("attending") for v in sub_event_data.values()) else None
        # Editing must not un-settle a registration somebody actually paid for
        # — resetting every save to "pending" would mark them unpaid and bill
        # them again for changing a dietary note. But "settled" has to mean
        # money moved, not merely status == "paid": see _settled_by_payment.
        prior_status = reg.status
        settled = _settled_by_payment(reg)
        if not settled:
            reg.status = "pending"
        if not existing:
            db.session.add(reg)
            db.session.flush()          # need the id before the token is stored
        reg.mint_pay_token()
        db.session.commit()
        audit.record("registration.saved",
                     target_kind="registration", target_id=reg.id,
                     summary=f"{current_user.email} → {c.slug} ({tier.name})")

        # Put the charge on the ledger. Until now it only ever carried credits
        # — payments in — with `amount` the sole record of what was owed, and
        # that is overwritten on every save. Recording the difference is what
        # makes an upgrade bill the difference rather than the whole fee, and a
        # downgrade credit it back rather than silently forgiving it.
        #
        # Called on every save rather than only when the tier changed, because
        # it is the difference that is recorded: an unchanged price books a
        # delta of zero and writes nothing. That keeps this the one place a
        # registration with no charge lines at all — a row that predates them
        # and escaped the backfill — still gets its baseline.
        reg.charge_to(amount, reason=(
            f"tier set to {tier.name}"
            + ("" if amount else " — no fee")))

        outstanding = reg.amount_due
        if reg.status == "refunded":
            flash("Registration updated.", "success")
        elif outstanding > 0:
            # Owing something — whether that is the whole fee, or the balance
            # after an upgrade on a registration already part paid.
            if payments_open_to_members():
                send_payment_email(reg)
                flash("Registration saved. A payment link has been emailed to "
                      "you.", "success")
            else:
                flash("Registration saved. Our payment portal is under "
                      "construction — you will be notified when it is ready.",
                      "warning")
        elif settled:
            flash("Registration updated.", "success")
        else:
            # Nothing owed and nothing received: a sponsor, plenary speaker or
            # comped attendee. Leaving it "pending" would park it in the
            # treasurer's unpaid list for a conference that owes nothing.
            reg.status = "paid"
            db.session.commit()
            # Only on the way in. Re-saving a fee-waived registration should
            # not stack another ledger row or send the confirmation again.
            if prior_status != "paid":
                record_payment_event(
                    merchant_reference=_reg_merchant_reference(reg),
                    registration_id=reg.id,
                    event_type="registration.no_payment_due",
                    amount=0,
                    note=f"{reg.reference}: {tier.name} carries no fee",
                )
                send_registration_confirmation(reg)
                flash("Registration confirmed — no payment is required.",
                      "success")
            else:
                flash("Registration updated.", "success")
        return redirect(url_for("member.dashboard"))

    # The lock is enforced on POST regardless; passing it to the GET is what
    # lets the page say so up front instead of failing on submit.
    in_flight = payment_in_flight(existing)
    return render_template("member/register_conference.html",
                           c=c, tiers=tiers, existing=existing,
                           schema=schema, sub_events=sub_events,
                           payment_locked=in_flight is not None,
                           payment_lock_until=(checkout_lock_expires_at(in_flight)
                                               if in_flight else None))


# ---------------------------------------------------------------------------
# Abstract submission
# ---------------------------------------------------------------------------


def _validate_reference(key: int, doi: str, body: str) -> list[str]:
    errors: list[str] = []
    marker = f"[{key}]"
    if marker not in body:
        errors.append(
            f"Reference {marker} ({doi}) is not cited in the abstract text. "
            f"Add {marker} where this reference belongs.")
    if not doi.startswith("10."):
        errors.append(f"Reference {marker} DOI does not look valid (should start with 10.).")
    return errors


def submission_errors(*, title: str, authors: str, body: str,
                      references: list[dict], schema, form_data) -> list[str]:
    """Everything that must hold before an abstract counts as submitted.

    One function, because there are now two ways to submit — the form, and the
    Submit button on the preview page — and two copies of these rules would
    drift into one of them accepting what the other refuses.

    `form_data` is whatever the custom-field schema should be validated
    against: the posted form when submitting from the form, the stored
    `custom_data` when submitting a draft that was already saved.
    """
    errors: list[str] = []
    if not (title and authors and body):
        errors.append("Title, authors and abstract body are required.")
    if len(title.split()) > 15:
        errors.append(f"Title is {len(title.split())} words — the limit is 15.")
    if len(body.split()) > 320:
        errors.append(f"Abstract body is {len(body.split())} words — "
                      f"the limit is 300 (soft cap 320).")
    if schema:
        errors.extend(validate_form(schema, form_data))

    ref_keys = {r["key"] for r in references}
    for ref in references:
        errors.extend(_validate_reference(ref["key"], ref["doi"], body))
    for marker in re.findall(r"\[(\d+)\]", body):
        n = int(marker)
        if n not in ref_keys:
            errors.append(f"Citation [\u200B{n}\u200B] appears in text but "
                          f"has no matching reference.")
    return errors


def _abstract_quota_reached(c, exclude_id=None):
    """Has this user used up their abstract allowance for conference *c*?

    Drafts are deliberately not counted — an author may keep as many works
    in progress as they like — which means the cap only bites at the moment
    a draft turns into a submission. Every route that performs that
    transition has to ask, not just the one that creates a new abstract.

    *exclude_id* leaves one abstract out of the count, so re-submitting an
    abstract that is already counted (a "revise" going back in) doesn't
    collide with itself.
    """
    if not c.max_abstracts_per_user:
        return False
    q = (Abstract.query
         .filter_by(user_id=current_user.id, conference_id=c.id)
         .filter(Abstract.deleted_at.is_(None))
         .filter(Abstract.status != "draft"))
    if exclude_id:
        q = q.filter(Abstract.id != exclude_id)
    return q.count() >= c.max_abstracts_per_user


@member_bp.route("/conferences/<slug>/abstract", methods=["GET", "POST"])
@login_required
def submit_abstract(slug):
    c = (Conference.query
         .filter_by(slug=slug)
         .filter(Conference.deleted_at.is_(None))
         .first_or_404())
    if c.is_draft:
        abort(404)
    if not c.accepts_abstracts and not c.external_abstract_url:
        flash("Abstract submission is not open for this conference.", "error")
        return redirect(url_for("public.conference_detail", slug=c.slug))

    # Enforce per-user abstract limit. Starting a fresh abstract is refused
    # outright; editing is allowed through, because saving a draft is always
    # permitted — the limit is re-checked below, at the point the draft would
    # actually become a submission.
    edit_id = request.args.get("edit", type=int) or request.form.get("edit_id", type=int)
    if not edit_id and _abstract_quota_reached(c):
        flash(
            f"You've reached the limit of {c.max_abstracts_per_user} "
            f"abstract(s) for this conference.", "error",
        )
        return redirect(url_for("public.conference_detail", slug=c.slug))

    tracks = c.tracks_list()
    abstract_schema = c.abstract_form_schema

    # Edit mode — load existing abstract
    draft = None
    if edit_id:
        draft = (Abstract.query
                 .filter_by(id=edit_id, user_id=current_user.id)
                 .filter(Abstract.deleted_at.is_(None))
                 .first())
        # Without this the URL reaches a decided abstract, and saving it sets
        # the status back to "submitted" — discarding the decision while
        # leaving decided_by and decided_at pointing at whoever made it.
        if draft is not None and not draft.is_editable:
            flash("That abstract can no longer be edited.", "error")
            return redirect(url_for("member.dashboard"))

    if request.method == "POST":
        action = request.form.get("action", "submit")
        is_draft = action in ("draft", "preview")

        title = (request.form.get("title") or "").strip()
        authors = (request.form.get("authors") or "").strip()
        body = (request.form.get("body") or "").strip()
        track = (request.form.get("track") or "").strip()
        ptype = (request.form.get("presentation_type") or "Either").strip()
        keywords = (request.form.get("keywords") or "").strip()
        coi = (request.form.get("coi") or "").strip()
        try:
            website_url = Abstract.clean_website(request.form.get("website_url"))
        except ValueError as e:
            website_url = ""
            errors_early = [str(e)]
        else:
            errors_early = []

        # Collect custom field data
        custom_data: dict = {}
        if abstract_schema:
            for section in abstract_schema.get("sections", []):
                for field in section.get("fields", []):
                    key = field.get("key", "")
                    val = request.form.getlist(key) if field.get("type") == "checkbox-group" else request.form.get(key, "")
                    if val:
                        custom_data[key] = val

        # Collect references
        ref_dois = request.form.getlist("ref_doi[]")
        references = []
        ref_keys = set()
        seen_dois = set()
        for i, doi in enumerate(ref_dois):
            doi = normalize_doi(doi)
            if doi and doi not in seen_dois:
                key = len(references) + 1
                references.append({"key": key, "doi": doi})
                ref_keys.add(key)
                seen_dois.add(doi)

        presenting_author_index = 0
        try:
            presenting_author_index = int(
                request.form.get("presenting_author_index", "0") or "0")
        except ValueError:
            pass

        errors: list[str] = list(errors_early)

        if not is_draft:
            errors.extend(submission_errors(
                title=title, authors=authors, body=body,
                references=references, schema=abstract_schema,
                form_data=request.form))

        elif not (title and authors):
            errors.append("Title and at least one author are required even for drafts.")

        if errors:
            # A failed submission used to leave no trace anywhere: the form
            # simply came back with red text, nothing was written, and nothing
            # was recorded. So an author reporting "it won't let me submit"
            # could not be investigated at all — the audit log showed their
            # login and then silence. Record the attempt and what stopped it.
            audit.record(
                "abstract.submit_failed",
                target_kind="abstract",
                target_id=draft.id if draft else None,
                summary=(f"{current_user.email} → {c.slug}: "
                         + "; ".join(errors))[:400])
            for err in errors:
                flash(err, "error")
            return render_template("member/submit_abstract.html",
                                   c=c, tracks=tracks, form=request.form,
                                   abstract_schema=abstract_schema, draft=draft)

        if not is_draft and _abstract_quota_reached(
                c, exclude_id=draft.id if draft else None):
            # Back to the form, not the dashboard. Every other refusal above
            # re-renders with what was typed; redirecting here would discard
            # a whole abstract because of a limit the author cannot see from
            # the editor, which is the same silent loss the rest of this route
            # was fixed to stop doing.
            flash(
                f"You've reached the limit of {c.max_abstracts_per_user} "
                f"abstract(s) for this conference. Save this as a draft if "
                f"you would like to keep it.", "error",
            )
            return render_template("member/submit_abstract.html",
                                   c=c, tracks=tracks, form=request.form,
                                   abstract_schema=abstract_schema, draft=draft)

        # Save abstract
        if draft:
            a = draft
        else:
            a = Abstract(user_id=current_user.id, conference_id=c.id)
        revision = a.status == "submitted"
        a.title = title
        a.authors = authors
        a.body = body
        a.track = track
        a.presentation_type = ptype
        a.keywords = keywords
        a.coi = coi
        a.website_url = website_url
        a.custom_data = custom_data if custom_data else None
        a.presenting_author_index = presenting_author_index
        a.references = references if references else None
        if not is_draft:
            a.status = "submitted"
        elif not draft:
            a.status = "draft"
        # else: keep existing status (e.g. "revise") on draft saves

        f = request.files.get("figure")
        if f and f.filename:
            try:
                new_fig = save_figure(
                    f,
                    upload_folder=current_app.config["UPLOAD_FOLDER"],
                    max_bytes=current_app.config["MAX_FIGURE_BYTES"],
                )
            except UploadError as e:
                flash(str(e), "error")
                return render_template("member/submit_abstract.html",
                                       c=c, tracks=tracks, form=request.form,
                                       abstract_schema=abstract_schema, draft=draft)
            if a.figure_filename:
                remove_upload(current_app.config["UPLOAD_FOLDER"],
                              a.figure_filename)
            a.figure_filename = new_fig
        elif request.form.get("remove_figure") == "1" and a.figure_filename:
            remove_upload(current_app.config["UPLOAD_FOLDER"], a.figure_filename)
            a.figure_filename = None

        pic = request.files.get("profile_picture")
        if pic and pic.filename:
            try:
                rel = save_image(pic,
                                 upload_folder=current_app.config["UPLOAD_FOLDER"],
                                 subdir="abstracts", prefix="profile-",
                                 max_bytes=current_app.config["MAX_HERO_BYTES"],
                                 target_size=400, force_webp=True)
            except UploadError as e:
                flash(str(e), "error")
                return render_template("member/submit_abstract.html",
                                       c=c, tracks=tracks, form=request.form,
                                       abstract_schema=abstract_schema, draft=draft)
            if a.profile_picture_filename:
                remove_upload(current_app.config["UPLOAD_FOLDER"],
                              f"abstracts/{a.profile_picture_filename}")
            a.profile_picture_filename = rel.split("/", 1)[-1]
        elif (request.form.get("remove_profile_picture") == "1"
                and a.profile_picture_filename):
            remove_upload(current_app.config["UPLOAD_FOLDER"],
                          f"abstracts/{a.profile_picture_filename}")
            a.profile_picture_filename = None

        if not draft:
            db.session.add(a)
        db.session.commit()

        # Auto-link abstract to an existing registration for this conference.
        if a.registration_id is None:
            reg = Registration.query.filter_by(
                user_id=current_user.id,
                conference_id=c.id,
                deleted_at=None,
            ).first()
            if reg:
                a.registration_id = reg.id
                db.session.commit()

        if action == "preview":
            return redirect(url_for("member.preview_abstract", aid=a.id))

        audit.record(
            "abstract.draft" if is_draft else
            "abstract.revised" if revision else "abstract.submitted",
            target_kind="abstract", target_id=a.id,
            summary=f"{current_user.email} → {c.slug}: {title}")

        # A receipt, not a decision — and only on an actual submission, so
        # saving a draft five times does not send five emails.
        receipted = False
        if not is_draft and c.abstract_receipt_email:
            from ...services.abstract_latex import send_abstract_receipt
            receipted = send_abstract_receipt(
                a, uploads_root=Path(current_app.config["UPLOAD_FOLDER"]),
                revision=revision)

        if is_draft:
            flash("Draft saved.", "success")
        elif revision:
            flash("Your changes have been saved — we'll use this version."
                  + (" A copy is on its way to your inbox." if receipted
                     else ""), "success")
        elif receipted:
            flash("Abstract submitted — a confirmation with a PDF copy is on "
                  "its way to your inbox. You'll be notified again after "
                  "review.", "success")
        else:
            flash("Abstract submitted. You'll be notified after review.",
                  "success")
        return redirect(url_for("member.dashboard"))

    # GET — pre-fill form for editing
    form_data: dict = {}
    if draft:
        form_data = {
            "title": draft.title,
            "authors": draft.authors,
            "body": draft.body,
            "track": draft.track,
            "presentation_type": draft.presentation_type,
            "keywords": draft.keywords,
            "coi": draft.coi,
            "website_url": draft.website_url,
            "presenting_author_index": draft.presenting_author_index,
            **{k: v for k, v in (draft.custom_data or {}).items()},
        }

    return render_template("member/submit_abstract.html",
                           c=c, tracks=tracks, form=form_data, draft=draft,
                           abstract_schema=abstract_schema)


# ---------------------------------------------------------------------------
# Abstract preview (fetches DOI metadata for references)
# ---------------------------------------------------------------------------

@member_bp.route("/abstracts/<int:aid>/preview")
@login_required
def preview_abstract(aid):
    a = Abstract.query.get_or_404(aid)
    if a.user_id != current_user.id:
        abort(403)

    refs_with_meta: list[dict] = []
    for ref in (a.references or []):
        doi = ref["doi"]
        meta = fetch_metadata(doi)
        if meta:
            refs_with_meta.append({
                "key": ref["key"],
                "doi": doi,
                "citation": format_reference(meta),
            })
        else:
            refs_with_meta.append({
                "key": ref["key"],
                "doi": doi,
                "citation": doi,
            })

    return render_template("member/preview_abstract.html",
                           a=a, refs_with_meta=refs_with_meta,
                           can_submit=a.status in ("draft", "revise"))


@member_bp.route("/abstracts/<int:aid>/submit", methods=["POST"])
@login_required
def submit_previewed_abstract(aid):
    """Submit an abstract that is sitting as a draft.

    Preview saves the abstract as a draft and then offered no way forward:
    the page's most prominent control was "Dashboard", so an author who
    previewed their work — the careful thing to do — could reasonably believe
    they had finished while their abstract stayed unsubmitted. This is the
    missing step.

    Validation is the same function the form uses, against the stored values,
    so nothing can be submitted here that the form would have refused.
    """
    a = (Abstract.query
         .filter_by(id=aid, user_id=current_user.id)
         .filter(Abstract.deleted_at.is_(None))
         .first_or_404())
    c = a.conference
    if c is None or c.is_draft:
        abort(404)
    if a.status not in ("draft", "revise"):
        flash("That abstract has already been submitted.", "success")
        return redirect(url_for("member.dashboard"))
    if not c.accepts_abstracts:
        flash("Abstract submission has closed for this conference.", "error")
        return redirect(url_for("member.dashboard"))

    if _abstract_quota_reached(c, exclude_id=a.id):
        flash(
            f"You've reached the limit of {c.max_abstracts_per_user} "
            f"abstract(s) for this conference.", "error",
        )
        return redirect(url_for("member.dashboard"))

    errors = submission_errors(
        title=a.title or "", authors=a.authors or "", body=a.body or "",
        references=a.references or [], schema=c.abstract_form_schema,
        form_data=a.custom_data or {})
    if errors:
        audit.record("abstract.submit_failed", target_kind="abstract",
                     target_id=a.id,
                     summary=(f"{current_user.email} → {c.slug} (from preview): "
                              + "; ".join(errors))[:400])
        for err in errors:
            flash(err, "error")
        # Back to the form, which is where the problems can be fixed.
        return redirect(url_for("member.submit_abstract", slug=c.slug, edit=a.id))

    a.status = "submitted"
    db.session.commit()
    audit.record("abstract.submitted", target_kind="abstract", target_id=a.id,
                 summary=f"{current_user.email} → {c.slug}: {a.title}")

    receipted = False
    if c.abstract_receipt_email:
        from ...services.abstract_latex import send_abstract_receipt
        receipted = send_abstract_receipt(
            a, uploads_root=Path(current_app.config["UPLOAD_FOLDER"]))
    flash("Abstract submitted." + (" A confirmation with a PDF copy is on its "
                                   "way to your inbox." if receipted else "")
          + " You'll be notified after review.", "success")
    return redirect(url_for("member.dashboard"))


# ---------------------------------------------------------------------------
# Abstract soft-delete (OTP-confirmed, member)
# ---------------------------------------------------------------------------

@member_bp.route("/abstracts/<int:aid>/delete-draft", methods=["POST"])
@login_required
def delete_draft_abstract(aid):
    """Discard a draft outright.

    A draft has never been sent anywhere and nobody has read it, so the emailed
    code that guards a real submission would only be an obstacle to throwing
    away one's own unfinished work.
    """
    a = Abstract.query.get_or_404(aid)
    if a.user_id != current_user.id:
        abort(403)
    if a.deleted_at is not None or a.status != "draft":
        flash("That abstract can't be discarded here.", "error")
        return redirect(url_for("member.dashboard"))

    title = a.title
    a.deleted_at = datetime.utcnow()
    db.session.commit()
    audit.record("abstract.draft_discarded",
                 target_kind="abstract", target_id=a.id,
                 summary=f"{current_user.email} discarded draft \"{title}\"")
    flash(f"Discarded draft \"{title}\".", "success")
    return redirect(url_for("member.dashboard"))


@member_bp.route("/abstracts/<int:aid>/delete-request", methods=["POST"])
@login_required
def delete_abstract_request(aid):
    a = Abstract.query.get_or_404(aid)
    if a.user_id != current_user.id:
        abort(403)
    if a.deleted_at is not None:
        flash("This abstract has already been deleted.", "error")
        return redirect(url_for("member.dashboard"))
    if a.status not in ("submitted", "accepted"):
        flash("This abstract can no longer be deleted.", "error")
        return redirect(url_for("member.dashboard"))
    code = f"{secrets.randbelow(1_000_000):06d}"
    ttl = current_app.config["OTP_TTL_SECONDS"]
    ok = send_mail(
        to=current_user.email,
        subject="Confirm abstract deletion",
        body=(f"You requested to delete the abstract \"{a.title}\".\n\n"
              f"Confirmation code: {code}\n\n"
              f"This code expires in {ttl // 60} minutes. "
              f"If you didn't request this, ignore the email."),
    )
    if not ok:
        flash("Failed to send confirmation email. Please try again.", "error")
        return redirect(url_for("member.dashboard"))
    db.session.add(OTPCode(
        email=current_user.email.lower(),
        code=code,
        purpose="abstract_delete",
        expires_at=datetime.utcnow() + timedelta(seconds=ttl),
        ip=request.remote_addr,
    ))
    db.session.commit()
    flash("A confirmation code has been sent to your email.", "success")
    return redirect(url_for("member.delete_abstract_confirm", aid=a.id))


@member_bp.route("/abstracts/<int:aid>/delete-confirm", methods=["GET", "POST"])
@login_required
def delete_abstract_confirm(aid):
    a = Abstract.query.get_or_404(aid)
    if a.user_id != current_user.id:
        abort(403)
    if a.deleted_at is not None:
        flash("This abstract has already been deleted.", "error")
        return redirect(url_for("member.dashboard"))
    if a.status not in ("submitted", "accepted"):
        flash("This abstract can no longer be deleted.", "error")
        return redirect(url_for("member.dashboard"))
    if request.method == "POST":
        entered = (request.form.get("code") or "").strip().replace(" ", "")
        otp = (OTPCode.query
               .filter_by(email=current_user.email.lower(),
                          code=entered,
                          purpose="abstract_delete",
                          consumed_at=None)
               .order_by(OTPCode.id.desc())
               .first())
        if not (otp and otp.is_valid()):
            flash("That code didn't match, or it has expired.", "error")
            return render_template("member/abstract_delete_confirm.html", a=a)
        otp.consumed_at = datetime.utcnow()
        title = a.title
        a.deleted_at = datetime.utcnow()
        db.session.commit()
        audit.record("abstract.deleted",
                     target_kind="abstract", target_id=a.id,
                     summary=f"{current_user.email} deleted \"{title}\"")
        flash(f"Deleted abstract \"{title}\".", "success")
        return redirect(url_for("member.dashboard"))
    return render_template("member/abstract_delete_confirm.html", a=a)


# ---------------------------------------------------------------------------
# Author-only figure download
# ---------------------------------------------------------------------------

@member_bp.route("/abstracts/<int:aid>/figure")
@login_required
def abstract_figure(aid):
    a = Abstract.query.get_or_404(aid)
    # Author OR anyone with abstract review permission.
    if a.user_id != current_user.id and not current_user.has_permission("abs.review"):
        abort(403)
    if not a.figure_filename:
        abort(404)
    folder = Path(current_app.config["UPLOAD_FOLDER"]) / "abstracts"
    name = a.figure_filename.split("/", 1)[-1]
    return send_from_directory(folder, name)


@member_bp.route("/abstracts/<int:aid>/pdf")
@login_required
def abstract_pdf(aid):
    """The author's own abstract, as the booklet will print it.

    Same access rule as the figure download: the author, or anyone who
    reviews abstracts.
    """
    from ...services.abstract_latex import (abstract_pdf_filename,
                                            render_abstract_pdf)
    from ...services.documents import RenderError

    a = Abstract.query.get_or_404(aid)
    if a.user_id != current_user.id and not current_user.has_permission("abs.review"):
        abort(403)
    try:
        pdf = render_abstract_pdf(
            a, uploads_root=Path(current_app.config["UPLOAD_FOLDER"]))
    except RenderError as e:
        current_app.logger.warning("Abstract PDF failed for %s: %s", aid, e)
        flash("That abstract could not be rendered just now. Please try "
              "again shortly, or contact us if it keeps happening.", "error")
        return redirect(url_for("member.dashboard"))
    return send_file(BytesIO(pdf), mimetype="application/pdf",
                     as_attachment=True,
                     download_name=abstract_pdf_filename(a))


# ---------------------------------------------------------------------------
# Review form — reviewers score and comment on assigned abstracts
# ---------------------------------------------------------------------------

@member_bp.route("/review/<int:assignment_id>", methods=["GET", "POST"])
@login_required
def review_form(assignment_id):
    ra = (ReviewAssignment.query
          .options(db.joinedload(ReviewAssignment.abstract))
          .get_or_404(assignment_id))
    if ra.reviewer_id != current_user.id:
        abort(403)

    a = ra.abstract
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        ra.score = int(request.form.get("score", 0))
        ra.recommendation = (request.form.get("recommendation") or "").strip() or None
        ra.comments_author = (request.form.get("comments_author") or "").strip()
        ra.comments_chair = (request.form.get("comments_chair") or "").strip()

        if action == "submit":
            if ra.score is None or ra.score < 0 or ra.score > 100:
                flash("Please provide a score between 0 and 100.", "error")
                return render_template("member/review_form.html", ra=ra, a=a)
            if not ra.recommendation:
                flash("Please select a recommendation.", "error")
                return render_template("member/review_form.html", ra=ra, a=a)
            ra.status = "completed"
            ra.submitted_at = datetime.utcnow()
            db.session.commit()
            flash("Review submitted. Thank you.", "success")
            return redirect(url_for("member.dashboard"))
        else:
            ra.status = "pending"
            db.session.commit()
            flash("Draft saved.", "success")

    return render_template("member/review_form.html", ra=ra, a=a)


@member_bp.route("/review/<int:assignment_id>/recuse", methods=["POST"])
@login_required
def review_recuse(assignment_id):
    ra = ReviewAssignment.query.get_or_404(assignment_id)
    if ra.reviewer_id != current_user.id:
        abort(403)
    if ra.status != "pending":
        flash("You can only recuse from a pending review.", "error")
        return redirect(url_for("member.review_form", assignment_id=ra.id))

    reason = (request.form.get("decline_reason") or "").strip()
    ra.decline_reason = reason
    ra.status = "declined"
    db.session.commit()
    flash("You have been removed from this review. Thank you for letting us know.", "success")
    return redirect(url_for("member.dashboard"))


# ---------------------------------------------------------------------------
# Payment stub — replace with real payment provider integration.
# ---------------------------------------------------------------------------

@member_bp.route("/registrations/<int:reg_id>/document/<kind>")
@login_required
def registration_document(reg_id, kind):
    """The member's own invoice or receipt for a registration.

    Compiled at most once per distinct document and served from cache
    afterwards, so this stays cheap however often it is asked for. The budget
    below bounds the misses, which are the only part that costs anything.
    """
    from ...models.rate_limit import allow
    from ...services.documents import RenderError
    from ...services.invoice import _safe_ref, registration_document

    if kind not in ("invoice", "receipt"):
        abort(404)
    reg = Registration.query.get_or_404(reg_id)
    if reg.user_id != current_user.id or reg.deleted_at is not None:
        abort(403)

    if kind == "receipt" and reg.status not in ("paid", "refunded"):
        flash("A receipt is available once your payment has gone through.",
              "error")
        return redirect(url_for("member.dashboard"))
    if kind == "invoice" and reg.amount_due <= 0:
        flash("Nothing is outstanding on this registration.", "error")
        return redirect(url_for("member.dashboard"))

    if not allow(f"doc.{current_user.id}", str(reg_id), limit=20,
                 per_seconds=3600):
        flash("That has been requested a few too many times just now. Please "
              "try again shortly.", "error")
        return redirect(url_for("member.dashboard"))

    try:
        pdf = registration_document(reg, kind)
    except RenderError as e:
        current_app.logger.warning("Document %s failed for reg %s: %s",
                                   kind, reg_id, e)
        flash("That document could not be produced just now. Please try again "
              "shortly, or contact us if it keeps happening.", "error")
        return redirect(url_for("member.dashboard"))

    return send_file(BytesIO(pdf), mimetype="application/pdf",
                     as_attachment=True,
                     download_name=f"{kind}-{_safe_ref(reg.reference)}.pdf")


@member_bp.route("/pay/<int:reg_id>")
@login_required
def pay_registration(reg_id):
    """Compatibility entry point for links already in members' inboxes and for
    the dashboard's Pay button. Paying itself lives on the public, token-keyed
    route so a forwarded link works for whoever actually settles it — this only
    checks ownership and hands over."""
    reg = Registration.query.get_or_404(reg_id)
    if reg.user_id != current_user.id:
        abort(403)
    return redirect(url_for("public.pay_registration",
                            token=reg.ensure_pay_token()))


def _can_test_payments() -> bool:
    """Admins and financial managers may pay before the portal opens."""
    return current_user.is_admin or current_user.has_permission("financial.manage")


@member_bp.route("/pay/<int:reg_id>/checkout", methods=["POST"])
@login_required
def pay_checkout(reg_id):
    """Create a hosted checkout session and send the member to Worldline."""
    reg = Registration.query.get_or_404(reg_id)
    if reg.user_id != current_user.id:
        abort(403)
    # The same states the public token route refuses. Checking only "paid"
    # here left a refunded registration payable through a direct POST, taking
    # money against nothing owed.
    if reg.status in ("paid", "refunded"):
        flash(f"This registration is already {reg.status}.", "success")
        return redirect(url_for("member.dashboard"))
    if reg.status == "processing":
        flash("Your payment is being processed.", "success")
        return redirect(url_for("member.pay_result", reg_id=reg.id))
    if not (payments_open_to_members() or _can_test_payments()):
        flash("The payment portal is not yet available.", "warning")
        return redirect(url_for("member.pay_registration", reg_id=reg.id))
    redirect_url = initiate_payment(reg)
    if not redirect_url:
        flash("The payment service could not be reached. Please try again "
              "shortly, or contact us if the problem persists.", "error")
        return redirect(url_for("member.pay_registration", reg_id=reg.id))
    return redirect(redirect_url)


@member_bp.route("/pay/<int:reg_id>/result")
@login_required
def pay_result(reg_id):
    reg = Registration.query.get_or_404(reg_id)
    if reg.user_id != current_user.id:
        abort(403)
    site = get_site_settings()

    if reg.status in ("paid", "refunded", "processing"):
        return render_template("member/pay_result.html", reg=reg, site=site,
                               already_complete=True)

    return render_template("member/pay_result.html", reg=reg, site=site,
                           already_complete=False)
