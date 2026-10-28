# Phase 4S independent discovery and enrichment

`IndependentDiscovery` accepts at most five independently supplied MusicBrainz
artist or recording UUIDs, and returns at most 20 minimal MusicBrainz recordings.
An artist seed browses one bounded first page of directly linked recordings; a
recording seed retrieves that recording. Results must contain valid IDs and artist
credits and match the requested identity. Duplicate recordings are suppressed.
One request per unique seed is made until the result limit is met; timeouts and
MusicBrainz errors preserve any other results. Insufficient results are flagged so
the caller can request more seeds. This is catalog expansion, not a similarity or
recommendation algorithm; one first page may have limited coverage.

Names alone are deliberately not resolved: a caller must ask the owner to select an
independent MusicBrainz identity, including for namesakes. A search/selection UI is
not implemented. No inference from Spotify IDs, URIs, metadata, or resolver output
is allowed. The seed type rejects generic `Candidate` objects and only transmits UUIDs;
however, the system cannot prove that a caller obtained a UUID independently.
Callers must not translate Spotify-derived data into a seed. The retained generic
MusicBrainz lookup accepts arbitrary data and remains unsafe for this boundary.

Optional `ReccoBeatsEnricher` searches with only the independent MusicBrainz title and
first credited artist, then requires a complete search page of at most 10 results,
exact normalized title and ordered artist credits, a duration within two seconds,
and exactly one matching result. No duration, short search terms (under three
characters), incomplete results, ambiguous editions or malformed evidence mean no
match. This conservative rule may miss valid tracks, and even a unique match is not
proof of identity. A feature request uses only the selected ReccoBeats UUID. Spotify
links and ISRCs returned by ReccoBeats are ignored, and no ReccoBeats response data
is ever sent to MusicBrainz. Generic Spotify candidates and obvious Spotify links are
rejected at the new enrichment boundary. The typed candidate is not proof of origin:
callers must never construct it from Spotify data or use resolver output.

Available feature values are labelled `reccobeats` and retained in the provider's
documented scales, including BPM and dB. Missing, invalid, non-finite and unknown key
values are omitted rather than replaced by zero. Errors, 429 responses, invalid
payloads and timeouts leave the original independent candidate available; a 429 pauses
further requests by that adapter instance for the specified Retry-After duration (or
a conservative default). Calls are serialized and paced per instance; limits across
multiple independent instances or processes are not coordinated. Redirects are not
followed. A bounded in-memory LRU cache holds only successful normalized features,
at most 128 entries and at most one hour, by default 15 minutes. No raw provider
payloads or feature caches are written to disk. MusicBrainz's existing rate limiter
uses a local timing/lock file. No Spotify, LLM, playback or queue API is called.

Mock HTTP tests inspect both providers' request paths and parameters, and cover bounded
seeds, identity mismatches, duplicates, insufficient results, ambiguity, malformed
pages, feature availability, cache eviction and expiry, rate limits, failures,
timeouts and rejection of Spotify candidate objects. They do not prove the origin of
arbitrary caller input, live coverage, real-world matching accuracy, or policy clearance.
No live provider tests were performed. ReccoBeats states that its foundational track
metadata comes from Spotify; the owner accepted that tradeoff for this hobby project,
not as policy clearance. See `docs/source-policy-review.md`.
