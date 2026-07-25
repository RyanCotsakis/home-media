# Home media server

This is a self-hosted household media stack. A Telegram bot accepts a request,
asks its owner for confirmation, and hands confirmed movie, TV, or music
requests to the relevant Arr application. Prowlarr supplies indexers,
qBittorrent downloads, and Jellyfin serves the finished library.

Use only indexers, media sources, and downloads that you are authorized to
use. Keep the service interfaces private; the intended remote-access path is
NordVPN Meshnet, not a public port-forward.

## Architecture

```text
Telegram app
    │ long polling
    ▼
worker ─────► API ─────► Postgres (requests and chat history)
  │                         │
  └─────────────────────────┴────► Redis (notification jobs)
                                    │
                                    ▼
                   Radarr (movies) / Sonarr (TV) / Lidarr (music)
                                    ▲
                                    │ indexers
                                Prowlarr
                                    │
                                    ▼
                              qBittorrent
                                    │ /data/downloads
                                    ▼
                       /data/library ─────► Jellyfin
```

The Docker services use two isolated bridge networks. Only Jellyfin is
published for household playback; the Arr, Prowlarr, and qBittorrent web UIs
bind to `127.0.0.1` for local use or access through an SSH/Meshnet tunnel.

## What you need

- Docker Engine with the Compose plugin (Docker Desktop with WSL integration
  is fine for a WSL-hosted setup).
- A Telegram account and bot token.
- Storage for the media library. This installation uses `/srv/media`.
- Optional: a Gemini API key for natural-language requests and recommendations.
- Optional: a NordVPN WireGuard private key when using the dedicated Gluetun
  downloader tunnel.

## First-time setup

### 1. Create storage directories

On the Linux/WSL host, create the persisted configuration and media paths.
The owner must match `PUID` and `PGID` in the environment file (normally your
Linux user, whose values are shown by `id -u` and `id -g`).

```bash
sudo install -d -o "$(id -u)" -g "$(id -g)" \
  /srv/home-media/config \
  /srv/media/downloads \
  /srv/media/library/movies \
  /srv/media/library/tv \
  /srv/media/library/music
```

Downloads first appear in `/srv/media/downloads`. After the Arr application
imports them, movies and TV episodes appear under `/srv/media/library/movies`
and `/srv/media/library/tv`. In Windows Explorer, these are available through
`\\wsl$\<your-distro>\srv\media` (for example, `\\wsl$\Ubuntu\srv\media`).

### 2. Create the environment file

```bash
cd /home/ryan/home_cotsakis/infra/docker
cp .env.example .env
chmod 600 .env
```

Edit `.env`; never commit it or paste it into chat. Generate long random
values for all passwords and internal tokens, for example with
`openssl rand -hex 32`.

#### Required environment values

| Variable | Purpose |
| --- | --- |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Database identity and password. |
| `DATABASE_URL` | PostgreSQL connection string. Its user/password/database must match the three values above. |
| `INTERNAL_API_TOKEN`, `AUTOMATION_WEBHOOK_TOKEN` | Separate random internal secrets. |
| `TELEGRAM_BOT_TOKEN` | Token issued by BotFather. Treat it like a password. |
| `TELEGRAM_ALLOWED_USER_IDS` | Comma-separated numeric Telegram account IDs allowed to request media. |
| `TZ` | IANA time zone, such as `Europe/Zurich`. |
| `PUID`, `PGID` | Owner IDs for files written by LinuxServer containers. |
| `CONFIG_ROOT`, `MEDIA_ROOT` | Host paths for application configuration and media. |

#### Optional application values

| Variable | When to set it |
| --- | --- |
| `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_GOOGLE_SEARCH_ENABLED` | Enable conversational requests and optional current-information lookup. Leave the key empty to require `/movie`, `/tv`, or `/music` commands. |
| `RADARR_API_KEY`, `SONARR_API_KEY`, `LIDARR_API_KEY` | Required when switching `AUTOMATION_PROVIDER` from `mock` to `arr`. |
| `RADARR_URL`, `SONARR_URL`, `LIDARR_URL` | Normally retain the internal Docker URLs from `.env.example`. |
| `RADARR_ROOT_FOLDER`, `SONARR_ROOT_FOLDER`, `LIDARR_ROOT_FOLDER` | Container paths; retain `/data/library/...` unless Compose mounts change. |
| `RADARR_QUALITY_PROFILE`, `SONARR_QUALITY_PROFILE` | Defaults to `HD-720p`, the compact 720p-only profile. |
| `QBITTORRENT_USERNAME`, `QBITTORRENT_PASSWORD` | Needed when the bot should report qBittorrent status. |
| `JELLYFIN_API_KEY` | Needed only for library-status reporting. |
| `NORDVPN_PRIVATE_KEY`, `NORDVPN_COUNTRIES` | Needed only with the optional Gluetun VPN Compose override. |

#### Advanced values and defaults

| Variable | Default / purpose |
| --- | --- |
| `REDIS_URL` | Internal Redis URL; retain the Compose default. |
| `APP_ENVIRONMENT` | Use `production` for the deployed stack. |
| `LLM_PROVIDER` | `gemini` uses Gemini when its key exists; otherwise command-only parsing is used. |
| `MEDIA_MARKET_COUNTRY` | Country used for conversational streaming-availability answers. |
| `CHAT_HISTORY_LIMIT` | Number of prior chat messages supplied to the conversational provider. |
| `MEDIA_ROOT_FOLDER` | Parent container library path; normally `/data/library`. |
| `JELLYFIN_URL` | Internal Jellyfin address; normally `http://jellyfin:8096`. |
| `QBITTORRENT_URL` | qBittorrent address for bot status checks; normally `http://qbittorrent:8080`. |
| `CHAT_READER_DATABASE_URL`, `CHAT_SQL_TIMEOUT_MS`, `CHAT_SQL_ROW_LIMIT` | Optional restricted database login and limits for read-only chat status questions. |
| `JELLYFIN_BIND_ADDRESS` | Published Jellyfin bind address. Leave it restricted by a host firewall/Meshnet policy. |
| `NORDVPN_SERVICE_PROVIDER` | Keep `nordvpn` when using the Gluetun override. |

Keep `AUTOMATION_PROVIDER=mock` until Radarr, Sonarr, and Lidarr are set up.
After their API keys are present, set `AUTOMATION_PROVIDER=arr` and recreate
the API and worker.

### 3. Create and restrict the Telegram bot

1. Open [@BotFather](https://t.me/BotFather) in Telegram and send `/newbot`.
2. Choose its display name and a username ending in `bot`; BotFather returns
   the token. Telegram documents this flow and warns that the token controls
   the bot, so keep it private. [Telegram BotFather guide](https://core.telegram.org/bots/features)
3. Put that value in `TELEGRAM_BOT_TOKEN`.
4. Find the numeric ID for every household member who may make requests, then
   set `TELEGRAM_ALLOWED_USER_IDS`, for example `123456789,987654321`.
   Do not use a username here.
5. In BotFather, use `/mybots` → your bot → **Edit Commands**, then add:

   ```text
   movie - Add a movie
   tv - Add a TV series
   music - Add an album
   ```

6. Open the bot conversation and send `/start`, then try `/movie Arrival` or
   `/tv Friends`. The worker receives messages through Telegram long polling;
   do not configure a Telegram webhook. Long polling and webhooks cannot be
   used simultaneously. [Telegram Bot API](https://core.telegram.org/bots/api)

### 4. Start the stack

```bash
cd /home/ryan/home_cotsakis/infra/docker
docker compose --env-file .env --profile media up -d --build
docker compose --env-file .env --profile media ps
```

For the dedicated downloader VPN, set `NORDVPN_PRIVATE_KEY` first, then use:

```bash
docker compose --env-file .env \
  -f docker-compose.yml -f docker-compose.vpn.yml \
  --profile media --profile vpn up -d --build
```

To apply only bot/API code or environment changes, recreate the two services:

```bash
docker compose --env-file .env --profile media up -d --build api worker
```

## Configure the media applications

Open the local UIs from the host:

| Service | Address |
| --- | --- |
| Jellyfin | `http://localhost:8096` |
| Prowlarr | `http://localhost:9696` |
| Radarr | `http://localhost:7878` |
| Sonarr | `http://localhost:8989` |
| Lidarr | `http://localhost:8686` |
| qBittorrent | `http://localhost:8080` |

When connecting from another Meshnet device, create an SSH tunnel instead of
opening these ports publicly. Example: `ssh -L 8989:127.0.0.1:8989 user@host`
then open `http://localhost:8989` on your own device.

1. Complete the initial setup for qBittorrent, Radarr, Sonarr, Lidarr, and
   Prowlarr. Configure qBittorrent’s incomplete/completed download location as
   `/data/downloads` inside its container.
2. In Radarr, set the movie root folder to `/data/library/movies`; in Sonarr,
   set the TV root to `/data/library/tv`; in Lidarr, set music to
   `/data/library/music`.
3. In Prowlarr, add only indexers you are authorized to use, then add Radarr,
   Sonarr, and Lidarr as applications using the internal Docker names (for
   example `http://radarr:7878`) and their API keys. Sync indexers to the apps.
4. In each Arr app, add qBittorrent as the download client using
   `http://qbittorrent:8080` and the qBittorrent credentials. Assign separate
   categories such as `movies`, `tv`, and `music` if desired.
5. Copy each Arr API key into `.env`, set `AUTOMATION_PROVIDER=arr`, then run
   the API/worker recreation command above.
6. Create Jellyfin libraries pointing to `/media/movies`, `/media/tv`, and
   `/media/music` inside its container.

### Compact 720p policy

The bot assigns new movie and TV requests to `HD-720p`. In Radarr’s **Settings
→ Quality**, set allowed 720p definitions to roughly 2--10 MB/minute; a
two-hour movie is then normally around 1 GB, with a ~1.2 GB upper bound. In
Sonarr, use roughly 2--8 MB/minute for 720p episodes.

For each synced torrent indexer, set **Minimum Seeders** to **10** where that
indexer exposes the setting, then re-sync it. This favours healthy torrents;
temporarily lower it only for rare older titles.

## Daily use and operations

- Request media with `/movie Title`, `/tv Title`, or `/music Artist - Album`.
  The bot creates a pending request and sends a **Confirm request** button.
- Check services: `docker compose --env-file .env --profile media ps`.
- Follow bot/API logs: `docker compose --env-file .env --profile media logs -f worker api`.
- Stop without deleting data: `docker compose --env-file .env --profile media down`.
- Configuration persists under `${CONFIG_ROOT}` and media under `${MEDIA_ROOT}`.
  Back up both, plus Postgres; Compose named volumes retain Postgres and Redis
  data unless explicitly removed.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Bot does not answer | Check `TELEGRAM_BOT_TOKEN`, allowlisted numeric user ID, and `worker` logs. Ensure no Telegram webhook is configured for the same bot. |
| Request confirms but nothing downloads | Make sure `AUTOMATION_PROVIDER=arr`, the Arr API keys are set, Prowlarr has synced indexers, and qBittorrent is configured as a download client. |
| Sonarr says “No available indexers” | Configure and sync at least one authorized Prowlarr indexer. |
| Files are hard to find | Check `/srv/media/downloads` first, then `/srv/media/library/movies` or `/srv/media/library/tv` after import. |
| TV confirmation fails with “Title must not be empty” | This is a current application bug in the TV submission payload; see the issue note below. |

## Current known limitation

TV confirmations currently fail because the API submits a Sonarr request with
the TVDB ID but omits Sonarr’s required `title` field. Movies work because
Radarr accepts their TMDB-ID-only request. The bot still creates the pending
confirmation, but after tapping it Telegram shows only a short failure alert
instead of posting a normal chat message. The required fix is to include the
resolved series title in the Sonarr submission payload.
