"""One HTTP path for every knowledge source: a browser-like User-Agent, a
60 s timeout, one request per call, no retries (the refresh prints the
failure and moves on; the user re-runs).
"""
import json
import urllib.error
import urllib.request

USER_AGENT = "Mozilla/5.0 (Macintosh) wos-bot knowledge refresh (one request per table)"


class FetchError(RuntimeError):
    """Named wrapper so the refresh can print `FetchError: <url>: <cause>`."""


def open_url(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    return urllib.request.urlopen(req, timeout=60)


def fetch_text(url, opener=None):
    try:
        with (opener or open_url)(url) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FetchError(f"{url}: {exc.__class__.__name__}: {exc}") from exc


def fetch_json(url, opener=None):
    text = fetch_text(url, opener)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise FetchError(f"{url}: not JSON ({exc.msg} at char {exc.pos}); first bytes: {text[:80]!r}") from exc
