"""A disposable local stand-in for the Supabase Storage REST API (real HTTP on 127.0.0.1; nothing leaves the machine).

It implements only what ``SupabaseChunkStore`` uses (upload with x-upsert, authenticated download, list, bulk delete), checks the
bearer key, and offers fault injection so tests can reproduce failures that are impossible to trigger on demand against the
real service: 5xx/429 bursts, dropped connections (before AND after the object was stored), delays beyond the client's timeout,
corrupted or oversized bodies, alternative duplicate/missing-object response shapes, and redirects.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

KEY = "test-service-key-" + "k" * 24


class FakeStorage:
    """``objects`` maps "<bucket>/<path>" -> bytes. ``faults`` is a FIFO of one-shot fault dicts applied to the next requests that
    match (``op`` of "any" matches every request): {"op": "upload"|"download"|"list"|"delete"|"any", ...} with one of
    status=<int> (respond with it), drop=True (close without answering), drop_after_store=True (store, then close),
    delay=<seconds>, corrupt=True (download returns altered bytes), body=<bytes> (download returns exactly this),
    redirect=<url>."""

    def __init__(self, bucket: str = "chunkvault-chunks", key: str = KEY) -> None:
        self.bucket, self.key = bucket, key
        self.objects: dict[str, bytes] = {}
        self.faults: list[dict] = []
        self.requests: list[dict] = []
        self.duplicate_style = "409"  # or "400"  (the alternative shape some Storage versions use)
        self.missing_style = "404"  # or "400"
        self.auth_failure_style = "401"  # or "403" or "400jwt"
        self.latency = 0.0  # added to every request (makes concurrency observable)
        self.always: dict[str, int] = {}  # op -> status returned for EVERY request of that op (a lasting outage)
        self.on_request = None  # callable(op) run before each request is handled (e.g. to inspect the database)
        self.inflight: dict[str, int] = {}
        self.max_inflight: dict[str, int] = {}
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):  # silence
                pass

            def _body(self) -> bytes:
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def _send(self, status: int, body: bytes = b"", ctype: str = "application/json", extra: dict | None = None) -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _json(self, status: int, obj) -> None:
                self._send(status, json.dumps(obj).encode())

            def _drop(self) -> None:
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            def _handle(self) -> None:
                op = outer._classify(self.command, unquote(urlsplit(self.path).path))
                with outer._lock:
                    outer.inflight[op] = outer.inflight.get(op, 0) + 1
                    outer.max_inflight[op] = max(outer.max_inflight.get(op, 0), outer.inflight[op])
                try:
                    if outer.latency:
                        time.sleep(outer.latency)
                    if outer.on_request is not None:
                        outer.on_request(op)
                    self._dispatch()
                finally:
                    with outer._lock:
                        outer.inflight[op] -= 1

            def _dispatch(self) -> None:
                body = self._body()
                path = unquote(urlsplit(self.path).path)
                auth = self.headers.get("Authorization", "")
                op = outer._classify(self.command, path)
                with outer._lock:
                    outer.requests.append({"method": self.command, "path": path, "op": op, "authorized": auth == f"Bearer {outer.key}",
                                           "headers": {k.lower(): v for k, v in self.headers.items() if k.lower() != "authorization"},
                                           "body": body if op != "upload" else b"<%d bytes>" % len(body)})
                    fault = None
                    for i, f in enumerate(outer.faults):
                        if f.get("op", "any") in ("any", op):
                            fault = outer.faults.pop(i)
                            break
                if fault is None and op in outer.always:
                    return self._json(outer.always[op], {"statusCode": str(outer.always[op]), "error": "outage", "message": "injected outage"})
                if fault and fault.get("delay"):
                    time.sleep(fault["delay"])
                if fault and fault.get("redirect"):
                    return self._send(302, b"", extra={"Location": fault["redirect"]})
                if fault and fault.get("drop"):
                    return self._drop()
                if fault and fault.get("status") and not fault.get("drop_after_store"):
                    return self._json(fault["status"], {"statusCode": str(fault["status"]), "error": "injected", "message": "injected fault"})
                if auth != f"Bearer {outer.key}":
                    if outer.auth_failure_style == "400jwt":
                        return self._json(400, {"statusCode": "400", "error": "invalid_jwt", "message": "invalid JWT: signature verification failed"})
                    code = int(outer.auth_failure_style)
                    return self._json(code, {"statusCode": str(code), "error": "Unauthorized", "message": "Invalid token"})
                prefix = f"/storage/v1/"
                if not path.startswith(prefix):
                    return self._json(404, {"message": "not found"})
                rest = path[len(prefix):]
                try:
                    if op == "upload":
                        bucket, _, obj = rest[len("object/"):].partition("/")
                        full = f"{bucket}/{obj}"
                        with outer._lock:
                            if full in outer.objects:
                                if outer.duplicate_style == "409":
                                    return self._json(409, {"statusCode": "409", "error": "Duplicate", "message": "The resource already exists"})
                                return self._json(400, {"statusCode": "409", "error": "Duplicate", "message": "The resource already exists"})
                            outer.objects[full] = body
                        if fault and fault.get("drop_after_store"):
                            return self._drop()
                        return self._json(200, {"Key": full})
                    if op == "download":
                        bucket, _, obj = rest[len("object/authenticated/"):].partition("/")
                        data = outer.objects.get(f"{bucket}/{obj}")
                        if data is None:
                            if outer.missing_style == "404":
                                return self._json(404, {"statusCode": "404", "error": "not_found", "message": "Object not found"})
                            return self._json(400, {"statusCode": "404", "error": "not_found", "message": "Object not found"})
                        if fault and fault.get("body") is not None:
                            data = fault["body"]
                        elif fault and fault.get("corrupt"):
                            data = bytes([data[0] ^ 1]) + data[1:]
                        return self._send(200, data, "application/octet-stream")
                    if op == "list":
                        bucket = rest[len("object/list/"):]
                        req = json.loads(body or b"{}")
                        prefix_, search = req.get("prefix", ""), req.get("search", "")
                        names = sorted(k[len(bucket) + 1:] for k in outer.objects if k.startswith(bucket + "/"))
                        out = []
                        for full in names:
                            folder, _, name = full.rpartition("/")
                            if folder != prefix_.strip("/") or (search and search not in name):
                                continue
                            out.append({"name": name, "metadata": {"size": len(outer.objects[f"{bucket}/{full}"])}})
                        off, lim = req.get("offset", 0), req.get("limit", 100)
                        return self._json(200, out[off:off + lim])
                    if op == "delete":
                        bucket = rest[len("object/"):]
                        req = json.loads(body or b"{}")
                        deleted = []
                        with outer._lock:
                            for p in req.get("prefixes", []):
                                if outer.objects.pop(f"{bucket}/{p}", None) is not None:
                                    deleted.append({"name": p, "bucket_id": bucket})
                        return self._json(200, deleted)
                except Exception as exc:  # noqa: BLE001 - a test double must not crash the connection thread silently
                    return self._json(500, {"message": f"fake server error: {type(exc).__name__}"})
                return self._json(404, {"message": "unknown"})

            do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _handle

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._server.handle_error = lambda request, client_address: None  # injected drops raise in the handler thread; expected
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @staticmethod
    def _classify(method: str, path: str) -> str:
        if "/object/list/" in path:
            return "list"
        if method == "POST":
            return "upload"
        if method == "GET":
            return "download"
        if method == "DELETE":
            return "delete"
        return "other"

    def count(self, op: str) -> int:
        return sum(1 for r in self.requests if r["op"] == op)

    def put(self, chunk_hash: str, data: bytes) -> None:
        self.objects[f"{self.bucket}/{chunk_hash[:2]}/{chunk_hash}"] = data

    def get(self, chunk_hash: str) -> bytes | None:
        return self.objects.get(f"{self.bucket}/{chunk_hash[:2]}/{chunk_hash}")

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
