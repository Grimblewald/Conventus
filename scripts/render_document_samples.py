"""Render one sample of each financial document kind for visual review.

Design aid, not part of the app: it exercises the real renderer with realistic
data so layout changes to `app/latex/document.tex` can be eyeballed before they
reach a society's members.

    uv run python scripts/render_document_samples.py [OUTDIR]

Writes invoice.pdf / receipt.pdf / adjustment.pdf. Uses a throwaway database,
so it never touches instance data.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SECRET_KEY", "sample-render-only")

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "var/doc-samples")

_tmp = tempfile.mkdtemp()
from app.config import BaseConfig, DevelopmentConfig  # noqa: E402

BaseConfig.SQLALCHEMY_DATABASE_URI = f"sqlite:///{_tmp}/samples.db"
DevelopmentConfig.SQLALCHEMY_DATABASE_URI = f"sqlite:///{_tmp}/samples.db"

from app import create_app  # noqa: E402

SAMPLE = {
    "user_name": "Mr Yunfan Bai",
    "user_email": "yunfan.bai@example.edu.au",
    "recipient_address": "20 Cornwall Street\nWoolloongabba, QLD 4102\nAustralia",
    "recipient_abn": "",
    "conference_title": "Registration Fee (Student)",
    "conference_dates": "1–3 September 2026",
    "tier_name": "DDA Meeting 2026",
    "amount": "400.00",
    "currency_code": "AUD",
    "currency_symbol": "$",
    "transaction_id": "2025262063211-b15e489e",
    "registration_id": "42",
    "payment_date": "19 September 2026",
    "due_date": "3 October 2026",
    "site_name": "Australian Controlled Release Society",
}


def main() -> None:
    app = create_app()
    with app.app_context():
        from app.extensions import db
        from app.models import get_document_template, get_financial_identity
        from app.services.documents import render_document
        from app.services.invoice import _business_vars

        db.create_all()
        ident = get_financial_identity()
        ident.legal_name = "Australian Controlled Release Society Ltd"
        ident.abn = "17 602 379 475"
        ident.address = ("C/- Dr Timothy Barnes\nClinical and Health Sciences, "
                         "HB6-18\nCity West Campus\nUniversity of South "
                         "Australia\nAdelaide, SA 5000")
        ident.payment_instructions = ("Electronic transfer\nBSB 000-000\n"
                                      "Account 12345678\nRef: your invoice number")
        ident.signatory_name = "Tim Barnes"
        ident.signatory_role = "Director/Treasurer"
        for kind in ("invoice", "receipt", "adjustment"):
            get_document_template(kind)
        db.session.commit()

        OUT.mkdir(parents=True, exist_ok=True)
        for kind in ("invoice", "receipt", "adjustment"):
            for gst in (True, False):
                vars_ = dict(SAMPLE)
                # _business_vars supplies blank due_date/recipient_address for
                # the real send path to fill; restore the sample values after.
                vars_.update(_business_vars(40000, gst=gst))
                vars_["recipient_address"] = SAMPLE["recipient_address"]
                vars_["due_date"] = SAMPLE["due_date"]
                if kind == "invoice":
                    vars_["payment_link"] = ("https://example.org/pay/invoice/"
                                             + SAMPLE["transaction_id"])
                suffix = "gst" if gst else "nogst"
                path = OUT / f"{kind}-{suffix}.pdf"
                path.write_bytes(render_document(kind, vars_))
                print(f"wrote {path}")


if __name__ == "__main__":
    main()
