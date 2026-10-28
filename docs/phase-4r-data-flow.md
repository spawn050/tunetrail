# Phase 4R data-flow inventory

This inventory records the removed legacy runtime and the offline 4R
foundation. The legacy TUI, entry point, graph, Spotify-derived profile/retrieval/
ranking services and Spotify-candidate MusicBrainz enrichment were removed from
the working tree. There is currently no runnable user-facing runtime. Lower-level
provider code remains and must be audited before reuse. The legacy profile, feedback,
playlist and cache methods and the Last.fm client have since been removed. The
explicit preference store rejects incompatible databases without modification.

## Historical legacy runtime paths

- The former `app.py` wired `SpotifyProfileService` into the TUI. Its refresh read Spotify
  top artists, top tracks, recent tracks and tracks from selected playlists, then
  persists a compact `profile_summary` and playlist references.
- The former `tui/app.py` could select playlists, record feedback on the Spotify current track,
  export legacy profile data and reset legacy personalization. The feedback table
  stores Spotify URIs. The existing reset also deletes session and source-cache
  records; it is not the isolated reset for the new profile.
- `graph/request_graph.py` reads the legacy summary, Spotify-URI feedback and
  generated-session URIs for retrieval and ranking. Playback actions remain
  downstream of the approval interrupt. Successful actions are recorded locally;
  they are not proof of listening.
- Optional Last.fm enrichment can import public history and expand candidates.
  Optional MusicBrainz enrichment receives candidates retrieved from Spotify.
  These paths do not satisfy the redesigned external-data boundary and must be
  disconnected or redesigned before the 4R runtime is considered complete.
- The database contained legacy profile, feedback, playlist, session and source
  cache tables. Existing tables remain on disk; new databases do not create them.
  `paths.py` defines XDG-aware locations; action history and credentials are separate.

## New foundation

The new `explicit_preference` table uses explicit columns and a bounded 1,000-entry
profile. It stores the latest explicit strength (-2 through 2), source and timestamp,
not an event history or raw chat. Its service supports validated chat interpretation,
direct input, feedback, inspection, removal and isolated reset. Parser and service
can be composed without a provider or UI; there is no current application runtime.
Tiles and surveys still need user-facing integration. A hosted LLM receives the chat.

Identity uses subject plus stripped, case-folded name and recording artist context;
MusicBrainz IDs are unique within subject and can be attached to a matching name.
Updates without an ID retain the existing ID. Conflicting IDs or names are rejected,
not merged. Name-only identities cannot distinguish namesakes, and ID-only recordings
cannot later gain artist context automatically; these cases require clarification.
Only independently supplied identities are permitted by contract; syntax checks
reject obvious Spotify references but cannot prove metadata provenance.

New databases contain only the new profile schema. Opening any existing database
without its matching schema marker and columns fails read-only without migration or
replacement, including old JSON profiles. Stop and ask the owner how to handle such
files; do not automatically create an alternative database or modify the original.
Reset deletes only rows in the new profile; it does not delete credentials, exports,
legacy records or external data.

## Current data-flow and boundary inventory

| Component | Inputs and outbound requests | Persistence or deletion | Reuse status |
| --- | --- | --- | --- |
| Explicit profile service and SQLite store | Direct structured preferences, tile/survey feedback, or validated chat interpretation; no provider calls of their own. | Stores latest preference fields, source and update time, up to 1,000 rows. Individual removal and reset delete only explicit preference rows. | Offline foundation; callers must ensure identities are independent. |
| Structured preference parser and OpenRouter factory | Sends the supplied chat and system instructions to the configured hosted LLM; receives structured output. No profile or credentials are included in the prompt by this service. | Does not persist the message or response. Provider-side handling is outside TuneTrail's control. | Offline mocked flow tested; hosted use requires user awareness. |
| Spotify client | Reads account, player, device, queue and catalog data; catalog selection can read album or playlist items. Player methods send playback/queue/device mutations, then read current playback. | Keeps last playback in memory; no profile write in the client. Authentication can refresh tokens. | Not wired into the new flow. Mutation methods have no approval check; do not expose them directly. |
| Spotify auth and credentials | Authorization and token exchange/refresh contact Spotify; tokens are used server-side. | File store writes/replaces OAuth tokens. Logout deletes the credential file; **failed refresh also deletes it automatically**. | Not safe for redesigned reuse until credential replacement/deletion behavior is reviewed against owner authorization. |
| Action history | Takes explicit action entries including Spotify URIs; does not prove listening. | `FileActionHistory.record` rewrites a bounded JSON file, retaining up to 100 entries by default. | Not wired to explicit profile; must not be treated as listening or preference evidence. |
| MusicBrainz client and lookup | Searches by arbitrary artist/title or ISRC, looks up artists, and sends those values to MusicBrainz. Lookup accepts a generic candidate and can pass its ISRC or metadata onward. | HTTP responses are not persisted; rate limiting writes a cache-directory lock/timing file. | Not safe for independent discovery reuse without provenance-aware inputs and outbound-boundary tests. |
| Spotify resolver and legacy direct-request/preview helpers | Resolver sends candidate artist/title to Spotify and replaces fields including ISRC with Spotify-derived values; direct helpers select Spotify candidates and build previews. | No preference persistence or player mutation in these helpers. | Resolver output must never be fed to MusicBrainz or ReccoBeats; review before 4T reuse. |
| Legacy profile, caches and export | Historical Spotify-derived rows and potential prior exports are outside the new profile; no legacy profile/export implementation is wired in the current code. | Old files and rows remain untouched. `paths.py` still defines an export location; credential and action-history files are separate. | No implicit migration, deletion, or use as taste signals. Ask the owner before replacing or deleting existing data. |

There is no ReccoBeats adapter in this phase. The retained Spotify client can access
playlist tracks for a selected catalog item, but the explicit profile does not read
them. No provider data is automatically translated into a preference. Runtime type
validation rejects unknown feedback rather than turning it into a dislike. These
boundaries are limited to the current composed profile flow; independently calling
legacy lower-level modules is not protected by a global provenance or approval guard.

## Provider boundary review

The retained MusicBrainz client accepts arbitrary artist/title strings and ISRCs;
it cannot establish their provenance. The retained matching code was designed for
Spotify candidates. Neither is wired into the explicit profile flow or safe to reuse
as an independent-source boundary without a provenance-aware adapter and tests.
The retained Spotify catalog/playback and direct-request services are also not part
of this flow; approval enforcement must be rebuilt and tested before reuse. No
ReccoBeats integration or provider-policy clearance is implied by this phase.

The configured database path was checked during this pass and was absent. Other
installations or overrides may have data. Synthetic and mocked tests cover old JSON
and legacy database preservation, profile reset isolation, invalid feedback and chat,
transaction rollback, and no HTTP calls from a locally mocked profile flow. These
tests do not establish LLM classification accuracy or enforce provenance on arbitrary
callers, and are not live-account testing or provider-policy clearance.
