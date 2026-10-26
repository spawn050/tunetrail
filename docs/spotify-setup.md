# Spotify developer-app setup

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

4. Configure the client ID in a local, Git-ignored `.env` file or in
   `~/.config/tunetrail/config.toml`:

   ```bash
   TUNETRAIL_SPOTIFY_CLIENT_ID=your-client-id
   ```

   ```toml
   spotify_client_id = "your-client-id"
   ```

   The client ID is public. Never put an access token, refresh token, API key,
   or any other secret in a tracked file.

## Requested scopes

TuneTrail currently requests only:

- `user-read-private` — read the authenticated user;
- `user-read-playback-state` — list devices and inspect playback;
- `user-read-currently-playing` — display the current track;
- `user-modify-playback-state` — control playback and add a track to the queue.

On success, Spotify tokens are stored at `~/.config/tunetrail/auth.json` with
owner-only (`0600`) permissions. Use TuneTrail's logout action to remove them.
