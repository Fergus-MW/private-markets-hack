# Model gateway

All production Gemini calls from the mail agent and ingestion/workflow service pass through this private Cloud Run service. Only its service account has `roles/aiplatform.user`; callers receive `roles/run.invoker` on the gateway.

The gateway is deliberately a single warm instance. It maintains one process-wide additive-increase/multiplicative-decrease concurrency window: a successful response increases the window by `1/window`, while HTTP 429, 408, transient 5xx responses and transport failures halve it. Retries honor `Retry-After` and Google `RetryInfo`, add exponential full jitter, and stop after a bounded number of attempts. Non-retryable responses are never replayed.

Vertex implicit caching remains provider-managed and enabled by default. The gateway does not add timestamps, attempt counters or changing text to prompts. It canonicalizes the Vertex body once and reuses those exact bytes on every attempt. Callers place stable system policy and project evidence before role-specific instructions. `X-Model-Cache-Tokens`, `X-Model-Prompt-Tokens`, and `X-Rate-Window` expose safe telemetry; logs contain the cache namespace, model, token counts and congestion window, never prompt content.

Environment controls:

- `GEMINI_MODEL` selects the one allowed model.
- `MODEL_GATEWAY_INITIAL_WINDOW` defaults to `4`.
- `MODEL_GATEWAY_MAX_WINDOW` defaults to `32`.
- `MODEL_GATEWAY_MAX_ATTEMPTS` defaults to `6`.
- `MODEL_GATEWAY_MAX_BACKOFF` defaults to `30` seconds.
- `GEMINI_API_KEY` enables the Google AI endpoint for local development only; production uses Application Default Credentials and Vertex AI.

Run tests with:

```sh
PYTHONPATH=services/model_gateway python -m unittest discover -s services/model_gateway/tests -v
```
