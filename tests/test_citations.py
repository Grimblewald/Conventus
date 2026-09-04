"""DOI normalisation and Crossref lookups.

An author types a DOI into a free-text box, so whatever a journal printed is
what arrives. None of it may reach the network as-is, and none of it may take
down the page they are trying to read.
"""
from __future__ import annotations

import json
from http.client import InvalidURL
from urllib.error import HTTPError, URLError

import pytest

from app.services import citations


@pytest.fixture(autouse=True)
def clean_cache(app, monkeypatch, tmp_path):
    """The cache is module state backed by a file, and both outlive a test.

    Pointed at a fresh path per test rather than cleared, because leaving it
    unset lets the module find the shared upload folder and reload whatever an
    earlier test persisted — a hit that looks exactly like the miss under test.
    """
    monkeypatch.setattr(citations, "_CACHE", {})
    monkeypatch.setattr(citations, "_CACHE_FILE", tmp_path / "cache.json")
    monkeypatch.setattr(citations, "_CACHE_DIRTY", False)
    with app.app_context():
        yield


def _responder(payload, captured):
    """Stand in for urlopen, recording the URL it was handed."""
    class _Resp:
        def read(self):
            return json.dumps(payload).encode("utf-8")

    def _urlopen(req, timeout=0):
        captured.append(req.full_url)
        return _Resp()

    return _urlopen


class TestNormalizeDoi:
    @pytest.mark.parametrize("raw", [
        "DOI: 10.1126/sciadv.ade5079",
        "doi:10.1126/sciadv.ade5079",
        "DOI 10.1126/sciadv.ade5079",
        "https://doi.org/10.1126/sciadv.ade5079",
        "doi: https://doi.org/10.1126/sciadv.ade5079",
        "  10.1126/sciadv.ade5079 ",
        # Every one of these was found in a submitted or abandoned abstract.
        "DOI (10.1126/sciadv.ade5079)",
        "DOI (10.1126/sciadv.ade5079).",
        "https://doi.org10.1126/sciadv.ade5079",
        "4.\tDoi: 10.1126/sciadv.ade5079",
        "[3] Zhao P, et al. Sci Adv (2023). 10.1126/sciadv.ade5079",
    ])
    def test_every_way_a_doi_gets_pasted_reduces_to_the_doi(self, raw):
        assert citations.normalize_doi(raw) == "10.1126/sciadv.ade5079"

    def test_it_survives_nothing_at_all(self):
        assert citations.normalize_doi("") == ""
        assert citations.normalize_doi(None) == ""

    @pytest.mark.parametrize("raw", [
        "0.1016/j.ejpb.2025.114637",     # a real typo, from a real abstract
        "see the paper for details",
        "",
    ])
    def test_what_holds_no_doi_is_not_invented_into_one(self, raw):
        """Tolerance is for how a DOI was written, not for whether there is one.

        Handed back unchanged rather than emptied, so whoever refuses it can
        show the author what was read.
        """
        assert not citations.is_doi(raw)
        assert citations.normalize_doi(raw) == raw.strip()

    def test_one_answer_to_what_a_doi_is(self):
        """is_doi and normalize_doi cannot disagree, being the same match.

        A second opinion held elsewhere in the codebase is the fault this
        pair replaced.
        """
        for raw in ["DOI: 10.1126/sciadv.ade5079", "not a reference", ""]:
            assert citations.is_doi(raw) == citations.normalize_doi(
                raw).startswith("10.")


class TestLookupNeverRaises:
    """The reported failure: a DOI copied with its label 500'd two pages."""

    def test_a_labelled_doi_is_looked_up_by_the_doi_alone(self, monkeypatch):
        captured: list[str] = []
        monkeypatch.setattr(citations, "urlopen", _responder(
            {"status": "ok", "message": {"DOI": "10.1126/sciadv.ade5079"}},
            captured))

        citations.fetch_metadata("DOI: 10.1126/sciadv.ade5079")

        assert captured == [
            "https://api.crossref.org/works/10.1126/sciadv.ade5079"]

    def test_the_url_it_builds_never_carries_a_raw_space(self, monkeypatch):
        """A space in the path is what the HTTP client refused to send.

        No longer escaped, because no space survives being read out: a DOI
        ends where the whitespace after it begins. The guarantee is about what
        reaches the network, not about which step removed it.
        """
        captured: list[str] = []
        monkeypatch.setattr(citations, "urlopen", _responder(
            {"status": "ok", "message": {}}, captured))

        citations.fetch_metadata("10.1126/sciadv trailing words")

        assert captured, "no request was made"
        assert " " not in captured[0]
        assert captured[0].endswith("/10.1126/sciadv")

    def test_what_survives_being_read_out_is_still_escaped(self, monkeypatch):
        """Whitespace is not the only character a URL cannot carry raw."""
        captured: list[str] = []
        monkeypatch.setattr(citations, "urlopen", _responder(
            {"status": "ok", "message": {}}, captured))

        citations.fetch_metadata("10.1126/sciadv#frag")

        assert captured, "no request was made"
        assert "#" not in captured[0]
        assert "%23" in captured[0]

    def test_a_url_the_client_refuses_is_an_absent_citation_not_a_500(
            self, monkeypatch):
        def _boom(req, timeout=0):
            raise InvalidURL("URL can't contain control characters.")

        monkeypatch.setattr(citations, "urlopen", _boom)

        assert citations.fetch_metadata("10.1126/whatever") is None


class TestWhatGetsRemembered:
    def test_a_doi_crossref_does_not_have_is_not_asked_for_twice(
            self, monkeypatch):
        calls = []

        def _missing(req, timeout=0):
            calls.append(req.full_url)
            raise HTTPError(req.full_url, 404, "Not Found", {}, None)

        monkeypatch.setattr(citations, "urlopen", _missing)

        assert citations.fetch_metadata("10.1126/nope") is None
        assert citations.fetch_metadata("10.1126/nope") is None
        assert len(calls) == 1

    def test_a_network_failure_does_not_poison_a_good_doi(self, monkeypatch):
        """A bad minute must not become a permanently uncitable reference."""
        def _down(req, timeout=0):
            raise URLError("temporary failure in name resolution")

        monkeypatch.setattr(citations, "urlopen", _down)
        assert citations.fetch_metadata("10.1038/nature12373") is None

        captured: list[str] = []
        monkeypatch.setattr(citations, "urlopen", _responder(
            {"status": "ok", "message": {"DOI": "10.1038/nature12373",
                                         "title": ["Nanoscale thermometry"]}},
            captured))

        meta = citations.fetch_metadata("10.1038/nature12373")
        assert meta and meta["title"] == "Nanoscale thermometry"

    def test_the_label_and_the_bare_doi_share_one_cache_entry(self, monkeypatch):
        calls = []
        monkeypatch.setattr(citations, "urlopen", _responder(
            {"status": "ok", "message": {"DOI": "10.1038/nature12373"}}, calls))

        citations.fetch_metadata("10.1038/nature12373")
        citations.fetch_metadata("doi: 10.1038/nature12373")
        citations.fetch_metadata("https://doi.org/10.1038/nature12373")

        assert len(calls) == 1
