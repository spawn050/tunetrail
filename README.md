# TuneTrail

A personal, terminal-first Spotify controller that turns explicit requests into
keyboard-driven Spotify playback actions.

## Development

```bash
uv sync --all-extras
uv run tunetrail
```

Before signing in, create a Spotify developer application and configure its
redirect URI and client ID as described in [Spotify setup](docs/spotify-setup.md).

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
