# Phase 4R data-flow inventory

This inventory records the removed legacy runtime and the partially implemented 4R
foundation. The legacy TUI, entry point, graph, Spotify-derived profile/retrieval/
ranking services and Spotify-candidate MusicBrainz enrichment were removed from
the working tree. There is currently no runnable user-facing runtime. Lower-level
provider code remains and must be audited before reuse. The legacy profile, feedback,
playlist and cache methods and the Last.fm client have since been removed. The
explicit preference store leaves legacy database tables and records untouched.

## Historical legacy runtime paths

- `app.py` wires `SpotifyProfileService` into the TUI. Its refresh reads Spotify
  top artists, top tracks, recent tracks and tracks from selected playlists, then
  persists a compact `profile_summary` and playlist references.
- `tui/app.py` can select playlists, record feedback on the Spotify current track,
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
replacement, including old JSON profiles. Handling those files is an owner decision.
Reset deletes only rows in the new profile; it does not delete credentials, exports,
legacy records or external data.

## Provider boundary review

The retained MusicBrainz client accepts arbitrary artist/title strings and ISRCs;
it cannot establish their provenance. The retained matching code was designed for
Spotify candidates. Neither is wired into the explicit profile flow or safe to reuse
as an independent-source boundary without a provenance-aware adapter and tests.
The retained Spotify catalog/playback and direct-request services are also not part
of this flow; approval enforcement must be rebuilt and tested before reuse. No
ReccoBeats integration or provider-policy clearance is implied by this phase.

The configured default database path was checked for existence during this pass;
no database was present there. Other installations or overrides may have data.
Preserve existing data and ask before deleting or replacing legacy records.
