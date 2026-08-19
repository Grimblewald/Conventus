"""Conference registrations attached to a user + tier."""
from __future__ import annotations

from datetime import datetime

from ..extensions import db




class Registration(db.Model):
    __tablename__ = "registrations"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"),
                        nullable=False, index=True)
    conference_id = db.Column(db.Integer, db.ForeignKey("conferences.id"),
                              nullable=False, index=True)

    tier_name = db.Column(db.String(80), nullable=False)
    amount = db.Column(db.Integer, default=0, nullable=False)

    dietary = db.Column(db.String(200), default="")
    accessibility = db.Column(db.String(400), default="")
    custom_data = db.Column(db.JSON, default=None)
    sub_events = db.Column(db.JSON, default=None)
    status = db.Column(db.String(40), default="pending", nullable=False)
    payment_sent_at = db.Column(db.DateTime, nullable=True)
    transaction_id = db.Column(db.String(120), nullable=True)
    last_webhook_event = db.Column(db.String(80), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)
    deleted_at = db.Column(db.DateTime, nullable=True, index=True)

    # Capability token for the durable pay link. Stored rather than derived
    # from the id: the id already yields `reference`, which a payer quotes on a
    # bank transfer and the treasurer searches on, so a derived link could only
    # be revoked by re-numbering the registration — which would change that
    # reference under the payer and orphan its PaymentEvent history. A stored
    # token is revoked by writing a new one, and survives a SECRET_KEY rotation
    # that an HMAC would not.
    pay_token = db.Column(db.String(64), nullable=True, unique=True, index=True)

    # What is still owed, maintained from the ledger (see
    # payment_event.recompute_outstanding). Stored rather than summed on every
    # read so there is one number, computed one way, for the places that bill,
    # display and reconcile — `amount` is only ever the current sticker price
    # of the chosen tier, and says nothing about what has been paid against it.
    amount_outstanding = db.Column(db.Integer, default=0, nullable=False,
                                   server_default="0")

    conference = db.relationship("Conference")

    def ensure_pay_token(self) -> str:
        """This registration's pay token, minting one if it somehow has none.

        32 bytes of urlsafe randomness: the link is the only thing standing in
        for a login, so it has to be unguessable and unenumerable in a way a
        sequential id never was.

        Tokens are minted where the registration is saved, and backfilled by
        migration for older rows, so in practice this only ever reads. The
        mint stays as a fallback because a registration created by some future
        path with no token would otherwise 500 on a link it needs to serve.
        """
        if not self.pay_token:
            self.mint_pay_token()
            from ..extensions import db as _db
            _db.session.commit()
        return self.pay_token

    def mint_pay_token(self) -> str:
        """Give this registration a pay token if it lacks one. Caller commits."""
        if not self.pay_token:
            import secrets
            self.pay_token = secrets.token_urlsafe(32)
        return self.pay_token

    def charged_total(self) -> int:
        """Total charged to this registration, read from its charge lines.

        A pure read. This used to seed a missing baseline on the spot, which
        made it a write — and `amount_due` calls it, so merely loading the pay
        page appended a financial record and committed whatever else the
        session was holding. Baselines are now seeded where the money actually
        changes: at registration save, and once by migration for the rows that
        predate charge lines.
        """
        from .payment_event import CHARGE_EVENTS, PaymentEvent

        charged = (PaymentEvent.query
                   .filter(PaymentEvent.registration_id == self.id,
                           PaymentEvent.event_type.in_(CHARGE_EVENTS))
                   .with_entities(PaymentEvent.amount).all())
        return sum((a or 0) for (a,) in charged)

    @property
    def amount_due(self) -> int:
        """What to bill right now — never less than nothing.

        The single number the pay link, the payment email and the admin views
        all read, so an upgrade after a part payment asks for the difference
        rather than the whole fee twice.
        """
        return max(0, self.amount_outstanding or 0)

    def charge_to(self, new_amount: int, *, reason: str) -> int:
        """Move the amount charged for this registration to *new_amount*.

        Appends the difference as a ledger line rather than rewriting what was
        charged before, so a tier change, an upgrade and an early-bird lapse
        are all the same operation and all remain auditable. Returns the delta
        recorded (0 when nothing changed, so callers can stay quiet).

        A registration with no charge lines yet reads as nothing charged, so
        the first call books the full amount — which is exactly right on the
        way in, and why the rows that predate charge lines are backfilled by
        migration rather than lazily on read.
        """
        from .payment_event import record_payment_event

        already = self.charged_total()
        delta = int(new_amount) - already
        if delta:
            record_payment_event(
                registration_id=self.id,
                merchant_reference=self.reference,
                event_type="registration.payment_due",
                amount=delta,
                note=f"{self.reference}: {reason}")
        return delta

    @property
    def reference(self) -> str:
        """The registration's payer-facing reference, e.g. REG-000123.

        Derived from the id rather than stored: it must exist the moment the
        registration does, because a member paying by bank transfer needs
        something to quote long before any card checkout mints a merchant
        reference. Deriving it also means it can never drift, be blank, or
        collide — the id already guarantees all three.

        Distinct from `transaction_id` (the gateway's per-operation id) and
        from the checkout's merchant reference (reg_<id>-c<conf>u<user>-<hex>,
        minted at checkout and absent until then).
        """
        return f"REG-{self.id:06d}"

    @property
    def sanitized_reference(self) -> str:
        """`reference` as it survives a bank reference field — see
        app.services.invoice.sanitized_reference for why the punctuation goes.
        """
        return self.reference.replace("-", "")
