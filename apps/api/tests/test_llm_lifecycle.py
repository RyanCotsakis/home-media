from app.models import MediaType
from app.services.llm import GeminiIntentProvider, MediaIntent


def test_gemini_provider_returns_typed_stop_seeding_action(monkeypatch) -> None:
    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, object]:
            return {"candidates": [{"content": {"parts": [{"text": (
                '{"message":"I can stop that after confirmation.",'
                '"title":null,"media_type":null,"year":null,'
                '"delete_title":null,"delete_media_type":null,"delete_year":null,'
                '"stop_title":"Arrival","stop_media_type":"movie","stop_year":2016,'
                '"stop_mode":"seeding","read_action":null}'
            )}]}}]}

    monkeypatch.setattr("app.services.llm.httpx.post", lambda *args, **kwargs: Response())
    reply = GeminiIntentProvider("test-key").reply("stop seeding it", [])

    assert reply.stop_intent == MediaIntent("Arrival", MediaType.MOVIE, 2016)
    assert reply.stop_mode == "seeding"
