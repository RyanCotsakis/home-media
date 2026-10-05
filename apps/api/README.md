## Home Media API

This service owns household authorization and the durable media-request workflow.
It is not a public internet API: Telegram is polled by the worker and media
automation webhooks travel only on the internal Docker network.

### Local development

Create an environment file with `DATABASE_URL` (SQLite is the safe default),
then run `uv sync --group dev` and `uv run pytest`. Start the service with
`uv run uvicorn app.main:app --reload`.

The bot accepts ordinary conversational messages when `LLM_PROVIDER=gemini`
and `GEMINI_API_KEY` is configured. Set `GEMINI_GOOGLE_SEARCH_ENABLED=true` only when
the Gemini project has Google Search grounding quota for current
recommendations and US streaming availability, keeps a bounded per-chat
transcript in the database, and still requires a Telegram confirmation before
any automation request is submitted. Without a Gemini key, `/movie Title` and
`/tv Title` remain the safe fallback commands.

The initial safe request API is:

- `POST /v1/telegram/updates` with `/movie Title` or `/tv Title`; only IDs in
  `TELEGRAM_ALLOWED_USER_IDS` can create a pending request.
- `POST /v1/requests/{id}/confirm` with the owner’s confirmation token; this
  is the only point at which the automation adapter may submit a request.
- `POST /v1/automation/events/arr` accepts native Radarr and Sonarr
  grab and completed-import webhooks authenticated with `X-Automation-Token`.
- `POST /v1/automation/events/imported` remains the small explicit event
  contract for custom integrations.

The `mock` automation provider provides a test seam before Arr credentials are
introduced. Production uses `AUTOMATION_PROVIDER=arr`; run
`infra/docker/configure_services.py --apply` after each service's first-run
setup to create the owned connections and webhooks.

Natural-language status requests can read owner-scoped request stages and
active qBittorrent progress, resolution, and size. `/status Title` and
`/downloads` provide the same core checks without an LLM. `/stop Title`
requires owner confirmation, unmonitors the item, removes the Arr queue entry,
stops/removes the qBittorrent item and partial data, and verifies both queues.
Natural-language requests to stop seeding use a distinct confirmation: they
remove only completed qBittorrent items and download-folder links, preserving
the imported Arr/Jellyfin library item. Outbound lifecycle notifications,
confirmation results, and failures are stored in the bounded transcript so
follow-ups can refer to the item the bot just mentioned.
Natural-language deletion (or `/delete movie|tv Title`) resolves an existing
Arr library item and always requires a separate owner-bound confirmation; the
same stop-and-verify boundary must succeed before library files are removed.
