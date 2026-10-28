# Phase 4S independent discovery: offline progress

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

The new flow holds only normalized results in memory, performs no database writes,
and does not retain raw provider payloads. MusicBrainz's existing rate limiter uses
a local timing/lock file. No Spotify, ReccoBeats, LLM, playback or queue API is called.
ReccoBeats feature values are unavailable, not zero: no endpoint, match, cache, scale
or feature data is fabricated. Its independently usable lookup and feature contract
remain unconfirmed; see `docs/source-policy-review.md` for sources and policy risks.

Mock HTTP tests inspect both request paths and parameters, and cover bounded seeds,
identity mismatch, duplicates, insufficient results, malformed browse pages, source
errors, timeouts and rejection of Spotify candidate objects. They do not prove the
origin of arbitrary caller input, live coverage or provider-policy clearance. No live
provider tests were performed. The 4S gate remains open until independent matching
and optional feature enrichment can be implemented and tested against an established
contract, or the owner approves a revised scope.
