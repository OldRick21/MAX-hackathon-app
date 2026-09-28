"""CoreClient против заглушки ядра: python -m unittest tests.test_core_client (из administration/)."""
import base64
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.core_client import BindingMissing, CoreClient, CoreUnavailable

INST = "11111111-1111-4111-8111-111111111111"
SRV = "22222222-2222-4222-8222-222222222222"


class Stub(BaseHTTPRequestHandler):
    state = {}

    def log_message(self, *args):
        pass

    def reply(self, status, body=None, headers=None):
        data = b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Request-ID", "upstream-1")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def handle_any(self):
        s = self.state
        s.setdefault("calls", []).append((self.command, self.path, dict(self.headers)))
        if self.path == "/api/v1/internal/auth/token":
            self.body()
            if self.headers["Authorization"] != "Basic " + base64.b64encode(b"cid:sec").decode():
                return self.reply(401, {"error": {"code": "UNAUTHENTICATED", "message": "x", "request_id": "r"}})
            s["exchanges"] = s.get("exchanges", 0) + 1
            return self.reply(200, {"access_token": f"m{s['exchanges']}", "expires_in": 300, "institution_id": INST,
                                    "service_id": SRV, "scopes": ["institution:manage", "tokens:introspect"]})
        auth = self.headers.get("Authorization")
        if s.get("reject_token") == auth:
            self.body()
            return self.reply(401, {"error": {"code": "UNAUTHENTICATED", "message": "x", "request_id": "r"}})
        if self.path == "/api/v1/internal/auth/introspect":
            token = json.loads(self.body())["token"]
            return self.reply(200, {"active": token == "good"})
        if self.path.startswith(f"/api/v1/institution/{INST}/internal"):
            self.body()
            mode = s.get("private", "ok")
            if mode == "500":
                return self.reply(500, {"error": {"code": "INTERNAL_ERROR", "message": "x", "request_id": "r"}})
            if mode == "409":
                return self.reply(409, {"error": {"code": "LAST_OWNER", "message": "last", "request_id": "r"}})
            return self.reply(201, {"id": "x"}, {"ETag": '"v1"', "Location": f"/api/v1/institution/{INST}/internal/members/u"})
        return self.reply(404, {"error": {"code": "RESOURCE_NOT_FOUND", "message": "x", "request_id": "r"}})

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = handle_any


class CoreClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        Stub.state.clear()
        # Ключ процесса из .env — как «Выдать ключ» в пульте.
        self.client = CoreClient(self.url, "cid", "sec", "https://a/api/v1", "https://a", timeout=2)

    def test_binding_and_token_cache(self):
        binding = self.client.binding(SRV)
        self.assertEqual((binding.institution_id, binding.api_base_url), (INST, "https://a/api/v1"))
        with self.assertRaises(BindingMissing):
            self.client.binding("33333333-3333-4333-8333-333333333333")
        self.assertTrue(self.client.introspect(binding, "good")["active"])
        self.assertFalse(self.client.introspect(binding, "bad")["active"])
        self.assertEqual(Stub.state["exchanges"], 1)  # machine JWT переиспользуется

    def test_retry_once_on_401(self):
        binding = self.client.binding(SRV)
        self.client.machine_token(binding)
        Stub.state["reject_token"] = "Bearer m1"
        self.assertTrue(self.client.introspect(binding, "good")["active"])
        self.assertEqual(Stub.state["exchanges"], 2)

    def test_private_forwarding_and_errors(self):
        binding = self.client.binding(SRV)
        up = self.client.private(binding, "POST", "/members", "actor-jwt", body=b"{}", if_match='"e"',
                                 facade_request_id="fid")
        self.assertEqual(up.status, 201)
        self.assertEqual(up.headers["ETag"], '"v1"')
        method, path, headers = Stub.state["calls"][-1]
        self.assertEqual(path, f"/api/v1/institution/{INST}/internal/members")
        self.assertEqual(headers["X-Actor-Token"], "actor-jwt")
        self.assertEqual(headers["If-Match"], '"e"')
        self.assertEqual(headers["X-Facade-Request-ID"], "fid")
        Stub.state["private"] = "409"
        self.assertEqual(self.client.private(binding, "DELETE", "/members/u", "a").body["error"]["code"], "LAST_OWNER")
        Stub.state["private"] = "500"
        with self.assertRaises(CoreUnavailable):
            self.client.private(binding, "GET", "/members", "a")

    def test_rejected_key_is_binding_missing(self):
        with self.assertRaises(BindingMissing):
            CoreClient(self.url, "cid", "wrong", timeout=2).binding(SRV)

    def test_transport_failure_is_unavailable(self):
        client = CoreClient("http://127.0.0.1:9", "cid", "sec", timeout=0.5)
        with self.assertRaises(CoreUnavailable):
            client.binding(SRV)


if __name__ == "__main__":
    unittest.main()
