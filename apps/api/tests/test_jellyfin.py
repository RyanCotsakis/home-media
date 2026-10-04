from app.services.jellyfin import refresh_library


def test_refresh_library_is_skipped_without_api_key(monkeypatch) -> None:
    monkeypatch.setattr("app.services.jellyfin.settings.jellyfin_api_key", None)
    monkeypatch.setattr(
        "app.services.jellyfin.httpx.post",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not call Jellyfin")),
    )

    assert refresh_library() is False


def test_refresh_library_uses_server_side_token(monkeypatch) -> None:
    captured = {}

    class Response:
        status_code = 204

        def raise_for_status(self) -> None:
            pass

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr("app.services.jellyfin.settings.jellyfin_api_key", "secret")
    monkeypatch.setattr("app.services.jellyfin.settings.jellyfin_url", "http://jellyfin:8096")
    monkeypatch.setattr("app.services.jellyfin.httpx.post", fake_post)

    assert refresh_library() is True

    assert captured["url"] == "http://jellyfin:8096/Library/Refresh"
    assert captured["headers"] == {"X-Emby-Token": "secret"}


def test_refresh_library_does_not_block_on_stale_key(monkeypatch) -> None:
    class Response:
        status_code = 401

        def raise_for_status(self) -> None:
            raise AssertionError("authorization failures should be handled")

    monkeypatch.setattr("app.services.jellyfin.settings.jellyfin_api_key", "stale")
    monkeypatch.setattr("app.services.jellyfin.httpx.post", lambda *args, **kwargs: Response())

    assert refresh_library() is False
