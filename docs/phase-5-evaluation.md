# Phase 5 offline evaluation

## Run and interpret

Run `uv run pytest -q -s tests/test_phase5_evaluation.py` for a small, fixed,
synthetic discovery-to-preview regression and its aggregate counts. Run the full
Python suite with `uv run pytest -q` and the interface suite with `npm test` in
`frontend/`. No provider access or existing user data is needed: sources, Spotify
search, chat interpretation and credentials in these tests are synthetic or mocked,
and test databases are temporary.

One fixture run produced:

| Measure | Result | Denominator and meaning |
| --- | --- | --- |
| Discovery coverage | 4/5 | Four candidate recordings from five requested synthetic recording seeds. |
| Discovery source failures | 1/5 | One deliberately failing seed request out of five attempted. |
| Known ambiguous searches | 1/4 | One deliberately ambiguous result set out of four ranked candidate searches. The implementation reports ambiguity and no match together. |
| Search failures | 1/4 | One deliberately timed-out search out of four attempts. |
| Resolution | 1/4 | One unique accepted match out of four ranked candidates; the others are ambiguous, a different edition, and a timeout. |
| Diversity | 4/4 | Distinct primary artists among four ranked candidates, not a musical-diversity score. |
| Preview latency | 0.637 ms | One illustrative in-process mock run, not provider or browser latency; reruns will vary. |

The test checks the exact fixture outcomes, rank order, positive and negative
preference signals, score explanations, unknown features, match evidence and
outage reports. Other existing Phase 4S/4T tests cover bounded discovery,
enrichment, feature scales, duplicates, unsupported constraints and ambiguity.
An additional case checks that an unverifiable year blocks approval even for a
matched track and that conflicting candidates sharing a recording ID are excluded.
These hand-authored cases are **not independently reviewed**, representative of
real provider catalogs, or evidence of real-world coverage, matching accuracy or
recommendation quality. Independent review remains outstanding. No statistical
inferences should be drawn from these small, deliberately selected denominators.

## Safety and flow coverage

The Python API suite uses mocked providers to exercise chat or survey feedback,
discovery, ranking, preview, cancellation, explicit approval and failures. It
checks play and queue proposals separately, including single-use approval and no
mutation before approval. A temporary SQLite dump checks that a synthetic raw chat
sentinel is not stored. Existing tests cover incompatible database preservation,
local origin/session protection, Spotify objects rejected as independent seeds,
and specific MusicBrainz and ReccoBeats outbound requests. These checks do not
prove the provenance of a user-supplied UUID or of arbitrary text; the owner must
obtain seeds independently and must not translate Spotify-derived data.

The React interaction suite mocks the API. It covers chat, discovery, feedback,
survey, preview, cancellation, approval, ambiguous chat and unavailable search.
Additionally, `tests/test_phase5_browser.py` drives the built React interface in
headless Firefox against the actual local API and a temporary database. The chat
parser and discovery source are synthetic; all Spotify HTTP requests use a mock
transport that rejects unexpected requests. It checks chat preference, discovery,
ranking explanation, cancellation, single-use approval, and a simulated search
outage. Player mutations are mocked. This is a real-browser **offline regression**,
not live-provider or real-device acceptance; it does not cover every workflow.

To run it, install Firefox and geckodriver, install project dev dependencies with
`uv sync --extra dev`, build the frontend with `npm ci && npm run build` in `frontend/`, and run
`TUNETRAIL_BROWSER_TEST=1 uv run pytest -q tests/test_phase5_browser.py` from the
repository root. It is skipped by default so ordinary test runs do not require a
browser; an opted-in run fails if its prerequisites are missing. The browser is
configured to proxy non-loopback HTTP(S) traffic to a closed local port. The test
uses temporary synthetic credentials and never accesses the owner's profile.

Legacy modules and fixtures are not evidence for the redesigned flow; legacy
generic MusicBrainz lookup and Spotify resolver must not be wired into independent
discovery.

## External evidence and authorization

ReccoBeats is optional and provisional. Independent title/artist lookup is based
on the documented contract, but real catalog coverage, uniqueness, matching
reliability, rate limits and current terms remain unverified. Its foundational
metadata is Spotify-derived; the owner's hobby-project tradeoff is not policy
clearance. Features retain their source-specific scales: missing values are unknown,
and only supported unit-interval features contribute to ranking. See
`docs/source-policy-review.md` and `docs/phase-4s-discovery.md`.

No live read-only evaluation has been authorized or run. Before one, obtain a
separate owner authorization with agreed providers, data, requests and scope; record
the results and limitations separately. Playback acceptance likewise needs separate
authorization, Spotify Premium, an active device, and explicit approval for every
playback or queue mutation. Approval or a playback request is not evidence of
listening. No policy clearance is claimed.
