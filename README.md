# TuneTrail

TuneTrail is being redesigned as a local, personal music discovery application.
The planned React interface and local Python API will use explicit preferences,
independent discovery, TuneTrail ranking and Spotify playback with user approval.
See [the roadmap](ROADMAP.md) for the active phases and their status.

**Current status:** there is no runnable user-facing application. The legacy
terminal interface and its Spotify-derived personalization workflow have been
removed; the browser interface and API are planned in Phases 4U–4V. The remaining
modules are development foundations, not a complete end-to-end product.

## Development

```bash
uv sync --all-extras
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

The existing Spotify authentication and provider modules are retained for review
and possible reuse. Their presence does not mean the redesigned app is integrated
or that provider policies have been cleared. Live read-only tests require separate
owner authorization; playback acceptance and every playback or queue mutation
require explicit approval.

Existing local databases, credentials and exports are not removed by this code
cleanup. Legacy records must remain isolated from the new explicit profile until
the owner decides how to handle them. Chat sent to a hosted LLM leaves the machine;
the explicit-profile service does not persist raw chat.

If an existing database has an incompatible profile schema, the profile store stops
without modifying it. Ask the owner how to handle the existing file before replacing,
deleting, or moving data; no automatic migration is performed. Profile reset removes
only explicit preference rows, not credentials, exports, legacy records or caches.
Retained Spotify authentication and player modules require boundary review before
reuse: failed token refresh currently deletes the credential file, and player methods
do not enforce approval themselves. See `docs/phase-4r-data-flow.md`.
