"""Gunicorn entry point: `gunicorn wsgi:app`.

We load `.env` here before anything else imports `app.config`, so secrets
are available at config-evaluation time.

ProxyFix is wired here (and *only* here) because it has to know how many
forwarded hops are trusted, and that's a deployment fact, not an app fact.
The defaults assume one trusted proxy in front of gunicorn (cloudflared,
nginx, or a Caddyfile reverse proxy); override with NUM_PROXIES if you
chain more.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parent / ".env", override=False)

from app import create_app  # noqa: E402  (after dotenv on purpose)
from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402


app = create_app()

# Trust X-Forwarded-Proto/Host/For from N upstream proxies. Without this,
# Talisman+ProductionConfig see scheme=http on every request behind
# cloudflared, force a redirect to https, the edge re-proxies that as
# http, and the browser sits in a redirect loop staring at a blank page.
_hops = int(os.environ.get("NUM_PROXIES", "1"))
app.wsgi_app = ProxyFix(
    app.wsgi_app,
    x_for=_hops, x_proto=_hops, x_host=_hops, x_prefix=_hops,
)


if __name__ == "__main__":
    # `python wsgi.py` is local dev only. We force FLASK_ENV=development
    # here so the DevelopmentConfig (insecure cookies, no force-HTTPS) is
    # picked even if the operator forgot to export it — otherwise the
    # default ProductionConfig forces HTTPS over a plain-HTTP dev server
    # and you get a redirect loop on first request.
    os.environ.setdefault("FLASK_ENV", "development")
    app = create_app()  # rebuild with dev config
    app.wsgi_app = ProxyFix(
        app.wsgi_app, x_for=_hops, x_proto=_hops, x_host=_hops, x_prefix=_hops,
    )
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", 5005)),
        debug=app.debug,
    )
