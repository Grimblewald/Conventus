"""A reference's stored text never reaches a URL as it was typed.

The DOI box is free text. What is saved may carry the label a journal printed
around it, and three places put that value straight into a link — the abstract
PDF's \\href, the public abstract page, and the author's own preview. A stored
"DOI: 10.1126/x" then links to https://doi.org/DOI: 10.1126/x, which is broken
and, in the PDF, puts a space inside a LaTeX argument.

Asserted on the model, because reading a reference is where the guarantee has
to hold: every consumer takes the list from there.
"""
from __future__ import annotations

import pytest

from app.models import Abstract

BARE = "10.1126/sciadv.ade5079"


class TestStoredReferencesAreResolvedOnRead:
    @pytest.mark.parametrize("stored", [
        "DOI: 10.1126/sciadv.ade5079",
        "DOI (10.1126/sciadv.ade5079)",
        "https://doi.org/10.1126/sciadv.ade5079",
        "https://doi.org10.1126/sciadv.ade5079",
        BARE,
    ])
    def test_however_it_was_saved_it_reads_back_as_the_doi(self, stored):
        a = Abstract(references=[{"key": 1, "doi": stored}])
        assert a.resolved_references == [{"key": 1, "doi": BARE}]

    def test_the_key_and_any_other_field_survive(self):
        a = Abstract(references=[{"key": 3, "doi": f"DOI: {BARE}", "note": "x"}])
        assert a.resolved_references == [
            {"key": 3, "doi": BARE, "note": "x"}]

    def test_no_references_is_an_empty_list_not_a_crash(self):
        assert Abstract(references=None).resolved_references == []
        assert Abstract(references=[]).resolved_references == []

    def test_a_reference_holding_no_doi_is_left_alone(self):
        """Not silently emptied: whoever renders it should show what is there
        rather than nothing at all."""
        a = Abstract(references=[{"key": 1, "doi": "see the paper"}])
        assert a.resolved_references == [{"key": 1, "doi": "see the paper"}]

    def test_a_missing_doi_field_does_not_raise(self):
        a = Abstract(references=[{"key": 1}])
        assert a.resolved_references == [{"key": 1, "doi": ""}]


class TestTheLinkThatGetsBuilt:
    @pytest.mark.parametrize("stored", [
        "DOI: 10.1126/sciadv.ade5079",
        "DOI (10.1126/sciadv.ade5079)",
    ])
    def test_a_doi_link_carries_no_space_or_bracket(self, stored):
        """What the PDF and both pages interpolate after https://doi.org/."""
        a = Abstract(references=[{"key": 1, "doi": stored}])
        url = "https://doi.org/" + a.resolved_references[0]["doi"]
        assert url == f"https://doi.org/{BARE}"
        for bad in " ()<>":
            assert bad not in url
