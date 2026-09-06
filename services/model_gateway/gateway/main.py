"""Cache-friendly Gemini gateway with adaptive, TCP-like rate limiting."""
from __future__ import annotations

import email.utils
import json
import logging
import math
import os
import random
import threading
import time
from dataclasses import dataclass

import google.auth
import httpx
from fastapi import FastAPI, HTTPException, Response
from google.auth.transport.requests import Request as AuthRequest
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)
app = FastAPI(title="Private Gemini gateway")

RETRYABLE = {408, 429, 500, 502, 503, 504}
ALLOWED_FIELDS = {"systemInstruction", "contents", "tools", "toolConfig", "generationConfig", "safetySettings"}


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cache_namespace: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    request: dict


class AdaptiveLimiter:
    """A process-wide additive-increase/multiplicative-decrease window."""

    def __init__(self, initial=4.0, maximum=32.0, minimum=1.0, clock=time.monotonic):
        if not 1 <= minimum <= initial <= maximum:
            raise ValueError("Invalid congestion-window bounds")
        self.window = float(initial)
        self.maximum = float(maximum)
        self.minimum = float(minimum)
        self.in_flight = 0
        self.blocked_until = 0.0
        self.clock = clock
        self.condition = threading.Condition()

    @property
    def capacity(self):
        return max(1, math.floor(self.window))

    def acquire(self):
        with self.condition:
            while True:
                delay = self.blocked_until - self.clock()
                if delay <= 0 and self.in_flight < self.capacity:
                    self.in_flight += 1
                    return
                self.condition.wait(timeout=max(0.001, delay) if delay > 0 else None)

    def success(self):
        with self.condition:
            self.in_flight -= 1
            # Approximately one additional slot per successful window, as in AIMD.
            self.window = min(self.maximum, self.window + 1.0 / self.window)
            self.condition.notify_all()

    def complete(self):
        with self.condition:
            self.in_flight -= 1
            self.condition.notify_all()

    def throttle(self, delay):
        with self.condition:
            self.in_flight -= 1
            self.window = max(self.minimum, self.window / 2.0)
            self.blocked_until = max(self.blocked_until, self.clock() + max(0.0, delay))
            self.condition.notify_all()


class Credentials:
    def __init__(self):
        self.credentials = None
        self.project = None
        self.lock = threading.Lock()

    def authorization(self):
        with self.lock:
            if self.credentials is None:
                self.credentials, self.project = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"])
            if not self.credentials.valid:
                self.credentials.refresh(AuthRequest())
            return "Bearer " + self.credentials.token, self.project

    def invalidate(self):
        with self.lock:
            if self.credentials is not None:
                self.credentials.expiry = None
                self.credentials.token = None


def retry_after(response, now=time.time):
    value = response.headers.get("retry-after")
    if value:
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                return max(0.0, email.utils.parsedate_to_datetime(value).timestamp() - now())
            except (TypeError, ValueError, OverflowError):
                pass
    try:
        details = response.json().get("error", {}).get("details", [])
        for detail in details:
            duration = detail.get("retryDelay")
            if isinstance(duration, str) and duration.endswith("s"):
                return max(0.0, float(duration[:-1]))
    except (ValueError, TypeError, AttributeError):
        pass
    return 0.0


@dataclass
class GatewayFailure(Exception):
    status: int
    message: str


class VertexGateway:
    def __init__(self, limiter, credentials=None, client=None, rand=random.random):
        self.limiter = limiter
        self.credentials = credentials or Credentials()
        self.client = client or httpx.Client(timeout=httpx.Timeout(120, connect=10))
        self.rand = rand
        self.attempts = int(os.environ.get("MODEL_GATEWAY_MAX_ATTEMPTS", "6"))
        self.max_backoff = float(os.environ.get("MODEL_GATEWAY_MAX_BACKOFF", "30"))

    def _delay(self, attempt, response=None):
        server = retry_after(response) if response is not None else 0.0
        jitter = self.rand() * min(self.max_backoff, 0.5 * (2 ** attempt))
        return max(server, jitter)

    def generate(self, request):
        model = os.environ.get("GEMINI_MODEL", "gemini-3.1-pro-preview")
        if os.environ.get("GEMINI_API_KEY"):
            endpoint = ("https://generativelanguage.googleapis.com/v1beta/models/"
                        + model + ":generateContent")
            headers = {"x-goog-api-key": os.environ["GEMINI_API_KEY"], "Content-Type": "application/json"}
        else:
            authorization, detected_project = self.credentials.authorization()
            project = os.environ.get("GOOGLE_CLOUD_PROJECT") or detected_project
            if not project:
                raise GatewayFailure(500, "No Google Cloud project is configured")
            endpoint = ("https://aiplatform.googleapis.com/v1/projects/" + project
                        + "/locations/global/publishers/google/models/" + model + ":generateContent")
            headers = {"Authorization": authorization, "Content-Type": "application/json"}
        # Render once. Every retry has identical bytes, preserving the prefix used
        # by Vertex implicit caching and avoiding retry-specific prompt mutations.
        body = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        last_status = 503
        for attempt in range(self.attempts):
            self.limiter.acquire()
            try:
                response = self.client.post(endpoint, headers=headers, content=body)
            except httpx.RequestError:
                delay = self._delay(attempt)
                self.limiter.throttle(delay)
                last_status = 503
                if attempt + 1 == self.attempts:
                    break
                continue
            last_status = response.status_code
            if response.status_code == 401 and attempt == 0 and "Authorization" in headers:
                self.limiter.complete()
                self.credentials.invalidate()
                headers["Authorization"], _ = self.credentials.authorization()
                continue
            if response.status_code in RETRYABLE:
                delay = self._delay(attempt, response)
                self.limiter.throttle(delay)
                if attempt + 1 == self.attempts:
                    break
                continue
            if response.is_error:
                self.limiter.complete()
                raise GatewayFailure(response.status_code, "Vertex rejected the model request")
            self.limiter.success()
            return response.json()
        raise GatewayFailure(last_status, "Vertex remained unavailable after adaptive retries")


limiter = AdaptiveLimiter(
    initial=float(os.environ.get("MODEL_GATEWAY_INITIAL_WINDOW", "4")),
    maximum=float(os.environ.get("MODEL_GATEWAY_MAX_WINDOW", "32")),
)
vertex = VertexGateway(limiter)


@app.get("/healthz")
def health():
    return {"status": "ok", "congestion_window": limiter.window, "in_flight": limiter.in_flight}


@app.post("/v1/generate")
def generate(body: GenerateRequest, response: Response):
    fields = set(body.request)
    if not fields <= ALLOWED_FIELDS or "contents" not in fields:
        raise HTTPException(422, "Unsupported Vertex request fields")
    try:
        payload = vertex.generate(body.request)
    except GatewayFailure as error:
        raise HTTPException(error.status, error.message) from None
    usage = payload.get("usageMetadata", {})
    cached = int(usage.get("cachedContentTokenCount", 0) or 0)
    response.headers["X-Model-Cache-Tokens"] = str(cached)
    response.headers["X-Model-Prompt-Tokens"] = str(int(usage.get("promptTokenCount", 0) or 0))
    response.headers["X-Rate-Window"] = f"{limiter.window:.2f}"
    logger.info(json.dumps({"event": "model_request", "cache_namespace": body.cache_namespace,
                            "model": os.environ.get("GEMINI_MODEL", "gemini-3.1-pro-preview"),
                            "cached_tokens": cached,
                            "prompt_tokens": usage.get("promptTokenCount", 0),
                            "congestion_window": round(limiter.window, 2)}))
    return payload
