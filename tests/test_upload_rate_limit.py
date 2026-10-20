"""Served uploads are not rate limited; everything else still is.

The default limit is counted per route, and one conference page asks for
every speaker portrait from the same uploads route — so a single visitor went
over it and saw broken images.

The suite runs with the limiter off, and Flask-Limiter only hooks requests
when it is enabled at app creation, so this runs a fresh app with the limiter
on in a subprocess rather than disturbing the shared one.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_PROBE = r"""
import json, sys
from pathlib import Path

tmp = Path(sys.argv[1])
from app.config import DevelopmentConfig

class Cfg(DevelopmentConfig):
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp}/app.db"
    UPLOAD_FOLDER = str(tmp / "uploads")
    SETUP_FLAG_PATH = str(tmp / ".setup-complete")
    RATELIMIT_ENABLED = True
    RATELIMIT_STORAGE_URI = "memory://"

Path(Cfg.SETUP_FLAG_PATH).touch()
from app import create_app
app = create_app(Cfg)
client = app.test_client()

out = {}
for folder in ("abstracts", "committee", "conferences", "sponsors", "site"):
    d = Path(Cfg.UPLOAD_FOLDER) / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    out[folder] = sorted({client.get(f"/uploads/{folder}/img.png").status_code
                          for _ in range(40)})
out["control"] = [client.get("/.well-known/security.txt").status_code
                  for _ in range(31)][-1]
print(json.dumps(out))
"""


def test_uploads_are_exempt_and_the_limiter_still_works(tmp_path):
    env = {**os.environ, "FLASK_ENV": "development",
           "SECRET_KEY": "test-key-do-not-use-in-production",
           "MAIL_BACKEND": "console"}
    proc = subprocess.run([sys.executable, "-c", _PROBE, str(tmp_path)],
                          cwd=ROOT, env=env, capture_output=True, text=True,
                          timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    result = json.loads(proc.stdout.strip().splitlines()[-1])

    for folder in ("abstracts", "committee", "conferences", "sponsors", "site"):
        assert result[folder] == [200], (folder, result[folder])
    # Control: the limiter really is on in that app.
    assert result["control"] == 429
