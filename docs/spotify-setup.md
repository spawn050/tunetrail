# Spotify developer-app setup

> The local API and React interface do not provide Spotify sign-in or logout.
> They require existing local credentials. Do not run live tests without separate
> authorization; playback also requires explicit approval for each action.

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

- `user-read-private`;
- `user-read-playback-state` — list devices and inspect playback;
- `user-read-currently-playing`;
- `user-modify-playback-state` — start playback and add a track to the queue.

The redesigned runtime does not ingest playlists, top tracks, or listening history.
Existing credentials may have different scopes; the current interface does not
offer reauthorization. Check Spotify's current developer documentation and arrange
authorization separately before using live features. Do not overwrite existing
credentials without the owner's decision.

On success, Spotify tokens are stored at `~/.config/tunetrail/auth.json` with
owner-only (`0600`) permissions. There is currently no logout control in the
interface. Failed token refresh preserves the existing credential file. Ask the
owner before removing or replacing it.
