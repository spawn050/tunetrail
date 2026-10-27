# TuneTrail

A personal, terminal-first Spotify controller that turns explicit requests into
keyboard-driven Spotify playback actions.

## Development

```bash
uv sync --all-extras
cp .env.example .env
# Set TUNETRAIL_SPOTIFY_CLIENT_ID in .env
uv run tunetrail
```

Before signing in, create a Spotify developer application and configure its
redirect URI and client ID as described in [Spotify setup](docs/spotify-setup.md).

## Natural-language requests

TuneTrail supports direct and Spotify-only personalized requests while retaining
the explicit catalog commands below. Add these optional settings to your untracked `.env` to enable
natural-language parsing through OpenRouter:

```text
TUNETRAIL_OPENROUTER_API_KEY=...
TUNETRAIL_LLM_MODEL=openai/gpt-4.1-mini
```

For example: `Play Taylor Swift from 2008 to 2012`, `Append Bad Romance by
Lady Gaga`, or `Play something peaceful for studying for 45 minutes`. TuneTrail shows the parsed intent and queue preview, then requires
**Confirm** before Spotify playback changes. **Cancel**, **Edit request**, and
**Regenerate** do not mutate Spotify.

Personalized queues use local Spotify top artists/tracks, recently played tracks,
explicit feedback, and only playlists that the user manually selects as taste
signals. Use **Refresh taste profile** after signing in, then optionally load and
toggle owned/collaborative playlists. **Like current**, **Dislike current**, and
the other feedback controls affect later rankings. Profile export and reset are
local-only; they never affect Spotify credentials or playback.

The LLM parses intent only; Spotify retrieval, filtering, ranking, URI resolution,
and player calls remain deterministic application code. Spotify does not provide
reliable current mood/audio-feature data for this flow, so mood/activity requests
are personalized from local taste signals rather than treated as claimed audio analysis.

## Catalog queue workflow

TuneTrail's catalog commands remain explicit and keyboard-first.

```text
search track <query>
search artist <query>
search album <query>
search playlist <query>
```

Choose a result, select **Preview play now** or **Preview append**, inspect the
tracks, then choose **Confirm**. Cancelling a preview never changes Spotify.
Use `Ctrl-K` to focus the command field, `Up`/`Down` to recall in-session
commands, `Escape` to cancel a preview, and `Ctrl-R` to refresh Spotify state.

TuneTrail keeps a small, owner-only local action history at the XDG state path.
It records confirmed action outcomes and track URIs, but never search text,
playlist contents, tokens, or a catalog copy.
