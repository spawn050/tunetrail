# Phase 4V local interface

From the repository checkout, run `npm ci` and `npm run build` in `frontend/`,
then `uv run python -m tunetrail.api` in the repository root. Open
`http://127.0.0.1:8766/`. Node.js 20.19+ or 22.12+ is required. The Python
server serves the built files only when they exist at startup. A wheel installation
does not currently include the frontend. Do not expose the server publicly or use
a separate frontend origin; see `docs/phase-4u-api.md` for the security boundary.

Chat uses the configured hosted LLM and leaves the machine. Clear lasting chat
preferences are saved immediately; ambiguous interpretations require clarification.
Raw chat is not persisted by this service. Spotify operations require client
configuration and local credentials. Use Connect Spotify in the interface to
authorize; see `docs/spotify-setup.md`. Missing credentials or provider failures are
surfaced as errors.

Spotify artist and track searches are read-only and displayed separately. They are
not used as discovery seeds or feedback identities. Independent discovery requires
a MusicBrainz UUID independently obtained by the owner, with explicit confirmation;
do not translate Spotify IDs or metadata. There is no name search or namesake
selection. The optional ReccoBeats feature estimates are provisional, can be missing,
and its foundational metadata is Spotify-derived; no policy clearance is implied.

Suggestions are MusicBrainz recordings, not invented survey entries. The optional
survey walks the current suggestions in deterministic order; like and dislike save
lasting explicit preferences, while skip and not-familiar do not change taste. Tile
feedback also saves immediately. The profile shows and removes individual entries;
reset requires confirmation and removes only explicit preference rows. No old
database, credentials or exports are removed automatically. An incompatible profile
database stops startup without replacement.

Choose a device and action, then create a preview. It shows the ordered Spotify
matches, URI, match evidence, ranking signals and issues. If the active-device
default is chosen, Spotify determines the active device at execution time and it may
change; choose a specific device for a fixed target. Only the approval button sends
the stored proposal for execution. Cancel or changing the selected action/device
invalidates the preview. Queue append has one track per proposal; unsupported
constraints prevent an approvable proposal. A failed approval consumes the proposal,
and playback may have changed even if the request reports a failure: inspect Spotify
before retrying. Approval is not evidence of listening.

Frontend interaction tests use a mocked API; Python API tests use mocked providers
and cover the server-side request-to-approval flow. No live provider, real-browser
acceptance, or actual playback test has been performed. Live read-only evaluation
requires separate owner authorization; playback acceptance requires separate
authorization, Spotify Premium and an active device, and explicit approval for every
playback or queue mutation. The application makes no provider-policy clearance claim.
