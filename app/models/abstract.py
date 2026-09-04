"""Abstract submissions (1 optional figure each)."""
from __future__ import annotations

from datetime import datetime

from ..extensions import db

SPEAKER_STATUSES = ("plenary", "keynote", "invited", "accepted")
SPEAKER_STATUS_ORDER = {s: i for i, s in enumerate(SPEAKER_STATUSES)}
ALL_STATUSES = ("draft", "submitted", "accepted", "rejected", "revise",
                "plenary", "keynote", "invited")

# Statuses an author may still change. Everything else has been decided, and a
# decision must not be edited out from under the person who made it.
EDITABLE_STATUSES = ("draft", "submitted", "revise")


class Abstract(db.Model):
    __tablename__ = "abstracts"

    id = db.Column(db.Integer, primary_key=True)
    # Nullable: admin-entered abstracts (invited/plenary speakers) have no
    # author account — the `authors` text field carries attribution.
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"),
                        nullable=True, index=True)
    conference_id = db.Column(db.Integer, db.ForeignKey("conferences.id"),
                              nullable=False, index=True)
    registration_id = db.Column(db.Integer, db.ForeignKey("registrations.id"),
                                nullable=True, index=True)

    title = db.Column(db.String(400), nullable=False)
    authors = db.Column(db.Text, nullable=False)
    body = db.Column(db.Text, nullable=False)
    track = db.Column(db.String(120), default="")
    presentation_type = db.Column(db.String(40), default="Either")
    keywords = db.Column(db.String(300), default="")
    coi = db.Column(db.Text, default="")
    custom_data = db.Column(db.JSON, default=None)
    presenting_author_index = db.Column(db.Integer, default=0)
    references = db.Column(db.JSON, default=None)

    figure_filename = db.Column(db.String(240))
    profile_picture_filename = db.Column(db.String(240))
    website_url = db.Column(db.String(300), default="")
    # Speaker biography — optional, set only from the admin abstract editor
    # (`abs.edit`); the member submission form never touches it. Rendered
    # wherever it is non-empty, so leaving it blank is how you hide it.
    speaker_bio = db.Column(db.Text, default="")
    status = db.Column(db.String(40), default="submitted", nullable=False)
    reviewer_notes = db.Column(db.Text, default="")
    decided_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    decided_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)
    deleted_at = db.Column(db.DateTime, nullable=True, index=True)

    conference = db.relationship("Conference")
    author = db.relationship("User", foreign_keys=[user_id], backref="abstracts")
    decided_by = db.relationship("User", foreign_keys=[decided_by_id])
    registration = db.relationship("Registration", foreign_keys=[registration_id])
    reviews = db.relationship("ReviewAssignment", lazy="selectin",
                              back_populates="abstract")

    @property
    def review_scores(self) -> list[int]:
        """Submitted review scores, excluding pending/draft reviews."""
        return [r.score for r in self.reviews
                if r.status == "completed" and r.score is not None]

    @property
    def mean_score(self) -> float | None:
        scores = self.review_scores
        if not scores:
            return None
        return round(sum(scores) / len(scores), 1)

    @property
    def recommendation_tally(self) -> dict[str, int]:
        """Count of completed review recommendations."""
        from collections import Counter
        recs = [r.recommendation for r in self.reviews
                if r.status == "completed" and r.recommendation]
        return dict(Counter(recs))

    @property
    def resolved_references(self) -> list[dict]:
        """Stored references, each DOI reduced to the DOI itself.

        Read through the parser rather than trusted as stored. The box it came
        from is free text, so a saved reference may still carry the label its
        journal printed around the DOI, and every consumer of this list puts
        the value straight into a URL.
        """
        from ..services.citations import normalize_doi

        return [{**ref, "doi": normalize_doi(ref.get("doi", ""))}
                for ref in (self.references or [])]

    @staticmethod
    def clean_website(raw: str | None) -> str:
        """Normalize an optional presenter website URL.

        Returns "" for blank input, prefixes https:// when no scheme is
        given, and raises ValueError for input that can't be a URL.
        """
        url = (raw or "").strip()
        if not url:
            return ""
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        if " " in url or "." not in url or len(url) > 300:
            raise ValueError("Website must be a valid URL (max 300 characters).")
        return url

    @property
    def presenting_author(self) -> tuple[str, str]:
        if not self.authors or not self.authors.strip():
            return ("", "")
        lines = self.authors.strip().split("\n")
        idx = max(0, min(self.presenting_author_index or 0, len(lines) - 1))
        parts = lines[idx].split("|")
        name = parts[0].strip() if parts else ""
        affil = parts[2].strip() if len(parts) > 2 else ""
        return (name, affil)

    @property
    def bio_paragraphs(self) -> list[str]:
        """Speaker bio split into paragraphs for rendering. Empty when unset,
        which is also the "don't show a bio" signal at every render site."""
        raw = (self.speaker_bio or "").strip()
        if not raw:
            return []
        return [p.strip() for p in raw.replace("\r\n", "\n").split("\n\n")
                if p.strip()]

    @property
    def is_editable(self) -> bool:
        """Whether the author may still change this abstract.

        Authors spot their own errors after sending, so the deadline is the
        cut-off rather than the act of submitting. A decision closes it: an
        accepted abstract that could still be rewritten is no longer the one
        that was accepted.
        """
        conf = self.conference
        return (self.status in EDITABLE_STATUSES
                and self.deleted_at is None
                and conf is not None
                and not conf.is_draft
                and conf.accepts_abstracts)

    @property
    def is_speaker(self) -> bool:
        return self.status in SPEAKER_STATUSES

    @property
    def speaker_sort_key(self) -> int:
        return SPEAKER_STATUS_ORDER.get(self.status, len(SPEAKER_STATUSES))
