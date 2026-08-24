"""How deep the background work queue is, across every worker process.

Each process holds its own queue in memory, so no one of them can answer "how
much work is outstanding on this box" — which is the only question worth
asking on the evening of a deadline. Each records what it sees here, one row
per process per hour, and the answer is assembled from all of them.

Rows are a diagnostic, not a record: they can be deleted at any time and are
pruned after a week.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..extensions import db

_RETAIN = timedelta(days=7)


class QueueStat(db.Model):
    __tablename__ = "queue_stats"
    __table_args__ = (
        db.UniqueConstraint("worker", "hour_start", name="uq_queue_stat_slot"),
    )

    id = db.Column(db.Integer, primary_key=True)
    worker = db.Column(db.String(80), nullable=False)
    hour_start = db.Column(db.DateTime, nullable=False, index=True)
    pending = db.Column(db.Integer, nullable=False, default=0)
    peak = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


def record_depth(worker: str, pending: int) -> None:
    """Note what *worker* currently has waiting. Never raises: a diagnostic
    must not be able to break the work it is describing."""
    now = datetime.utcnow()
    hour = now.replace(minute=0, second=0, microsecond=0)
    try:
        row = QueueStat.query.filter_by(worker=worker, hour_start=hour).first()
        if row is None:
            row = QueueStat(worker=worker, hour_start=hour, peak=pending)
            db.session.add(row)
        row.pending = pending
        row.peak = max(row.peak or 0, pending)
        row.updated_at = now
        db.session.commit()
        if now.minute < 2 and pending == 0:
            QueueStat.query.filter(QueueStat.hour_start < now - _RETAIN).delete()
            db.session.commit()
    except Exception:
        db.session.rollback()


def snapshot() -> dict:
    """Work outstanding now, and the deepest it has been in the last day.

    "Now" counts only workers heard from recently: a process that has exited
    leaves its last row behind, and counting it would report work that nobody
    is going to do.
    """
    now = datetime.utcnow()
    try:
        current = (db.session.query(db.func.sum(QueueStat.pending))
                   .filter(QueueStat.updated_at >= now - timedelta(minutes=2))
                   .scalar()) or 0
        peak = (db.session.query(db.func.max(QueueStat.peak))
                .filter(QueueStat.hour_start >= now - timedelta(hours=24))
                .scalar()) or 0
    except Exception:
        return {"current": 0, "peak_24h": 0, "known": False}
    return {"current": int(current), "peak_24h": int(peak), "known": True}
