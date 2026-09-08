"""knowledge/fetch.py: one guarded HTTP path for every knowledge source.
Every failure mode (network, rate limit, wrong content) becomes a named
FetchError instead of an uncaught urllib/json exception (A1)."""
import urllib.error

import pytest

from knowledge import fetch as kf


class FakeResp:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_json_uses_the_opener():
    calls = []

    def opener(url, headers=None):
        calls.append(url)
        return FakeResp(b'{"a": 1}')

    assert kf.fetch_json("https://x/y.json", opener=opener) == {"a": 1}
    assert calls == ["https://x/y.json"]


def test_fetch_text_uses_the_opener_and_decodes():
    def opener(url, headers=None):
        return FakeResp("café".encode("utf-8"))

    assert kf.fetch_text("https://x/y.txt", opener=opener) == "café"


def test_open_url_sends_the_user_agent_and_a_60s_timeout(monkeypatch):
    """open_url is the default opener; a fake urlopen/Request pair captures
    what it would have sent."""
    captured = {}

    class FakeRequest:
        def __init__(self, url, headers=None):
            self.url = url
            captured["url"], captured["headers"] = url, headers

    def fake_urlopen(req, timeout=None):
        captured["timeout"] = timeout
        return FakeResp(b"ok")

    monkeypatch.setattr(kf.urllib.request, "Request", FakeRequest)
    monkeypatch.setattr(kf.urllib.request, "urlopen", fake_urlopen)
    with kf.open_url("https://x/y") as resp:
        assert resp.read() == b"ok"
    assert captured["url"] == "https://x/y"
    assert captured["headers"]["User-Agent"] == kf.USER_AGENT
    assert captured["timeout"] == 60


def test_http_error_yields_fetch_error_naming_the_status():
    def opener(url, headers=None):
        raise urllib.error.HTTPError(url, 429, "rate", {}, None)

    with pytest.raises(kf.FetchError) as exc:
        kf.fetch_json("https://x/y.json", opener=opener)
    assert "429" in str(exc.value)


def test_non_json_body_yields_fetch_error_naming_not_json():
    def opener(url, headers=None):
        return FakeResp(b"<html>")

    with pytest.raises(kf.FetchError) as exc:
        kf.fetch_json("https://x/y.json", opener=opener)
    assert "not JSON" in str(exc.value)


def test_url_error_yields_fetch_error():
    def opener(url, headers=None):
        raise urllib.error.URLError("no route to host")

    with pytest.raises(kf.FetchError) as exc:
        kf.fetch_text("https://x/y", opener=opener)
    assert "https://x/y" in str(exc.value)
