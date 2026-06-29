"""ANZ Worldline Global Online Pay gateway.

Uses the DirectLink / Server-to-Server API.

Env vars:
  ANZ_PSPID       — Merchant ID
  ANZ_API_KEY_ID  — API user
  ANZ_SECRET_KEY  — SHA-IN passphrase for signature verification
  ANZ_TEST_MODE   — set to "1" for sandbox (ommits live payment)
"""
from __future__ import annotations

import hashlib
import logging
import urllib.request

from flask import url_for

from . import CheckoutResult, PaymentGateway, WebhookResult, register_gateway

log = logging.getLogger(__name__)

_WORLDLINE_LIVE = "https://secure.ogone.com/ncol/prod/orderdirect.asp"
_WORLDLINE_TEST = "https://secure.ogone.com/ncol/test/orderdirect.asp"


class ANZWorldlineGateway(PaymentGateway):
    def __init__(self, pspid: str = "", api_key_id: str = "",
                 secret_key: str = "", test_mode: bool = False):
        self.pspid = pspid
        self.api_key_id = api_key_id
        self.secret_key = secret_key.encode() if secret_key else b""
        self.test_mode = test_mode

    def create_checkout(self, registration, amount: int,
                        currency: str = "AUD") -> CheckoutResult:
        if not all([self.pspid, self.api_key_id, self.secret_key]):
            return CheckoutResult(
                error="Payment gateway not configured. "
                      "Set ANZ_PSPID, ANZ_API_KEY_ID, and ANZ_SECRET_KEY.")

        reg_id = registration.id
        conf = registration.conference
        order_id = f"r{reg_id}-{conf.slug[:20]}"

        params = {
            "PSPID": self.pspid,
            "USERID": self.api_key_id,
            "PSWD": self._api_password(),
            "ORDERID": order_id,
            "AMOUNT": str(amount),
            "CURRENCY": currency,
            "LANGUAGE": "en_AU",
            "OPERATION": "RES",
            "ACCEPTURL": url_for("public.home", _external=True),
            "DECLINEURL": url_for("member.pay_registration",
                                  reg_id=reg_id, _external=True),
            "EXCEPTIONURL": url_for("member.pay_registration",
                                    reg_id=reg_id, _external=True),
            "PARAMPLUS": f"registration_id={reg_id}",
        }

        try:
            resp = self._post(params)
            if resp.get("STATUS") in ("5", "9", "51"):
                return CheckoutResult(
                    redirect_url=resp.get("PAYID", ""),
                    payment_id=resp.get("PAYID", ""),
                )
            error_msg = resp.get("NCERROR", "Unknown error")
            return CheckoutResult(error=f"Payment error: {error_msg}")
        except Exception as exc:
            log.exception("Worldline checkout failed")
            return CheckoutResult(error=f"Payment service error: {exc}")

    def verify_webhook(self, request_data: dict,
                       headers: dict | None = None) -> WebhookResult:
        sha_sign = (request_data.get("SHASIGN") or "").upper()
        params = {k.upper(): v for k, v in request_data.items()
                  if k.upper() != "SHASIGN" and v}

        sorted_keys = sorted(params.keys())
        sign_str = ""
        for k in sorted_keys:
            val = str(params[k])
            sign_str += f"{k}={val}{self.secret_key.decode()}"

        computed = hashlib.sha256(sign_str.encode()).hexdigest().upper()

        if computed != sha_sign:
            return WebhookResult(error="Invalid signature")

        status = params.get("STATUS", "")
        reg_id_str = params.get("PARAMPLUS", "")
        reg_id = None
        if reg_id_str.startswith("registration_id="):
            try:
                reg_id = int(reg_id_str.split("=", 1)[1])
            except ValueError:
                pass

        if status in ("5", "9"):
            return WebhookResult(
                success=True,
                registration_id=reg_id,
                transaction_id=params.get("PAYID", ""),
            )
        return WebhookResult(
            success=False,
            registration_id=reg_id,
            transaction_id=params.get("PAYID", ""),
            error=f"Payment not successful (status {status})",
        )

    def _api_password(self) -> str:
        return hashlib.sha256(self.secret_key).hexdigest()

    def _post(self, params: dict) -> dict:
        endpoint = _WORLDLINE_TEST if self.test_mode else _WORLDLINE_LIVE
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(endpoint, data=data,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        result = {}
        for line in text.split("&"):
            if "=" in line:
                k, v = line.split("=", 1)
                result[k.strip().upper()] = v.strip()
        return result


def create_anz_gateway():
    import os
    return ANZWorldlineGateway(
        pspid=os.getenv("ANZ_PSPID", ""),
        api_key_id=os.getenv("ANZ_API_KEY_ID", ""),
        secret_key=os.getenv("ANZ_SECRET_KEY", ""),
        test_mode=os.getenv("ANZ_TEST_MODE", "0") == "1",
    )


register_gateway("anz_worldline", ANZWorldlineGateway)
