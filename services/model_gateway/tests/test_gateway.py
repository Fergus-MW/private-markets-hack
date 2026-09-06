import json
import os
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient

from gateway.main import AdaptiveLimiter, VertexGateway, app


class StaticCredentials:
    def authorization(self):
        return "Bearer test", "test-project"

    def invalidate(self):
        pass


class LimiterTests(unittest.TestCase):
    def test_additive_increase_and_multiplicative_decrease(self):
        limiter = AdaptiveLimiter(initial=4, maximum=8)
        limiter.acquire()
        limiter.success()
        self.assertEqual(limiter.window, 4.25)
        limiter.acquire()
        limiter.throttle(0)
        self.assertEqual(limiter.window, 2.125)


class GatewayTests(unittest.TestCase):
    def test_rate_limit_retries_identical_bytes_and_reports_cache_hit(self):
        calls = []

        def post(url, headers, content):
            calls.append(content)
            if len(calls) == 1:
                return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": {}})
            return httpx.Response(200, json={"candidates": [], "usageMetadata": {
                "promptTokenCount": 5000, "cachedContentTokenCount": 4096}})

        transport = Mock(post=post)
        limiter = AdaptiveLimiter(initial=4, maximum=8)
        gateway = VertexGateway(limiter, StaticCredentials(), transport, rand=lambda: 0)
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project", "MODEL_GATEWAY_MAX_ATTEMPTS": "2"}):
            gateway.attempts = 2
            result = gateway.generate({"contents": [{"parts": [{"text": "same prefix"}]}]})
        self.assertEqual(result["usageMetadata"]["cachedContentTokenCount"], 4096)
        self.assertEqual(calls[0], calls[1])
        self.assertLess(limiter.window, 4)

    def test_non_retryable_request_is_not_replayed(self):
        transport = Mock()
        transport.post.return_value = httpx.Response(400, json={"error": {}})
        gateway = VertexGateway(AdaptiveLimiter(), StaticCredentials(), transport)
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}):
            with self.assertRaises(Exception):
                gateway.generate({"contents": []})
        transport.post.assert_called_once()

    def test_endpoint_rejects_fields_that_could_bypass_gateway_policy(self):
        response = TestClient(app).post("/v1/generate", json={
            "cache_namespace": "test", "request": {"model": "other", "contents": []}})
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
