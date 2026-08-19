"""Per-resource request throttling.

Rate limiting keyed on the thing being protected rather than on the client
asking. A payment link is used by one or two people — the member, or the
finance office they forwarded it to — so the link itself is a far better
budget than an IP address, which Cloudflare collapses to one value for every
visitor and which any attacker who cares can rotate at will.

Deliberately its own table, touched by nothing financial. These rows are
operational bookkeeping: they can be wiped, they can be wrong, and losing
them costs nothing but a window of unthrottled requests. That is what makes
it acceptable for a GET to write here while GETs may not write to the ledger.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..extensions import db


class RateBucket(db.Model):
    __tablename__ = "rate_buckets"

    id = db.Column(db.Integer, primary_key=True)
    # "<scope>:<resource>", e.g. "paylink.view:<token>".
    key = db.Column(db.String(160), nullable=False, unique=True, index=True)
    window_start = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    count = db.Column(db.Integer, nullable=False, default=0)


def allow(scope: str, resource: str, *, limit: int, per_seconds: int) -> bool:
    """Consume one unit of *resource*'s budget for *scope*. False when spent.

    A fixed window, not a sliding one: a caller who times requests around the
    boundary can get up to twice the limit across two windows. That is fine.
    The point is to bound abuse to a brief hiccup, not to be exact — and an
    exact limiter costs a lock on the hot path of the payment page.

    Never raises: a throttle that errors must not be what stops somebody
    paying, so a failure here fails open and is logged by the caller's
    surroundings.
    """
    if not resource:
        return True
    key = f"{scope}:{resource}"[:160]
    now = datetime.utcnow()
    try:
        bucket = RateBucket.query.filter_by(key=key).first()
        if bucket is None:
            db.session.add(RateBucket(key=key, window_start=now, count=1))
            db.session.commit()
            return True
        if bucket.window_start < now - timedelta(seconds=per_seconds):
            bucket.window_start = now
            bucket.count = 1
            db.session.commit()
            return True
        bucket.count += 1
        db.session.commit()
        return bucket.count <= limit
    except Exception:
        db.session.rollback()
        return True


def purge_expired(older_than_seconds: int = 86_400) -> int:
    """Drop buckets nothing has touched in a day. Nothing depends on them."""
    cutoff = datetime.utcnow() - timedelta(seconds=older_than_seconds)
    n = RateBucket.query.filter(RateBucket.window_start < cutoff).delete()
    db.session.commit()
    return n
