"""Over-sized images, and telling somebody when the server breaks.

A member uploaded a 79-megapixel figure. Pillow raised DecompressionBombError,
which is not an OSError and so slipped past the handler written to catch it;
the submission became a 500 whose page claimed an administrator had been
notified, which nothing in the code did.
"""
from __future__ import annotations

import io

import pytest
from PIL import Image

from app.services.uploads import (MAX_IMAGE_PIXELS, MAX_MEGAPIXELS,
                                  UploadError, save_image)


def _png_bytes(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 30, 30)).save(buf, "PNG")
    buf.seek(0)
    return buf


def _upload(w, h, name="fig.png"):
    from werkzeug.datastructures import FileStorage
    return FileStorage(stream=_png_bytes(w, h), filename=name,
                       content_type="image/png")


class TestOversizedImages:
    def test_beyond_the_hard_limit_is_a_friendly_error(self, app, tmp_path,
                                                       monkeypatch):
        """Past twice MAX_IMAGE_PIXELS, Pillow raises inside Image.open —
        the case that used to reach the user as a 500."""
        # Build a small file, then lower the ceiling so opening it trips the
        # bomb guard: generating a real 79-megapixel PNG in a test is absurd.
        from PIL import Image as PILImage
        monkeypatch.setattr(PILImage, "MAX_IMAGE_PIXELS", 100)
        with app.app_context():
            with pytest.raises(UploadError) as ei:
                save_image(_upload(400, 400), upload_folder=str(tmp_path),
                           max_bytes=10_000_000)
        msg = str(ei.value)
        assert "megapixels" in msg
        # It must not send them off compressing the file, which changes the
        # byte count and not the pixel count.
        assert "not its file size" in msg

    def test_between_the_limits_is_also_friendly(self, app, tmp_path,
                                                 monkeypatch):
        """Pillow only warns here, so our own dimension check is what fires."""
        from PIL import Image as PILImage
        monkeypatch.setattr(PILImage, "MAX_IMAGE_PIXELS", 10_000_000)
        monkeypatch.setattr("app.services.uploads.MAX_IMAGE_PIXELS", 1000)
        with app.app_context():
            with pytest.raises(UploadError) as ei:
                save_image(_upload(400, 400), upload_folder=str(tmp_path),
                           max_bytes=10_000_000)
        assert "megapixels" in str(ei.value)

    def test_an_ordinary_image_still_uploads(self, app, tmp_path):
        with app.app_context():
            name = save_image(_upload(800, 600), upload_folder=str(tmp_path),
                              max_bytes=10_000_000)
        assert name

    def test_the_form_tells_the_browser_the_limit(self, seeded, member_client,
                                                  app):
        """So the check can run before the upload, not after it."""
        from datetime import date
        from app.extensions import db
        from app.models import Conference
        with app.app_context():
            c = Conference(slug="upload-conf", title="Upload Conference",
                           start_date=date(2027, 7, 1), end_date=date(2027, 7, 3))
            db.session.add(c)
            db.session.commit()
        resp = member_client.get("/conferences/upload-conf/abstract")
        assert resp.status_code == 200
        assert b'data-max-megapixels="' + str(MAX_MEGAPIXELS).encode() in resp.data
        assert b"image-dimensions.js" in resp.data


class TestErrorReports:
    def test_an_unhandled_error_emails_the_admins(self, seeded, app, monkeypatch):
        sent = []
        monkeypatch.setattr("app.services.mail.send_mail",
                            lambda **kw: sent.append(kw) or True)
        from app.services import error_reports
        monkeypatch.setattr(error_reports, "_last_sent", {})

        with app.app_context():
            try:
                raise ValueError("something broke")
            except ValueError as e:
                assert error_reports.report_exception(e) is True

        assert len(sent) == 1
        body = sent[0]["body"]
        assert "ValueError: something broke" in body
        assert "Time (UTC)" in body
        assert "Traceback" in body

    def test_the_report_carries_recent_log_lines(self, seeded, app, monkeypatch):
        import logging
        sent = []
        monkeypatch.setattr("app.services.mail.send_mail",
                            lambda **kw: sent.append(kw) or True)
        from app.services import error_reports
        monkeypatch.setattr(error_reports, "_last_sent", {})
        error_reports.install(app)

        logging.getLogger("test.marker").error("a distinctive log line")
        with app.app_context():
            try:
                raise RuntimeError("boom")
            except RuntimeError as e:
                error_reports.report_exception(e)
        assert "a distinctive log line" in sent[0]["body"]

    def test_a_repeated_failure_is_not_sent_twice(self, seeded, app, monkeypatch):
        """A crash in a hot path must not bury the inbox."""
        sent = []
        monkeypatch.setattr("app.services.mail.send_mail",
                            lambda **kw: sent.append(kw) or True)
        from app.services import error_reports
        monkeypatch.setattr(error_reports, "_last_sent", {})

        with app.app_context():
            for _ in range(5):
                try:
                    raise KeyError("same place every time")
                except KeyError as e:
                    error_reports.report_exception(e)
        assert len(sent) == 1

    def test_reporting_never_raises(self, seeded, app, monkeypatch):
        """It runs while the user already has one error; it must not add another."""
        def explode(**kw):
            raise RuntimeError("mail is down")
        monkeypatch.setattr("app.services.mail.send_mail", explode)
        from app.services import error_reports
        monkeypatch.setattr(error_reports, "_last_sent", {})

        with app.app_context():
            try:
                raise ValueError("original problem")
            except ValueError as e:
                assert error_reports.report_exception(e) is False

    def test_the_page_only_claims_notification_when_true(self, seeded, app):
        """The old page said an admin had been notified unconditionally."""
        import re

        from flask import render_template
        with app.test_request_context("/"):
            notified = render_template("errors/500.html", notified=True)
            silent = render_template("errors/500.html", notified=False)
        flatten = lambda h: re.sub(r"\s+", " ", h)
        assert "administrator has been notified" in flatten(notified)
        assert "has been notified" not in flatten(silent)
        assert "Contact us" in silent
