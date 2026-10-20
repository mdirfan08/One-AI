"""HTTP-level tests. Require fastapi + httpx (pip install -r requirements-dev.txt).
NOTE: these could not be executed in the sandbox this repo was authored in (no network / packages)."""
import unittest

try:
    from fastapi.testclient import TestClient
    from project.backend.app.api import create_app
    HAVE = True
except ImportError:
    HAVE = False

from project.backend.tests.helpers import make_service


@unittest.skipUnless(HAVE, "fastapi/httpx not installed")
class ApiTests(unittest.TestCase):
    def client(self, **cfg):
        svc = make_service([], **cfg)
        return TestClient(create_app(svc)), svc

    def test_demo_flow_over_http(self):
        c, _ = self.client()
        pid = c.post("/api/projects", json={"request": "write a blog post about tea", "mode": "demo", "execution": "auto"}).json()["id"]
        self.assertEqual(c.post(f"/api/projects/{pid}/plan").status_code, 200)
        self.assertEqual(c.post(f"/api/projects/{pid}/run").status_code, 200)
        import time
        for _ in range(50):
            d = c.get(f"/api/projects/{pid}").json()
            if not d["running"]:
                break
            time.sleep(0.1)
        self.assertEqual(d["status"], "completed")
        aid = d["artifacts"][0]["id"]
        self.assertEqual(c.get(f"/api/projects/{pid}/artifacts/{aid}").status_code, 200)
        self.assertEqual(c.get(f"/api/projects/{pid}/export").status_code, 200)

    def test_errors_map_to_status_codes(self):
        c, _ = self.client()
        self.assertEqual(c.get("/api/projects/p_0000000000").status_code, 404)
        self.assertEqual(c.post("/api/projects", json={"request": ""}).status_code, 422)
        pid = c.post("/api/projects", json={"request": "x"}).json()["id"]
        self.assertEqual(c.post(f"/api/projects/{pid}/run").status_code, 409)

    def test_access_token_enforced(self):
        c, svc = self.client(access_token="s3cret-token")
        self.assertEqual(c.get("/api/health").status_code, 401)
        self.assertEqual(c.get("/api/health", headers={"X-Access-Token": "s3cret-token"}).status_code, 200)

    def test_credentials_never_returned(self):
        c, _ = self.client()
        r = c.put("/api/providers/openai/credentials", json={"api_key": "sk-test-abcdefghijklmnop"})
        self.assertNotIn("abcdefghijklmnop", r.text)
        self.assertNotIn("abcdefghijklmnop", c.get("/api/providers").text)

    def test_oversized_body_rejected(self):
        c, _ = self.client(max_request_bytes=1000)
        self.assertEqual(c.post("/api/projects", json={"request": "x" * 5000}).status_code, 413)


if __name__ == "__main__":
    unittest.main()
