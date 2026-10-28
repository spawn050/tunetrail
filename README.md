# TuneTrail

TuneTrail is being redesigned as a local, personal music discovery application.
The React interface and local Python API use explicit preferences,
independent discovery, TuneTrail ranking and Spotify playback with user approval.
See [the roadmap](ROADMAP.md) for the active phases and their status.

**Current status:** the local React interface and API have passed mocked offline
checks. The legacy terminal interface and its Spotify-derived personalization
workflow have been removed. Live behavior has not been verified.

## Development

```bash
uv sync --all-extras
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

Build the interface from the repository checkout (Node.js 20.19+ or 22.12+):

```bash
cd frontend
npm ci
npm test
npm run build
cd ..
uv run python -m tunetrail.api
```

Open `http://127.0.0.1:8766/`. Build before starting the server; it serves the
local build from the checkout, not from an installed wheel. The API binds to
`127.0.0.1:8766`. See [the interface guide](docs/phase-4v-interface.md) and
[API guide](docs/phase-4u-api.md) for origin protections, limitations, and
offline status. Chat sent to a configured hosted LLM leaves the machine.

The existing Spotify authentication and provider modules are retained for review
and possible reuse. Their presence does not mean provider policies have been cleared.
Live read-only tests require separate
owner authorization; playback acceptance and every playback or queue mutation
require explicit approval.

Existing local databases, credentials and exports are not removed by this code
cleanup. Legacy records must remain isolated from the new explicit profile until
the owner decides how to handle them. Chat sent to a hosted LLM leaves the machine;
the explicit-profile service does not persist raw chat.

Phase 4S has an offline-tested, bounded MusicBrainz discovery foundation using
independently supplied MusicBrainz IDs. It does not resolve namesakes automatically.
Optional ReccoBeats enrichment searches using independent MusicBrainz metadata and
conservatively matches tracks; coverage and accuracy have not been tested live. See
`docs/phase-4s-discovery.md` for data boundaries and provider limitations.

If an existing database has an incompatible profile schema, the profile store stops
without modifying it. Ask the owner how to handle the existing file before replacing,
deleting, or moving data; no automatic migration is performed. Profile reset removes
only explicit preference rows, not credentials, exports, legacy records or caches.
Phase 4T adds offline-tested ranking, Spotify previews and single-use player approval;
failed Spotify token refresh preserves existing credentials. See
`docs/phase-4t-ranking.md` for limits and approval responsibilities. The Phase 4R
data-flow audit describes the historical state before these changes.
