"""Minimal HTTP API that exposes facebook-scraper functionality."""
from __future__ import annotations

import json
import logging
import os
from http import HTTPStatus
from io import BytesIO
from typing import Any, Dict, List, MutableMapping, Optional, Tuple
from wsgiref.simple_server import make_server

from . import exceptions, get_posts

LOGGER = logging.getLogger(__name__)
DEFAULT_LIMIT = 10
MAX_LIMIT = 100

OPENAPI_SPEC: Dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {
        "title": "Facebook Scraper API",
        "version": "1.0.0",
        "description": (
            "Expose the existing scraping helpers over HTTP so they can be "
            "invoked from hosted environments such as Railway."
        ),
    },
    "paths": {
        "/health": {
            "get": {
                "summary": "Health check",
                "responses": {
                    "200": {
                        "description": "API is ready",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"status": {"type": "string"}},
                                    "required": ["status"],
                                }
                            }
                        },
                    }
                },
            }
        },
        "/posts": {
            "post": {
                "summary": "Scrape posts from Facebook",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ScrapePostsRequest"}
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Scraped posts",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ScrapePostsResponse"}
                            }
                        },
                    },
                    "400": {"description": "Invalid payload"},
                    "401": {"description": "Invalid cookies"},
                },
            }
        },
    },
    "components": {
        "schemas": {
            "ScrapePostsRequest": {
                "type": "object",
                "properties": {
                    "account": {"type": "string", "description": "Page or profile"},
                    "group": {"type": "string", "description": "Group ID"},
                    "post_urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Explicit list of post URLs",
                    },
                    "hashtag": {"type": "string", "description": "Hashtag to search"},
                    "pages": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "How many paginator pages to visit",
                    },
                    "page_limit": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Legacy alias for pages",
                    },
                    "timeout": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Request timeout in seconds",
                    },
                    "options": {
                        "type": "object",
                        "description": "Options passed through to get_posts",
                    },
                    "cookies": {
                        "description": "Cookie mapping or path to a cookies file",
                        "oneOf": [
                            {"type": "string"},
                            {"type": "object", "additionalProperties": {"type": "string"}},
                        ],
                    },
                    "extra_info": {"type": "boolean"},
                    "youtube_dl": {"type": "boolean"},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_LIMIT,
                        "default": DEFAULT_LIMIT,
                        "description": "Maximum number of posts to return",
                    },
                },
                "description": (
                    "Exactly one of account, group, post_urls, or hashtag must be provided."
                ),
            },
            "ScrapePostsResponse": {
                "type": "object",
                "properties": {
                    "count": {"type": "integer"},
                    "posts": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["count", "posts"],
            },
        }
    },
}

SWAGGER_HTML = """<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"utf-8\" />
    <title>Facebook Scraper API</title>
    <link rel=\"stylesheet\" href=\"https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css\" />
    <style>body { margin: 0; background: #f7f7f7; }</style>
</head>
<body>
<div id=\"swagger-ui\"></div>
<script src=\"https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js\"></script>
<script>
window.onload = () => {
  SwaggerUIBundle({ url: '/openapi.json', dom_id: '#swagger-ui' });
};
</script>
</body>
</html>
"""

CORS_HEADERS = (
    ("Access-Control-Allow-Origin", "*"),
    ("Access-Control-Allow-Methods", "GET,POST,OPTIONS"),
    ("Access-Control-Allow-Headers", "Content-Type"),
)


class APIError(Exception):
    """Exception raised for invalid client payloads."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def app(environ: MutableMapping[str, Any], start_response):
    method = (environ.get("REQUEST_METHOD") or "GET").upper()
    path = environ.get("PATH_INFO") or "/"

    if method == "OPTIONS" and path == "/posts":
        return _options_response(start_response)

    if method == "GET" and path == "/":
        return _json_response(start_response, HTTPStatus.OK, {"message": "Facebook Scraper API", "docs": "/docs"})
    if method == "GET" and path == "/health":
        return _json_response(start_response, HTTPStatus.OK, {"status": "ok"})
    if method == "GET" and path == "/openapi.json":
        return _json_response(start_response, HTTPStatus.OK, OPENAPI_SPEC)
    if method == "GET" and path == "/docs":
        return _html_response(start_response, SWAGGER_HTML)
    if method == "POST" and path == "/posts":
        return _handle_posts(environ, start_response)

    return _json_response(start_response, HTTPStatus.NOT_FOUND, {"detail": "Not Found"})


def _options_response(start_response):
    headers = list(CORS_HEADERS)
    start_response(_status_line(HTTPStatus.NO_CONTENT), headers)
    return [b""]


def _handle_posts(environ, start_response):
    if not _is_json(environ.get("CONTENT_TYPE")):
        return _json_response(
            start_response,
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            {"detail": "Content-Type must be application/json"},
        )

    try:
        payload = _load_json_body(environ)
        posts = _collect_posts(payload)
    except APIError as exc:
        return _json_response(start_response, HTTPStatus(exc.status_code), {"detail": exc.detail})
    except exceptions.InvalidCookies as exc:
        return _json_response(start_response, HTTPStatus.UNAUTHORIZED, {"detail": str(exc)})
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.exception("Unhandled error while scraping posts")
        return _json_response(start_response, HTTPStatus.BAD_GATEWAY, {"detail": "Failed to fetch posts"})

    return _json_response(start_response, HTTPStatus.OK, {"count": len(posts), "posts": posts})


def _collect_posts(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    target_field, target_value = _extract_target(payload)
    request_kwargs = _extract_request_kwargs(payload)
    limit = _coerce_int(payload.get("limit", DEFAULT_LIMIT), "limit", minimum=1, maximum=MAX_LIMIT)

    iterator = get_posts(**{target_field: target_value}, **request_kwargs)
    results: List[Dict[str, Any]] = []
    for post in iterator:
        results.append(post)
        if len(results) >= limit:
            break
    return results


def _extract_target(payload: Dict[str, Any]) -> Tuple[str, Any]:
    candidates = []
    for field in ("account", "group", "post_urls", "hashtag"):
        value = payload.get(field)
        if value not in (None, "", []):
            candidates.append((field, value))
    if len(candidates) != 1:
        raise APIError(400, "Exactly one of account, group, post_urls, or hashtag is required")

    field, value = candidates[0]
    if field == "post_urls":
        if not isinstance(value, list) or not value:
            raise APIError(400, "post_urls must be a non-empty list of URLs")
    return field, value


def _extract_request_kwargs(payload: Dict[str, Any]) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {}
    for key in ("pages", "page_limit", "timeout"):
        if key in payload:
            kwargs[key] = _coerce_int(payload[key], key, minimum=1)

    options = payload.get("options")
    if options is not None:
        if not isinstance(options, dict):
            raise APIError(400, "options must be an object")
        kwargs["options"] = options

    cookies = payload.get("cookies")
    if cookies is not None and not isinstance(cookies, (dict, str)):
        raise APIError(400, "cookies must be a string path or name/value mapping")
    if cookies is not None:
        kwargs["cookies"] = cookies

    for boolean_key in ("extra_info", "youtube_dl"):
        if boolean_key in payload:
            kwargs[boolean_key] = bool(payload[boolean_key])

    return kwargs


def _is_json(content_type: Optional[str]) -> bool:
    if not content_type:
        return False
    return "application/json" in content_type.lower()


def _load_json_body(environ: MutableMapping[str, Any]) -> Dict[str, Any]:
    try:
        length = int(environ.get("CONTENT_LENGTH") or "0")
    except (ValueError, TypeError):
        length = 0
    body_stream = environ.get("wsgi.input")
    if body_stream is None:
        body_stream = BytesIO()
    raw = body_stream.read(length) if length else body_stream.read()
    if not raw:
        raise APIError(400, "Request body is required")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise APIError(400, f"Invalid JSON payload: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise APIError(400, "JSON payload must be an object")
    return payload


def _coerce_int(value: Any, field: str, *, minimum: int = 1, maximum: Optional[int] = None) -> int:
    if isinstance(value, bool):  # bools are ints in Python
        raise APIError(400, f"{field} must be an integer")
    try:
        value_int = int(value)
    except (TypeError, ValueError):
        raise APIError(400, f"{field} must be an integer") from None
    if value_int < minimum:
        raise APIError(400, f"{field} must be >= {minimum}")
    if maximum is not None and value_int > maximum:
        raise APIError(400, f"{field} must be <= {maximum}")
    return value_int


def _json_response(start_response, status: HTTPStatus, payload: Dict[str, Any]):
    body = json.dumps(payload).encode("utf-8")
    headers = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
    ] + list(CORS_HEADERS)
    start_response(_status_line(status), headers)
    return [body]


def _html_response(start_response, html: str):
    body = html.encode("utf-8")
    headers = [
        ("Content-Type", "text/html; charset=utf-8"),
        ("Content-Length", str(len(body))),
    ]
    start_response(_status_line(HTTPStatus.OK), headers)
    return [body]


def _status_line(status: HTTPStatus) -> str:
    return f"{status.value} {status.phrase}"


def run() -> None:
    """Start a simple WSGI server for local development."""

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    with make_server(host, port, app) as server:
        LOGGER.info("Serving Facebook Scraper API on %s:%s", host, port)
        server.serve_forever()


if __name__ == "__main__":
    run()
