# Phase 4T: offline ranking and playback previews

`rank_candidates` consumes independent discovery results and explicit preferences;
it does not read Spotify history or legacy preference data. Exact recording preferences
and name-based artist preferences contribute their strength. ReccoBeats unit-scale
features contribute `strength × (value - 0.5)` when available; unsupported, missing
or invalid features are not zero-filled. Requested artist and title terms are soft
signals. Each applied score contribution is retained; a candidate with no signals
is not represented as having an inferred preference. Ties use the MusicBrainz UUID.
The primary-artist diversity penalty is 0.5 per earlier preview item, capped at 1;
repetition is assessed only within the current preview, not listening history.

The independent candidate currently lacks artist IDs, genre, year and explicit-content
classification. Artist-ID and genre preferences, unsupported features, year ranges,
explicit-content restrictions, total duration, non-default familiarity, mood/activity
and clarification requests are reported as unsupported. A preview with any reported
unsupported item cannot produce an approvable action, including unsupported preference
types; this is deliberately conservative. A neutral preference has no score effect.
The ranker selects up to the requested queue size before resolution; it does not refill
from lower-ranked candidates after resolution failures.

`build_preview` searches Spotify only after ranking. The Spotify adapter requests market
context and accepts only explicitly playable and unrestricted tracks from a complete
search page. Resolution requires exactly one result with the same normalized title and
ordered artists and duration within two seconds. Missing duration, incomplete pages,
ambiguous matches and outages yield fewer results and an issue. This is conservative,
not proof of identity or actual playback availability. Spotify metadata is kept in a
separate match object and must never be passed to MusicBrainz or ReccoBeats. The legacy
resolver is not part of this flow and remains unsafe to wire into independent discovery.

Previews do not mutate playback. A preview may register a short-lived proposal with the
same `PlayerApprovals` instance as the Spotify client. The caller must display the
matches, issues and exact action, and call `approve` only after explicit owner consent;
there is no user interface or API in this phase. All client player mutations, including
legacy pause, skip and transfer operations, require a confirmed, unexpired, single-use
proposal matching the HTTP method, path, parameters, body, ordered URIs and device.
Approval is consumed before the request, including on failure; cancellation removes it.
Play-now supports the matched list; appending multiple tracks requires a separate
single-track preview and approval per Spotify queue mutation. Replace-queue and refine
are unsupported. No playback request, approval or recommendation is recorded as a
listen or preference. Proposal and approval state is in memory only.

Failed token refresh preserves existing credentials; explicit logout still removes them.
Successful OAuth token exchange or refresh retains the existing credential-store
behavior. No new persistent data, raw responses or full playlists are stored by 4T.

The gate is based on synthetic and mocked tests only. No live Spotify, MusicBrainz,
ReccoBeats or playback acceptance test was performed. Real-world match coverage,
provider policies and actual playback behavior remain unverified; live read-only or
playback acceptance needs separate authorization. In-process approval is a backend
boundary, not a defense against arbitrary code running in the same Python process;
future API/UI work must protect endpoints and ensure approvals reflect owner intent.
