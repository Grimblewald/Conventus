"""uv run python -m app <command>

Commands:
    launch             start gunicorn + cloudflared
    update             backup, pull, migrate, restart
    revert             restore last backup
    backup             manual backup
    register-service   install systemd units
    uninstall-service  remove systemd units
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

COMMANDS: dict[str, tuple[str, ...]] = {
    "launch":             ("scripts/launch_cloudflared.sh",),
    "update":             ("scripts/update.sh",),
    "revert":             ("scripts/update.sh", "--revert"),
    "backup":             ("scripts/backup.py",),
    "register-service":   ("scripts/register-service.sh",),
    "uninstall-service":  ("scripts/remove-service.sh",),
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__.strip())
        sys.exit(0)

    name = sys.argv[1]
    if name not in COMMANDS:
        print(f"Unknown command: {name}", file=sys.stderr)
        print(__doc__.strip(), file=sys.stderr)
        sys.exit(1)

    script, *args = COMMANDS[name]
    path = ROOT / script

    if not path.exists():
        print(f"✗ not found: {path}", file=sys.stderr)
        sys.exit(1)

    path.chmod(0o755)

    if path.suffix == ".py":
        os.execv(sys.executable, [sys.executable, str(path), *args])
    else:
        os.execvp(str(path), [str(path), *args])


if __name__ == "__main__":
    main()
