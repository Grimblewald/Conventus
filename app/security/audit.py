"""Audit log helper. Single entry point: `record(action, ...)`."""
from __future__ import annotations

import json
import logging
from typing import Any

from flask import has_request_context, request
from flask_login import current_user

from ..extensions import db
from ..models.audit import AuditLog


log = logging.getLogger(__name__)


def record(
    action: str,
    *,
    target_kind: str | None = None,
    target_id: str | int | None = None,
    summary: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Best-effort audit row. Never raises."""
    try:
        actor_id = None
        actor_email = None
        actor_role = None
        if has_request_context() and current_user.is_authenticated:
            actor_id = current_user.id
            actor_email = current_user.email
            actor_role = current_user.role_name

        ip = request.remote_addr if has_request_context() else None
        row = AuditLog(
            actor_id=actor_id,
            actor_email=actor_email,
            actor_role=actor_role,
            action=action,
            target_kind=target_kind,
            target_id=str(target_id) if target_id is not None else None,
            summary=summary,
            metadata_json=json.dumps(metadata) if metadata else None,
            ip=ip,
        )
        db.session.add(row)
        db.session.commit()
    except Exception:  # pragma: no cover  — never crash a request because of logging
        log.exception("Failed to record audit row for action %r", action)
        try:
            db.session.rollback()
        except Exception:
            pass
