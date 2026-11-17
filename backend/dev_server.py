"""Local development server: serves frontend/ and the API (/api, /mcp) on one port.

    python backend/dev_server.py            # in-memory AWS (moto) — no AWS account needed
    python backend/dev_server.py --aws      # use real AWS resources from TABLE_NAME / BUCKET_NAME env vars

Open http://localhost:8787. Emails (password reset links!) are printed to the console.
Data in mock mode is lost when the server stops.
"""
import argparse
import base64
import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

CORS = {"access-control-allow-origin": "*", "access-control-allow-headers": "*",
        "access-control-allow-methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS"}


def start_mock_aws(port: int) -> None:
    from moto.server import ThreadedMotoServer
    os.environ.update({"AWS_ACCESS_KEY_ID": "dev", "AWS_SECRET_ACCESS_KEY": "dev", "AWS_REGION": "eu-central-1",
                       "AWS_ENDPOINT_URL": f"http://localhost:{port}"})
    ThreadedMotoServer(port=port, verbose=False).start()
    from padlet import aws
    from padlet.localinfra import create_resources
    aws.reset()
    create_resources()
    print(f"moto (fake AWS) running on :{port}")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # the app logs requests itself as JSON
        pass

    def _send(self, status: int, headers: dict, body: bytes) -> None:
        self.send_response(status)
        for k, v in {**CORS, **headers}.items():
            self.send_header(k, v)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, {}, b"")

    def _api(self):
        from padlet.handler import lambda_handler
        url = urlsplit(self.path)
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        event = {"rawPath": url.path, "rawQueryString": url.query, "headers": dict(self.headers.items()),
                 "requestContext": {"http": {"method": self.command, "path": url.path,
                                             "sourceIp": self.client_address[0]}},
                 "body": base64.b64encode(raw).decode(), "isBase64Encoded": True}
        resp = lambda_handler(event, None)
        self._send(resp["statusCode"], resp.get("headers", {}), (resp.get("body") or "").encode())

    def _static(self):
        path = urlsplit(self.path).path
        target = (FRONTEND / path.lstrip("/")).resolve()
        if target.is_dir():
            target = target / "index.html"
        if not str(target).startswith(str(FRONTEND)) or not target.is_file():
            return self._send(404, {"content-type": "text/plain"}, b"not found")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(200, {"content-type": ctype, "cache-control": "no-store"}, target.read_bytes())

    def _route(self):
        path = urlsplit(self.path).path
        if path.startswith("/api/") or path == "/mcp" or path.startswith("/mcp/"):
            self._api()
        elif self.command == "GET":
            self._static()
        else:
            self._send(405, {}, b"")

    do_GET = do_POST = do_PATCH = do_DELETE = do_PUT = _route


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--moto-port", type=int, default=5001)
    parser.add_argument("--aws", action="store_true", help="use real AWS instead of the in-memory mock")
    args = parser.parse_args()
    os.environ.setdefault("FRONTEND_URL", f"http://localhost:{args.port}")
    os.environ.setdefault("LOG_LEVEL", "INFO")
    if not args.aws:
        start_mock_aws(args.moto_port)
    print(f"Padlet dev server on http://localhost:{args.port}  (Ctrl+C to stop)")
    print(f"MCP endpoint: http://localhost:{args.port}/mcp/<api key>")
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
