"""What the public uploads routes will and will not hand out.

The abstract export bundles, their grants and the compiled booklets are cached
in dot-folders under the uploads tree. They are meant to reach only the
signed-in person they were built for, so the public routes must refuse them.
"""
from __future__ import annotations

from pathlib import Path

import pytest

FOLDERS = ("abstracts", "committee", "conferences", "sponsors", "site")


@pytest.fixture
def uploads(app):
    root = Path(app.config["UPLOAD_FOLDER"])
    for folder in FOLDERS:
        (root / folder).mkdir(parents=True, exist_ok=True)
        (root / folder / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        hidden = root / folder / ".export-cache"
        hidden.mkdir(exist_ok=True)
        (hidden / "grant-abc.json").write_text('{"key": "secret"}')
        (root / folder / ".hidden.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return root


@pytest.mark.parametrize("folder", FOLDERS)
def test_uploads_are_served(client, uploads, folder):
    assert client.get(f"/uploads/{folder}/img.png").status_code == 200


@pytest.mark.parametrize("folder", FOLDERS)
@pytest.mark.parametrize("path", [".export-cache/grant-abc.json", ".hidden.png"])
def test_hidden_entries_are_not(client, uploads, folder, path):
    assert client.get(f"/uploads/{folder}/{path}").status_code == 404
