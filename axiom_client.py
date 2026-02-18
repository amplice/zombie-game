"""Axiom engine HTTP API client."""

import json
import http.client
import os
import time
import urllib.error
import urllib.request

DEFAULT_API_URL = "http://127.0.0.1:3000"


class AxiomClient:
    def __init__(self, base_url: str = DEFAULT_API_URL, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_token = os.environ.get("AXIOM_API_TOKEN", "").strip()

    def _request(self, method: str, path: str, data=None):
        url = f"{self.base_url}{path}"
        headers = {"Connection": "close"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            print(f"  HTTP {e.code} on {method} {path}: {error_body[:500]}")
            raise

    def get(self, path: str):
        return self._request("GET", path)

    def post(self, path: str, data=None):
        return self._request("POST", path, data or {})

    def delete(self, path: str):
        return self._request("DELETE", path)

    def wait_for_server(self, timeout: float = 10.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            try:
                self.get("/state")
                return True
            except Exception:
                time.sleep(0.5)
        return False
