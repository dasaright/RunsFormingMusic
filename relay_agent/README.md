# Runsforming Audio Relay

The relay lets the Railway-hosted bot play YouTube audio through a volunteer's
Windows internet connection. The Discord bot token, database, permissions,
queues, and commands remain on Railway.

## Install

1. Download `RunsformingRelay-Windows.zip` from a `relay-` GitHub release.
2. Extract the complete ZIP. Keep `RunsformingRelay.exe` and `README.md` in
   the main folder, with `ffmpeg.exe` and `yt-dlp.exe` inside `Files`.
3. Run `RunsformingRelay.exe` and enter the Railway WebSocket URL and relay
   token supplied by the bot owner.
4. Leave the program open while you are willing to relay audio.

Ask the bot owner for a relay token before completing setup.

The program creates `relay-config.json` next to the executable. That file
contains a secret relay token and must not be shared.

## Bot-owner Railway setup

1. Generate a public Railway domain for the existing bot service.
2. Add `AUDIO_RELAY_TOKENS` as a JSON object. Each relay PC gets a unique,
   revocable token, for example:

   ```json
   {"Taco PC":"long-random-token","Volunteer PC":"another-random-token"}
   ```

3. Give each volunteer the WebSocket URL
   `wss://YOUR-DOMAIN.up.railway.app/relay` and only their own token.
4. Redeploy after changing the token list. The `/health` endpoint reports the
   number of connected relays but never exposes their names or tokens.

The relay makes an outbound encrypted WebSocket connection. Volunteers do not
need to open router ports, configure port forwarding, or run the Discord bot.

## Stable update model

The relay protocol is versioned independently of the main bot. The executable
only supports four stable operations: authenticate, inspect a YouTube URL,
stream fixed-format PCM audio, and cancel a stream. Changes to Discord
commands, permissions, queues, forms, personas, or server configuration do not
require a relay update.

`yt-dlp.exe` checks for its own updates whenever the relay starts. FFmpeg and
the relay executable only need replacement when the protocol or their own
runtime requirements change.

## Optional cookies

The relay normally uses the PC's residential connection without an account.
If a video requires authentication, set `cookies_file` in `relay-config.json`
to a Netscape-format cookie file stored on that same PC. Never share a cookie
file with the bot owner, Railway, or another relay host.

## Soundboard playback

The relay opens a desktop window. Choose a folder to scan its audio files; the
folder is remembered in relay-config.json. Empty or undecodable files are omitted.
Use Refresh files after adding or removing files.

Join a Discord voice channel and use `y!join` to connect the bot. In the relay
window, click Refresh Discord and select that server/channel. A single click
on an audio file plays it immediately as a soundboard clip over the current music.
The music and queue are preserved. Multiple clips can overlap (up to eight).
Stop clips stops only clips. `y!pause` pauses music; `y!resume` or `y!play`
without a URL resumes it. Clips can play while music is paused. `y!stop` stops
all audio and clears the music queue. Audio files remain on the PC: only decoded audio is
sent to Railway. Local controls require the approved Owner PC relay credential;
other volunteer credentials continue to support YouTube requests.

Bot/command changes continue to work without replacing this executable. This
MP3 feature requires one executable upgrade from the original console version.

## YouTube playlists and automatic disconnect

`y!play <video URL>` queues only the linked song, even when the URL includes
playlist information. `y!playlist <playlist URL>` appends the first 50 entries
in playlist order (unavailable entries are skipped). Install this version of
the relay to enable playlist inspection. Local MP3 and single-video playback
remain compatible with older relays.

Audio commands connect or move the bot to the requesting user's voice channel.
The bot leaves when no human listeners remain or five minutes pass without
another audio command or local playback request, including during playback.

## Certificate verification

The relay bundles Mozilla's public certificate roots through certifi and uses
that bundle explicitly for encrypted connections. Certificate expiration and
hostname checks remain enabled. This avoids selecting stale certificates from
the Windows certificate store. Keep the PC clock synchronized. If an error
persists while Chrome connects, check Chrome's certificate issuer and expiration
date to identify an antivirus or network HTTPS inspection certificate.

Soundboard mixing and pause/resume require this updated executable. Keep your
existing relay-config.json when replacing the program.

## Desktop controls

Select your connected Discord destination at the top. The left panel shows
YouTube music and the queue, with the current song highlighted. Pause/Play
controls music only. Next advances the queue; Previous returns to the most
recently played song and puts the interrupted song at the front of the queue.
The right panel plays soundboard clips only when a file row is clicked.
Stop all clips stops every active clip while preserving music. Clips do not
post Now playing messages in Discord. The queue refreshes every two seconds.

Music uses a half-second jitter buffer and absolute 20 ms frame deadlines
to avoid accumulating Windows scheduling delays. Soundboard clips remain
immediate; music re-buffers if its network stream stalls.

## Audio library and drag-and-drop

Supported soundboard formats: MP3, OGG/OGA, Opus, WAV, FLAC, M4A, AAC, WMA,
and AIFF/AIF. Files must contain decodable audio. The list hides extensions.
Choose a folder, then drop one or more audio files anywhere in the window.
The relay copies them into the selected folder and refreshes the library;
originals are preserved. Existing names are preserved with numbered copies.
Files already in the selected folder and invalid files are skipped.

## Relay updates

Update automatically is enabled by default. The program checks published
`relay-` releases in dasaright/RunsFormingMusic on startup and every six hours.
Use Check for updates for a manual check; uncheck Update automatically to
turn automatic updates off. The preference is saved in relay-config.json.

Updates download in the background and check the release SHA-256 checksum.
Installation waits until music, its queue, and clips are finished, followed
by 30 seconds idle. The relay then restarts itself. Settings, tokens, cookies,
and soundboard files are not replaced. Previous program files are backed up
while installing; a failed copy restores them. The last installer result is
stored at Files/update-status.txt. If a check or download fails, playback
continues and the relay retries at its next scheduled check.

For this initial upgrade, close the old relay and extract the new bundle
into its folder, preserving relay-config.json. Legacy root-level FFmpeg and
yt-dlp files can be removed after Files contains their replacements. Future
relay updates use GitHub releases automatically; ordinary bot updates do not
trigger a new executable build.

## Shared clips

The right pane lists your chosen local folder and the separate `sharedclips` folder beside the executable. Name sorts filenames; Shared toggles Local-first / Shared-first, alphabetically within each group. Click a filename to play. **Sync clips** uploads new files from `sharedclips` and downloads missing shared files. Local files are never uploaded. Put files into `sharedclips` to share them. Shared files are public in this repository. Sync adds files only; rename conflicts and keep clips under 20 MB. Updates preserve both folders.

Bot administrator: configure `SHARED_CLIPS_GITHUB_TOKEN` on Railway with a fine-grained GitHub token restricted to `dasaright/RunsFormingMusic`, Contents read/write. Approved relay tokens authorize sync. The GitHub token stays on the bot and is never distributed in the executable.

## Personal relay tokens and updates

Use `!token` in Discord (server or DM) to receive a personal token privately. Each use replaces the old token and disconnects relays using it. Setup asks only for this token; the Railway URL is fixed automatically and the bot supplies your Discord username. Invalid/replaced tokens prompt for a new token. Join voice and click a clip to have the bot connect automatically to your channel. You can control audio only from your own voice channel.

Automatic updates wait for actual audio running on this PC, not remote queued music. Manual checks install when downloaded; **Install update now** installs a pending update immediately, interrupting this relay's audio. Both clip folders and configuration are preserved.

The song pane retains previous songs for the current session. Click any song to switch to it and continue from there; the current song remains highlighted. Stop/leave clears the session playlist. Install update now is disabled until an update is downloaded. The bot remains in voice while at least one connected personal relay's Discord user is present in that channel; otherwise the usual five-minute idle timeout applies.

Update failures are recorded in `Files/update-launch.log` and `Files/update-install.log`. The relay only closes after the independent installer confirms it has started. Restart uses a fresh executable runtime, and renamed relay executables are updated in place. A failed update delays automatic retry until the next scheduled check; manual retry remains available.
