# Workspace deployment record — 2026-10-02

This records changes made to the local deployment in addition to the tracked
code changes. No credential values are included.

## Applied

- Built the current API and worker images and started the complete `media`
  Compose profile. Postgres, Redis, API, worker, Jellyfin, Prowlarr, Radarr,
  Sonarr, and qBittorrent were left running.
- Verified the API, Jellyfin, Postgres, Redis, and qBittorrent health checks.
- Verified Radarr and Sonarr have `/data/library/movies` and
  `/data/library/tv`, the `HD-720p` quality profile, qBittorrent, and a synced
  search indexer.
- Added/updated full-sync Prowlarr application connections for Radarr and
  Sonarr.
- Added/updated qBittorrent download clients using distinct `movies` and `tv`
  categories.
- Added authenticated native-import webhooks from both Arr services to
  `http://api:8000/v1/automation/events/arr`; each service's webhook test
  passed.
- Set the deployed application environment to `production`.

These connections can be reconciled again with:

```bash
cd /home/ryan/home_cotsakis/infra/docker
python3 configure_services.py --apply
```

No new movie, series, torrent, or NZB was submitted during deployment.
The five historical `Friends` confirmations remain pending so the setup did
not unexpectedly start downloading the entire series. Confirm one from
Telegram only when that acquisition is intended; a successful confirmation
will mark the duplicate historical pending rows as superseded.

The configured Jellyfin API key and the locally documented administrator
password were both rejected by Jellyfin. The worker treats this optional
refresh as non-fatal, so an Arr import can still notify Telegram and Jellyfin's
filesystem watcher can still discover it. To restore the immediate refresh,
create a fresh key in Jellyfin's dashboard, update `JELLYFIN_API_KEY`, and
recreate `worker`. The administrator password was not reset.

## Movie/TV-only cleanup — 2026-10-04

- Removed music requests from the application model, Gemini contract,
  Telegram fallback commands, read tools, webhook mapping, automation adapter,
  tests, configuration, and operator documentation.
- Removed Lidarr from Compose and the service configurator, deleted its
  Prowlarr application, and removed the empty qBittorrent `music` category.
- Deleted two duplicate, unconfirmed historical music requests. Neither had an
  automation ID, so no acquisition had been submitted.
- Removed `/srv/home-media/config/lidarr` and the verified-empty
  `/srv/media/library/music` directory. Movie and TV data was not changed.
