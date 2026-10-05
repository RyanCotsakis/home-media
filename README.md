# Home media server

A private household movie and TV stack operated through a Telegram bot. A
confirmed request is sent to Radarr (movies) or Sonarr (TV). Prowlarr provides
authorized indexers, qBittorrent downloads, and Jellyfin serves the imported
library.

Use only indexers, media sources, and downloads that you are authorized to
use. Keep management interfaces private. The intended remote-access path is
NordVPN Meshnet, not public router port-forwarding.

## Architecture

```text
Telegram (long polling)
          │
          ▼
       worker ───── API ───── Postgres
          │          │
          │          └─────── Redis job queue
          │
          └──── Radarr / Sonarr ◄──── Prowlarr
                         │
                         ▼
                    qBittorrent
                         │ /data/downloads
                         ▼
                    /data/library ───── Jellyfin
```

Only Jellyfin is published for playback. Prowlarr, the Arr applications, and
qBittorrent bind to `127.0.0.1` and should be reached locally or through an SSH
tunnel over Meshnet.

## Requirements

- Docker Engine with the Compose plugin.
- A Telegram bot token and the numeric Telegram IDs allowed to use it.
- Storage for configuration and media; this deployment uses
  `/srv/home-media/config` and `/srv/media`.
- An indexer and media sources you are authorized to use.
- Optional Gemini API key for conversational requests. Without it, `/movie`
  and `/tv` commands still work.
- Optional NordVPN WireGuard key for the dedicated Gluetun downloader tunnel.

## First-time setup

### 1. Create storage

Use the same numeric owner as `PUID` and `PGID` in `.env`:

```bash
sudo install -d -o "$(id -u)" -g "$(id -g)" \
  /srv/home-media/config \
  /srv/media/downloads \
  /srv/media/library/movies \
  /srv/media/library/tv
```

Every downloading and importing container sees the host media root as `/data`.
Jellyfin sees the completed library as `/media`.

Radarr and Sonarr should use hard links when importing torrents. The download
and library paths then show the same media under two names, but both names
reference one inode and consume one set of disk blocks. This works because
`/data/downloads` and `/data/library` are on the same filesystem and are
mounted into each Arr container through the same `/data` bind mount.

### 2. Configure secrets and paths

```bash
cd /home/ryan/home_cotsakis/infra/docker
cp .env.example .env
chmod 600 .env
```

Never commit `.env` or put credentials in comments. Generate separate long
random values for `POSTGRES_PASSWORD`, `INTERNAL_API_TOKEN`, and
`AUTOMATION_WEBHOOK_TOKEN`, for example with `openssl rand -hex 32`.

Required deployment values include:

| Setting | Purpose |
| --- | --- |
| `POSTGRES_*`, `DATABASE_URL`, `REDIS_URL` | Application storage. The PostgreSQL URL must match the declared user, password, and database. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_IDS` | Bot credential and comma-separated numeric household allowlist. |
| `CONFIG_ROOT`, `MEDIA_ROOT`, `PUID`, `PGID`, `TZ` | Host storage, ownership, and time zone. |
| `RADARR_API_KEY`, `SONARR_API_KEY` | Server-owned Arr credentials used when `AUTOMATION_PROVIDER=arr`. |
| `QBITTORRENT_USERNAME`, `QBITTORRENT_PASSWORD` | Download-client connection and status checks. |
| `RADARR_ROOT_FOLDER`, `SONARR_ROOT_FOLDER` | Keep the defaults under `/data/library`. |
| `RADARR_QUALITY_PROFILE`, `SONARR_QUALITY_PROFILE` | Default `HD-720p`; 720p is preferred while non-remux 1080p and reputable SD qualities are fallbacks. |
| `TORRENT_MINIMUM_SEEDERS` | Default `10`; rejects weak torrents so a well-seeded fallback can beat an under-seeded 720p release. |
| `JELLYFIN_API_KEY` | Library status and an immediate library refresh after an Arr import. |

`GEMINI_API_KEY` and the Gluetun/NordVPN settings are optional. Keep
`AUTOMATION_PROVIDER=mock` until the media services have completed their
first-run setup.

### 3. Configure Telegram

1. Create the bot with [BotFather](https://core.telegram.org/bots/features)
   and place its token in `.env`.
2. Add numeric user IDs—not Telegram usernames—to
   `TELEGRAM_ALLOWED_USER_IDS`.
3. In BotFather, configure these commands:

   ```text
   movie - Add a movie
   tv - Add a TV series
   downloads - Show active downloads and progress
   status - Show request status
   stop - Cancel and remove a download
   delete - Delete a library item and its files
   ```

4. Do not configure a Telegram webhook. The worker uses long polling and the
   Bot API permits only one of those delivery modes at a time.

### 4. Start services

```bash
cd /home/ryan/home_cotsakis/infra/docker
docker compose --env-file .env --profile media up -d --build
docker compose --env-file .env --profile media ps
```

Open the first-run interfaces from the Docker host:

| Service | URL |
| --- | --- |
| Jellyfin | `http://localhost:8096` |
| Prowlarr | `http://localhost:9696` |
| Radarr | `http://localhost:7878` |
| Sonarr | `http://localhost:8989` |
| qBittorrent | `http://localhost:8080` |

From another Meshnet device, tunnel a management port rather than publishing
it—for example, `ssh -L 8989:127.0.0.1:8989 user@host` for Sonarr.

### 5. Connect the media services

1. Complete each first-run screen. In qBittorrent, use port 8080, create the
   credentials stored in `.env`, and set its default save path to
   `/data/downloads`.
2. Copy the Arr API keys to `.env`. Ensure the named `HD-720p` quality profiles
   exist as described below.
3. Run the idempotent service configurator:

   ```bash
   cd /home/ryan/home_cotsakis/infra/docker
   python3 configure_services.py --apply
   ```

   It creates or reconciles the root folders, Prowlarr application links,
   qBittorrent clients and categories, and authenticated native Arr import
   webhooks. It tests connections without printing credentials. Re-run it
   after relevant `.env` changes.
4. In Prowlarr, add only authorized indexers. Verify they return the categories
   required by each application: movies (`2000`) and TV (`5000`).
5. Set `AUTOMATION_PROVIDER=arr` and recreate the application services:

   ```bash
   docker compose --env-file .env --profile media up -d --build api worker
   ```

6. In Jellyfin, create movie and TV libraries for `/media/movies` and
   `/media/tv`. Create an API key and set `JELLYFIN_API_KEY`, then recreate the
   worker.

### 6. Set compact quality limits

The bot uses `HD-720p` for movie and TV requests. The configurator ranks 720p
first, then ordinary 1080p, then SDTV/DVD/480p/576p. Raw-HD, remux, disc, and
2160p qualities remain disabled to avoid unexpectedly large downloads. In
Radarr, set approximately 2–10 MB/minute for 720p; in Sonarr, use roughly 2–8
MB/minute.

The configurator applies `TORRENT_MINIMUM_SEEDERS` (10 by default) in Prowlarr,
Radarr, and Sonarr. A 720p result below that threshold is rejected; Arr can then
select a 1080p or lower-resolution result only when it meets the same
healthy-seeder threshold. Re-run the configurator after changing the value.

## Daily operation

### One-click Windows launcher

Run the shortcut installer once from Windows PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "\\wsl.localhost\Ubuntu-26.04\home\ryan\home_cotsakis\infra\windows\Install-HomeMediaShortcut.ps1"
```

This creates **Home Media Server** shortcuts on the Windows desktop and in the
Start Menu. Pin the Start Menu entry to the taskbar once. Each launch starts
Docker Desktop if necessary, waits for its engine and WSL integration,
recreates bind-mounted media services to prevent stale WSL mounts, and checks
container health, persistent configuration, media paths, internal service
ports, Jellyfin, and Telegram. Jellyfin opens only after every check succeeds.
The launcher log is `%LOCALAPPDATA%\HomeMedia\launcher.log`.

- Restart the complete movie/TV stack:

  ```bash
  cd /home/ryan/home_cotsakis/infra/docker
  docker compose --env-file .env --profile media restart
  ```

- After changing application code, Compose configuration, or `.env`, rebuild
  and recreate everything instead:

  ```bash
  cd /home/ryan/home_cotsakis/infra/docker
  docker compose --env-file .env --profile media up -d --build --force-recreate --remove-orphans
  ```

- Send `/movie Title` or `/tv Title`; the bot always asks for confirmation
  before submitting to an Arr service.
- Ask “what is the status of Band of Brothers?” or “what is downloading?” for
  owner-scoped request state and live qBittorrent percentages, resolution, and
  total size. `/status Title` and `/downloads` are non-AI fallbacks when Gemini
  is unavailable.
- Use `/stop Title` to create an owner-bound confirmation that unmonitors the
  item, removes it from the Arr queue, stops/removes its qBittorrent torrent and
  partial files, and verifies both queues are clear.
- Say “stop seeding Band of Brothers” after an import to get a separate
  confirmation that removes the completed torrent and its download-folder
  hard links while retaining the imported Jellyfin library files. Short
  follow-ups such as “stop seeding it” use the bot's bounded chat history.
- Ask “delete Arrival from my movie library” (or use `/delete movie Arrival`).
  The bot resolves the exact Arr library item and requires a separate **Delete
  files** confirmation. The destructive boundary first performs the same
  stop-and-verify procedure; library deletion is refused if a torrent remains.
- Check health with `docker compose --env-file .env --profile media ps`.
- Follow application logs with
  `docker compose --env-file .env --profile media logs -f api worker`.
- Stop without deleting state with
  `docker compose --env-file .env --profile media down`.
- Re-run `python3 configure_services.py --apply` after changing service
  credentials, paths, or connection settings.

An Arr grab webhook sends a one-time “download started” notification including
the selected release's resolution and estimated total size when supplied. A
completed-import webhook marks the request imported; the worker asks Jellyfin
to refresh, sends the Telegram ready notification, and records delivery so
retries do not notify twice.

## Optional downloader VPN

The default deployment uses the host's route. For the dedicated fail-closed
Gluetun path, set `NORDVPN_PRIVATE_KEY` and run:

```bash
docker compose --env-file .env \
  -f docker-compose.yml -f docker-compose.vpn.yml \
  --profile media --profile vpn up -d --build
```

This places qBittorrent in Gluetun's network namespace. Meshnet remains the
private route for management and playback.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Bot does not answer | Verify the bot token and numeric allowlist, inspect `worker` logs, and ensure Telegram has no webhook configured. |
| Worker times out only on `api.telegram.org` | Test the configured `TELEGRAM_API_IP`; the Compose worker pins that address while preserving normal TLS hostname verification. Update it if Telegram changes its reachable Bot API address. |
| Confirmation succeeds but no download starts | Verify `AUTOMATION_PROVIDER=arr`, run the service configurator, then test Prowlarr's app and indexer connections. |
| AI says it is temporarily unavailable | The message means the worker could not reach Gemini, not that Radarr/Sonarr are down. Verify host/Docker DNS and outbound networking, then test again; `/status`, `/downloads`, `/movie`, `/tv`, and `/delete` remain available without Gemini. |
| TV confirmation fails | Inspect `api`, `worker`, and `sonarr` logs. The adapter sends Sonarr's canonical lookup record, including its required title, and posts configuration errors into the Telegram chat. |
| Sonarr has no indexers | Add and sync an authorized Prowlarr indexer supporting TV categories. |
| Arr webhook is unhealthy | Re-run `python3 configure_services.py --apply`; its connection tests verify the internal API URL and authentication header. |
| Jellyfin refresh logs `rejected its API key` | Create a fresh key in Jellyfin's dashboard, update `JELLYFIN_API_KEY`, and recreate `worker`. The stale key does not block ready notifications. |
| Download completed but media is absent | Inspect the Arr import history, then `/srv/media/downloads` and the appropriate `/srv/media/library` directory. |
| Download and library both appear to use space | Compare inode/link counts with `stat`; correctly imported files have the same inode and a link count of at least 2, so the apparent copies do not consume duplicate blocks. |

## More documentation

- [Infrastructure and security runbook](docs/infrastructure.md)
- [API development notes](apps/api/README.md)
- [Changes applied to this workspace deployment](docs/deployment-2026-10-02.md)
