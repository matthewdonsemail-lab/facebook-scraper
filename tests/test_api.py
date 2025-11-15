import json
import sys
from io import BytesIO

from facebook_scraper import api


def _invoke(path, method="GET", body=None, content_type="application/json"):
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "SERVER_NAME": "testserver",
        "SERVER_PORT": "80",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "http",
        "wsgi.errors": sys.stderr,
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
    }
    raw_body = body or b""
    environ["wsgi.input"] = BytesIO(raw_body)
    environ["CONTENT_LENGTH"] = str(len(raw_body))
    if body is not None:
        environ["CONTENT_TYPE"] = content_type

    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    response_body = b"".join(api.app(environ, start_response))
    return captured["status"], captured["headers"], response_body


def test_health_endpoint_returns_ok():
    status, _, body = _invoke("/health")
    assert status.startswith("200")
    assert json.loads(body) == {"status": "ok"}


def test_posts_endpoint_limits_results(monkeypatch):
    def fake_posts(**_):
        yield {"post_id": "1", "text": "hello"}
        yield {"post_id": "2", "text": "world"}
        yield {"post_id": "3", "text": "ignored"}

    monkeypatch.setattr(api, "get_posts", lambda **kwargs: fake_posts(**kwargs))

    payload = json.dumps({"account": "nintendo", "limit": 2}).encode("utf-8")
    status, _, body = _invoke("/posts", method="POST", body=payload)
    assert status.startswith("200")
    data = json.loads(body)
    assert data["count"] == 2
    assert [post["post_id"] for post in data["posts"]] == ["1", "2"]


def test_posts_endpoint_requires_single_target():
    payload = json.dumps({"account": "a", "group": "b"}).encode("utf-8")
    status, _, body = _invoke("/posts", method="POST", body=payload)
    assert status.startswith("400")
    assert "Exactly one" in json.loads(body)["detail"]


def test_invalid_cookies_surface_as_unauthorized(monkeypatch):
    def fake_posts(**_):
        raise api.exceptions.InvalidCookies("bad cookies")

    monkeypatch.setattr(api, "get_posts", lambda **kwargs: fake_posts(**kwargs))

    payload = json.dumps({"account": "nintendo"}).encode("utf-8")
    status, _, body = _invoke("/posts", method="POST", body=payload)
    assert status.startswith("401")
    assert "bad cookies" in json.loads(body)["detail"]
