"""Synthetic labelled era comparisons; not a real-world accuracy estimate."""

from tests.factories import intent
from tests.test_musicbrainz_enrichment import Lookup, match
from tests.test_request_graph import playable_candidate
from tunetrail.domain.models import Candidate
from tunetrail.musicbrainz.enrichment import MusicBrainzEnrichment
from tunetrail.musicbrainz.models import Release
from tunetrail.recommendation.ranking import rank_personalized_candidates


async def test_labelled_era_comparison() -> None:
    """Compare baseline and cached enrichment against constructed release labels."""
    cases = [
        # Spotify year, MusicBrainz releases, labelled safe eligibility for 2008.
        (2008, [Release(id="r1", title="Fearless", date="2008")], True),
        (None, [Release(id="r1", title="Fearless", date="2008")], True),
        (None, [Release(id="r1", title="Fearless", date="2010")], False),
        (2010, [Release(id="r1", title="Fearless", date="2008")], False),
        (
            None,
            [
                Release(id="r1", title="Fearless", date="2008"),
                Release(id="r2", title="Fearless", date="2010"),
            ],
            False,
        ),
    ]
    parsed = intent(artists=["Taylor Swift"], year_start=2008, year_end=2008)
    baseline_correct = enriched_correct = baseline_accepted = enriched_accepted = 0
    for spotify_year, releases, expected in cases:
        candidate = playable_candidate(album="Fearless", year=spotify_year)
        enriched = await MusicBrainzEnrichment(Lookup(match(*releases))).enrich([candidate])

        def accepted(items: list[Candidate]) -> bool:
            return bool(
                rank_personalized_candidates(
                    items, parsed, profile=None, feedback=[], generated_uris=[]
                )
            )

        baseline = accepted([candidate])
        improved = accepted(list(enriched))
        baseline_correct += baseline == expected
        enriched_correct += improved == expected
        baseline_accepted += baseline
        enriched_accepted += improved
    assert (baseline_correct, baseline_accepted) == (4, 1)
    assert (enriched_correct, enriched_accepted) == (5, 2)
