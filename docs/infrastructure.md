# Home Media Infrastructure

## Purpose and topology

The production target is Ubuntu Server with Docker Engine. The server joins
NordVPN Meshnet; household iPhones also join Meshnet and use the server's
Meshnet hostname in the Jellyfin iOS app. There is no router port-forward.

```text
Telegram (long polling) -> worker/API -> Postgres + Redis
                                  |          |
                                  v          v
                         Radarr / Sonarr <- Prowlarr
                                  |
                 qBittorrent (host VPN route by default)
                                  |
                       /data/library -> Jellyfin -> Meshnet iPhones
```

The API receives only authenticated internal automation events. The worker is
the boundary for Telegram polling, delivery, retries, and slow integrations.
The LLM is a replaceable conversational provider, not an infrastructure
administrator. It may use Gemini Google Search grounding for current media information and
may propose a title, but application code alone resolves that proposal and
creates a pending request. It has no credentials or direct path to automation,
download, or infrastructure services. A bounded Telegram transcript is stored
in Postgres per chat for follow-up questions.

## Service boundaries

| Service | Responsibility | Network exposure |
| --- | --- | --- |
| API and worker | authorization, request state, tool validation, notifications | internal Docker networks only |
| Postgres / Redis | durable state / queued work | internal only |
| Prowlarr | indexer configuration for automation services | loopback-only management port |
| Radarr / Sonarr | movie/TV lookup, monitored request, import, webhook | loopback-only management port |
| qBittorrent | download client using the host VPN route by default | loopback-only management port |
| Gluetun (optional) | dedicated fail-closed downloader VPN | qBittorrent UI loopback-only through Gluetun |
| Jellyfin | library scan and iPhone playback | port 8096; firewall admits Meshnet only |

The `app` and `media` Docker networks are ordinary Docker bridge networks so
the worker can reach Telegram/Gemini and media services can reach their allowed
upstream services. Exposure is controlled by published ports: Docker publishes
no database, Redis, or downloader port. Management ports bind to `127.0.0.1`
and are accessed only from the server or via an SSH tunnel over Meshnet.

## Request lifecycle

1. An allowlisted Telegram user sends a conversational message or `/movie Title` / `/tv Title`.
2. The LLM may answer directly or propose one normalized title; the application validates the user and resolves it through the
   automation boundary, and stores `pending_confirmation` with an opaque token.
3. The Telegram adapter presents the title/type/year and confirmation button.
   Only its original requester can confirm the token.
4. Confirmation submits to the configured Radarr/Sonarr adapter and becomes
   `submitted`; Prowlarr and qBittorrent remain behind that adapter.
5. An authenticated import webhook marks it `imported`, queues a notification,
   triggers/awaits the Jellyfin scan in the live adapter, and then marks it
   `notified`. Notification delivery must be idempotent.

`failed` records a safe error message rather than retrying unknown acquisition
actions. The first code milestone uses a mock automation adapter so this whole
state machine is testable without live indexers or credentials.

Telegram identifiers are signed 64-bit values. Database migrations must retain
`BIGINT` types for requester and chat identifiers; using PostgreSQL `INTEGER`
will reject many valid Telegram accounts and chats.

## Data, secrets, and backups

- Mount `${MEDIA_ROOT}` consistently into Radarr, Sonarr, qBittorrent, and
  Jellyfin. Downloads use `/data/downloads`; final media uses `/data/library`.
  Matching paths prevent slow copies and broken imports.
- Mount `${CONFIG_ROOT}` for each media service. Back up it plus the Postgres
  database; media files need a separate storage/retention strategy.
- Copy `infra/docker/.env.example` to `.env`, use long random secrets, and keep
  it out of Git. Never put Telegram, Gemini, NordVPN, indexer, or tracker
  credentials in Compose or documentation.
- Back up Postgres with `docker compose exec -T postgres pg_dump -U
  "$POSTGRES_USER" "$POSTGRES_DB"`, encrypt it, and test restoring it to an
  isolated machine. Restore service config and database together.

## Ubuntu production runbook

1. Install Docker Engine and Compose plugin; create the configured storage
   directories owned by the selected `PUID:PGID` user.
2. Install and log into NordVPN on the host, enable Meshnet, authorize the two
   iPhones, and record the server Meshnet hostname. NordVPN on the host is for
   remote access; Gluetun is separately responsible for downloader egress.
3. Create `infra/docker/.env` from the example. Set real service tokens and
   initially leave `AUTOMATION_PROVIDER=mock` until Radarr/Sonarr configuration
   is complete.
4. Start the complete production stack with `docker compose --env-file .env
   --profile media up -d --build` from `infra/docker`. Use `docker compose ps`
   and API `/healthz`/`/readyz` through an internal diagnostic command to verify
   health. The development override exposes the API only on loopback and does
   not start media services.
5. Use an SSH tunnel over Meshnet for each service's first-run setup, place the
   three Arr API keys and qBittorrent credentials in `.env`, then run
   `python3 configure_services.py --apply` from `infra/docker`. This configures
   the shared `/data` roots, Prowlarr applications, qBittorrent clients, and
   native Arr import webhooks with `X-Automation-Token`. It is idempotent and
   safe to re-run after a credential or connection change. Add only indexers
   and download sources you are authorized to use; the setup tool deliberately
   does not select them.

### Compact movie and TV downloads

New movie and TV requests use the `HD-720p` quality profile by default
(`RADARR_QUALITY_PROFILE` and `SONARR_QUALITY_PROFILE`). That profile allows
only 720p sources, so it avoids automatic upgrades to 1080p or 4K. Do not
rename it unless the matching environment setting is updated too.

In Radarr, set the maximum sizes for the allowed 720p quality definitions to
about **10 MB/minute** (with a 2 MB/minute minimum). For a two-hour film this
tops out at roughly 1.2 GB, keeping the usual target around 1 GB while leaving
room for slightly longer films. In Sonarr, use a 2--8 MB/minute range for the
allowed 720p definitions; this keeps ordinary episodes compact.

For each torrent indexer synced from Prowlarr, set **Minimum Seeders** to
**10** in the indexer settings and re-sync it to Radarr and Sonarr. This makes
release selection favour well-seeded torrents; lower the threshold temporarily
only when an older or rare title has no result. These controls are per-indexer,
so Prowlarr must apply them to every torrent indexer rather than to a single
global setting.

### Chat read-only database role

Create a separate login for chatbot queries; do not reuse the application owner:

```sql
CREATE ROLE chat_reader LOGIN PASSWORD 'replace-me';
GRANT CONNECT ON DATABASE home_media TO chat_reader;
GRANT USAGE ON SCHEMA public TO chat_reader;
GRANT SELECT ON TABLE media_requests, chat_messages TO chat_reader;
ALTER ROLE chat_reader SET default_transaction_read_only = on;
```

Set `CHAT_READER_DATABASE_URL` to this role's connection string. Application
code additionally validates a single SELECT, applies requester/chat scoping,
enforces a timeout and row limit, and redacts confirmation tokens before a
result is given to Gemini.

Set `JELLYFIN_API_KEY` only if the chatbot should report the library item
count and the import worker should trigger library refreshes; it is kept
server-side and never sent to Gemini.
6. Create individual Jellyfin accounts and libraries rooted at `/media`. Add
   the Meshnet hostname and port 8096 to each iPhone's Jellyfin app.

### Firewall and monitoring

Allow SSH and Jellyfin only on the NordVPN Meshnet interface; do not open port
8096 on the WAN interface. Docker's published-port rules can bypass ordinary
UFW rules, so enforce this in the `DOCKER-USER` chain or with a host firewall
rule evaluated before Docker forwarding. Test from a non-Meshnet network.

Monitor `docker compose ps`, container restart counts, disk capacity/inodes,
Gluetun health/logs, Radarr/Sonarr import failures, Redis queue depth, and
Postgres backups. Alert before library storage is full; a full filesystem can
corrupt imports and prevent notifications.

## Architecture decisions

- **Telegram long polling:** avoids a public inbound webhook endpoint and fits
  a small household bot.
- **Jellyfin:** self-hosted, Docker-friendly streaming with iPhone clients.
- **Pluggable LLM:** Gemini is the first provider, while a local model later
  implements the same intent/message contract; automation permissions stay
  unchanged.
- **Meshnet, not public exposure:** encrypted peer connectivity replaces router
  port forwarding; device authorization remains explicit in NordVPN.
- **Host VPN by default:** qBittorrent uses the server's normal outbound route,
  intended for a host-level NordVPN connection. This keeps operation simple but
  does not provide Gluetun's independent kill switch. To opt into that stronger
  isolation, start with `-f docker-compose.yml -f docker-compose.vpn.yml` and
  the `vpn` profile; then qBittorrent shares Gluetun's network namespace and
  fails closed when its tunnel is unavailable.

### VPN modes

The normal deployment does not start Gluetun and needs no NordVPN WireGuard
key in `.env`:

```bash
docker compose --env-file .env --profile media up -d --build
```

It relies on NordVPN being connected on the host. Enable NordVPN's host kill
switch if you require traffic to stop when that host tunnel drops.

For the optional dedicated downloader tunnel, set `NORDVPN_PRIVATE_KEY` and
use:

```bash
docker compose --env-file .env -f docker-compose.yml -f docker-compose.vpn.yml \
  --profile media --profile vpn up -d --build
```
