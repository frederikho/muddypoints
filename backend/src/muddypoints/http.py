"""Minimal HTTP layer for Lambda Function URL events (payload format 2.0)."""
import base64
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import parse_qs


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def not_found(what: str = "Resource", ident=None) -> ApiError:
    """'Board 'abc123' not found' - naming the id helps API/MCP callers see which reference was wrong."""
    return ApiError(404, "not_found", f"{what} '{ident}' not found" if ident else f"{what} not found")


def forbidden(message: str = "You do not have permission to do this") -> ApiError:
    return ApiError(403, "forbidden", message)


@dataclass
class Request:
    method: str
    path: str
    headers: dict[str, str]
    query: dict[str, str]
    raw_body: bytes
    path_params: dict[str, str] = field(default_factory=dict)
    source_ip: str = ""
    _json: Any = None

    @classmethod
    def from_event(cls, event: dict) -> "Request":
        http = event.get("requestContext", {}).get("http", {})
        body = event.get("body") or ""
        raw = base64.b64decode(body) if event.get("isBase64Encoded") else body.encode()
        query = {k: v[-1] for k, v in parse_qs(event.get("rawQueryString", "")).items()}
        return cls(
            method=(http.get("method") or "GET").upper(),
            path=event.get("rawPath") or http.get("path") or "/",
            headers={k.lower(): v for k, v in (event.get("headers") or {}).items()},
            query=query,
            raw_body=raw,
            source_ip=http.get("sourceIp", ""),
        )

    def json(self) -> dict:
        if self._json is None:
            if not self.raw_body:
                self._json = {}
            else:
                try:
                    self._json = json.loads(self.raw_body)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    raise ApiError(400, "invalid_json", "Request body is not valid JSON")
            if not isinstance(self._json, (dict, list)):
                raise ApiError(400, "invalid_json", "Request body must be a JSON object")
        return self._json

    @property
    def bearer(self) -> str:
        value = self.headers.get("authorization", "")
        return value[7:].strip() if value.lower().startswith("bearer ") else ""


def json_response(status: int, body: Any, headers: dict | None = None) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", **(headers or {})},
        "body": json.dumps(body, default=str, ensure_ascii=False) if body is not None else "",
    }


def error_response(err: ApiError) -> dict:
    return json_response(err.status, {"error": {"code": err.code, "message": err.message}})


Handler = Callable[[Request], Any]


class Router:
    def __init__(self):
        self.routes: list[tuple[str, re.Pattern, Handler]] = []

    def route(self, method: str, pattern: str):
        regex = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern.rstrip("/") or "/") + "/?$")

        def decorator(fn: Handler) -> Handler:
            self.routes.append((method.upper(), regex, fn))
            return fn
        return decorator

    def resolve(self, req: Request) -> Handler:
        path_matched = False
        for method, regex, fn in self.routes:
            m = regex.match(req.path)
            if not m:
                continue
            path_matched = True
            if method == req.method:
                req.path_params = m.groupdict()
                return fn
        if path_matched:
            raise ApiError(405, "method_not_allowed", f"{req.method} is not allowed on {req.path}")
        raise ApiError(404, "not_found", f"No route for {req.method} {req.path}")
