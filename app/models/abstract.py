"""Abstract submissions (1 optional figure each)."""
from __future__ import annotations

from datetime import datetime

from ..extensions import db

# Oral and poster are separate decisions: a conference programmes them
# differently and may choose to list only one of them publicly.
ACCEPTED_STATUSES = ("accepted_oral", "accepted_poster")
SPEAKER_STATUSES = ("plenary", "keynote", "invited", *ACCEPTED_STATUSES)
SPEAKER_STATUS_ORDER = {s: i for i, s in enumerate(SPEAKER_STATUSES)}
ALL_STATUSES = ("draft", "submitted", *ACCEPTED_STATUSES, "rejected",
                "revise", "plenary", "keynote", "invited")

STATUS_LABELS = {
    "accepted_oral": "Accepted (oral)",
    "accepted_poster": "Accepted (poster)",
}


def status_label(status: str | None) -> str:
    """How a status reads to people; the stored value is an identifier."""
    return STATUS_LABELS.get(status, (status or "").capitalize())


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
    # Authors and affiliations as validated structure (services.authors).
    # `authors` and `presenting_author_index` are written alongside it by
    # set_authors and kept for text search and older readers.
    author_data = db.Column(db.JSON, default=None)
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
    def reference(self) -> str:
        """The abstract's human-facing reference, e.g. ABS-000123.

        Derived from the id for the same reasons as Registration.reference:
        it exists from the first save and can never drift or collide.
        """
        return f"ABS-{self.id:06d}"

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
    def author_list(self):
        """The authors and their numbered affiliations (services.authors).

        From the stored structure; an abstract saved before there was one, or
        whose structure cannot be read, is read from its text, which is kept
        in step on every save.
        """
        from ..services.authors import from_data, parse

        if self.author_data:
            authors = from_data(self.author_data)
            if authors.authors:
                return authors
        return parse(self.authors, self.presenting_author_index)

    def set_authors(self, authors) -> None:
        """The one way to change an abstract's authors, so the structure and
        the text kept beside it cannot disagree."""
        from ..services.authors import to_data, to_legacy

        self.author_data = to_data(authors)
        self.authors = to_legacy(authors)
        self.presenting_author_index = authors.presenting

    @property
    def presenting_author(self) -> tuple[str, str]:
        authors = self.author_list
        a = authors.presenting_author
        if a is None:
            return ("", "")
        return (a.name, "; ".join(authors.affiliations_of(a)))

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
    def status_label(self) -> str:
        return status_label(self.status)

    @property
    def is_speaker(self) -> bool:
        return self.status in SPEAKER_STATUSES

    @property
    def speaker_sort_key(self) -> int:
        return SPEAKER_STATUS_ORDER.get(self.status, len(SPEAKER_STATUSES))
