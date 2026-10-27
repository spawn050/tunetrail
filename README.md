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

Phase 3 supports clear direct requests, while retaining the explicit catalog
commands below. Add these optional settings to your untracked `.env` to enable
natural-language parsing through OpenRouter:

```text
TUNETRAIL_OPENROUTER_API_KEY=...
TUNETRAIL_LLM_MODEL=openai/gpt-4.1-mini
```

For example: `Play Taylor Swift from 2008 to 2012` or `Append Bad Romance by
Lady Gaga`. TuneTrail shows the parsed intent and queue preview, then requires
**Confirm** before Spotify playback changes. **Cancel**, **Edit request**, and
**Regenerate** do not mutate Spotify.

Vague mood/activity, duration, familiarity, and discovery requests are deferred
to Phase 4. The LLM parses intent only; Spotify search, filtering, ranking, URI
resolution, and player calls remain deterministic application code.

## Catalog queue workflow

TuneTrail's catalog commands are explicit and keyboard-first; natural-language
requests are planned for a later phase.

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
