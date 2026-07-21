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
- `POST /v1/automation/events/imported`, authenticated with
  `X-Automation-Token`, marks a submitted request ready for notification.

The current `mock` automation provider is deliberate. It provides an
end-to-end test seam before Radarr/Sonarr credentials are introduced.
