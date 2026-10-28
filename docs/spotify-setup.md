# Spotify developer-app setup

> Historical setup notes: the legacy interface and entry point have been removed.
> The local backend is not implemented, and the scopes and sign-in instructions below
> must be reviewed for the redesign before reuse. Do not run live tests without
> separate authorization.

TuneTrail is a personal Spotify controller. Spotify Premium is required for
playback and queue control.

## Create the application

1. Open the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. Create an app for personal use and copy its **Client ID**. Do not create or
   store a client secret: TuneTrail uses Authorization Code with PKCE.
3. In the app settings, add this Redirect URI exactly:

   ```text
   http://127.0.0.1:8765/callback
   ```

   If you set a different local callback URI, add that exact URI to Spotify and
   set `TUNETRAIL_SPOTIFY_REDIRECT_URI` to the same value.

4. Create the local, Git-ignored configuration file from the example:

   ```bash
   cp .env.example .env
   ```

   Add the Client ID to `.env`:

   ```text
   TUNETRAIL_SPOTIFY_CLIENT_ID=your-client-id
   ```

   TuneTrail loads `.env` automatically at startup. Shell environment variables
   override `.env` values when needed. The client ID is public. Never put an
   access token, refresh token, API key, or any other secret in a tracked file.

## Requested scopes

TuneTrail currently requests only:

- `user-read-private` — read the authenticated user;
- `user-read-playback-state` — list devices and inspect playback;
- `user-read-currently-playing` — display the current track;
- `user-modify-playback-state` — control playback and add a track to the queue.
- `playlist-read-private` and `playlist-read-collaborative` — preview tracks from a playlist the user can access.
- `user-top-read` — derive a compact local taste profile from top artists and tracks.
- `user-read-recently-played` — apply local repetition avoidance.

If you previously authorized TuneTrail before any newly listed scope was added,
use **Log out** and sign in again to grant it. TuneTrail does not retain playlist
contents after profile refresh; it stores only selected playlist references and
bounded derived taste signals.

On success, Spotify tokens are stored at `~/.config/tunetrail/auth.json` with
owner-only (`0600`) permissions. Use TuneTrail's logout action to remove them.
