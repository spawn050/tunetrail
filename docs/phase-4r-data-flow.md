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

The new `explicit_preference` table is separate from legacy tables. Its service
supports a bounded structured profile, chat interpretation, inspection and an
isolated reset. The structured chat parser and service are not yet wired into the
application; there is no current application runtime. Direct input, tiles and
surveys still need user-facing integration. No raw chat is saved by the new service.

The configured default database path was checked for existence during this pass;
no database was present there. Other installations or overrides may have data.
Preserve existing data and ask before deleting or replacing legacy records.
