import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from .adapter import WebGameAdapter


STATIC_DIR = Path(__file__).resolve().parent / "static"


class StoryEngineHTTPServer(ThreadingHTTPServer):
    # Avoid "Address already in use" after recent shutdowns (TIME_WAIT), and
    # make restarts in dev loops reliable.
    allow_reuse_address = True

    def __init__(self, server_address, RequestHandlerClass, adapter: WebGameAdapter):
        super().__init__(server_address, RequestHandlerClass)
        self.adapter = adapter


class StoryEngineRequestHandler(BaseHTTPRequestHandler):
    server: StoryEngineHTTPServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._serve_static("index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/assets/style.css":
            self._serve_static("style.css", "text/css; charset=utf-8")
            return
        if parsed.path == "/assets/app.js":
            self._serve_static("app.js", "application/javascript; charset=utf-8")
            return
        if parsed.path == "/api/state":
            self._send_json(self.server.adapter.get_state())
            return
        self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        payload = self._read_json_body()

        if parsed.path == "/api/step":
            result = self.server.adapter.submit_turn(
                command=str(payload.get("command", "")),
                inject_event=str(payload.get("inject_event", "")),
            )
            self._send_json(result)
            return

        if parsed.path == "/api/reset":
            self._send_json(self.server.adapter.reset())
            return

        if parsed.path == "/api/retry-delivery":
            self._send_json(self.server.adapter.retry_delivery())
            return

        self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json_body(self) -> Dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            return {}
        body = self.rfile.read(content_length)
        if not body:
            return {}
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _serve_static(self, filename: str, content_type: str) -> None:
        path = STATIC_DIR / filename
        if not path.exists():
            self._send_json({"error": "Missing static asset"}, status=HTTPStatus.NOT_FOUND)
            return

        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: Dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def run_server(adapter: WebGameAdapter, host: str = "127.0.0.1", port: int = 8000) -> StoryEngineHTTPServer:
    try:
        server = StoryEngineHTTPServer((host, port), StoryEngineRequestHandler, adapter=adapter)
    except OSError as e:
        if getattr(e, "errno", None) == 98:
            # If the requested port is busy, fall back to an ephemeral free port
            # so the dev server can still start without manual intervention.
            server = StoryEngineHTTPServer((host, 0), StoryEngineRequestHandler, adapter=adapter)
            actual_port = int(server.server_address[1])
            print(
                f"Port {port} is already in use on {host}; "
                f"falling back to a free port {actual_port}.",
                flush=True,
            )
        else:
            raise

    actual_port = int(server.server_address[1])
    print(f"Story Engine Web UI running at http://{host}:{actual_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return server
