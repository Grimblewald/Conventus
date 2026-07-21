"""Issued-document regeneration store (plan §12, ATO 5-year retention).

Every real send that attaches a PDF (an auto receipt/adjustment, a manual
invoice, a manual-invoice receipt, a §7 pending retry) appends one row here
carrying enough to rebuild that exact PDF byte-identically on demand:

* `vars_json`     — a JSON snapshot of ALL resolved variables handed to the
                    renderer for this document;
* `template_json` — a JSON snapshot of the render-affecting DocumentTemplate
                    fields actually used (pdf_body, gst_registered,
                    business_number, payment_instructions), so regeneration
                    stays faithful even after the live template is edited;
* `content_hash`  — the template's content hash at issue time.

PDFs themselves stay transient; `services.documents.regenerate_document`
rebuilds them from these snapshots plus the pinned SOURCE_DATE_EPOCH. Rows are
append-only, mirroring the payment-event ledger.
"""
from __future__ import annotations

from datetime import datetime

from ..extensions import db


class IssuedDocument(db.Model):
    __tablename__ = "issued_documents"

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(20), default="", nullable=False)
    reference = db.Column(db.String(120), default="", index=True, nullable=False)
    recipient = db.Column(db.String(200), default="")
    amount = db.Column(db.Integer, nullable=True)              # cents
    vars_json = db.Column(db.Text, default="")
    template_json = db.Column(db.Text, default="")
    content_hash = db.Column(db.String(64), default="")
    issued_at = db.Column(db.DateTime, default=datetime.utcnow,
                          nullable=False, index=True)
