# Working in this repository

## Test locally. Do not test in production.

Deploying to Cloud Run, waiting for the rollout, hitting the live URL and pulling
Cloud Logging is not a test loop. It is slow, it costs money, it mutates real
tenant data, and a failure tells you almost nothing about which change caused it.
The whole stack runs locally; use it.

```sh
make up      # frontend 18081, ingestion 18080, SurrealDB 18000, all loopback-only
make test    # every suite: ingestion, connectors, mail agent, model gateway, infrastructure, frontend
make smoke   # ingestion smoke test against the running stack
make logs    # follow container logs instead of Cloud Logging
make down    # stop, keeping the database volume
```

`make` with no target lists everything. Before you claim a change works, it must
pass `make test`, and anything touching ingestion, storage or the HTTP surface
must also pass `make smoke` against a running stack.

Deploy only to release a change that is already proven locally. If you genuinely
cannot reproduce a problem locally, say so and explain what is missing, rather
than reaching for the deployed environment as the default.

## Prove it against a real database

Six tests are gated behind environment variables because they need a live
SurrealDB. `make up` gives you one, so run them — they cover the guarantees that
unit tests cannot: optimistic concurrency, and per-user database isolation.

```sh
make up         # gives you the database these tests need
make test-live  # optimistic concurrency, identity, per-user database isolation
```

Point `PROJECT_TERMS_FIXTURES` at the partner pack's
`05-terms-and-side-letter-demo` to include the project workflow tests too:

```sh
PROJECT_TERMS_FIXTURES=/path/to/05-terms-and-side-letter-demo make test-live
```

That run prints a result line (`arithmetic=0 failures, Q2=1, Q3=4`) whose figures
must match the pack's documented expectation. A mismatch is a real regression in
the gates, not a flaky fixture.

A green `make test` alone does not prove isolation or concurrency. Say which of
these you actually ran.

## Local and production must mirror each other

`compose.yaml` and `infrastructure/*.tf` describe the same system twice. When you
change one, change the other in the same commit, or local success stops
predicting production behaviour.

| Change | Also update |
|---|---|
| New service | `compose.yaml`, its `infrastructure/<name>.tf`, a `cloudbuild-<name>.yaml`, and a CI job |
| New environment variable | the compose service **and** the Cloud Run service in Terraform |
| New secret | compose (a dev-only literal) **and** Secret Manager with an IAM binding |
| New table or index in a provisioned database | a migration in `services/ingestion/app/migrations.py` — never a bare `DEFINE` at bootstrap |
| New database principal or namespace | the `surreal-init` service **and** `infrastructure/startup.sh.tftpl` |
| New dependency | the service's `requirements.txt` or `package.json`, so the image installs it |

Two real failures this rule exists to prevent, both found by running the stack
rather than reading it:

- The per-user graph path returned 500 on any fresh local database, because
  `startup.sh.tftpl` defined the `workflow_provisioner` principal on the cloud VM
  and nothing defined it locally. Local and production had drifted apart.
- `services/model_gateway` imported `google.auth.transport.requests` while
  pinning only `google-auth`. Its container failed on import. Nothing caught it
  because the service had no CI job.

Terraform is the source of truth for production. Never click in the console or
run an imperative `gcloud` command to fix something; change the `.tf` and apply.
Check your work with `make tf` (`fmt -check`, `init -backend=false`, `validate`).

## Schema changes go through migrations

Per-user and per-project databases are created on demand, so `DEFINE ... IF NOT
EXISTS` at provision time only ever reaches databases that did not exist yet.
`app/migrations.py` keeps an ordered, idempotent list per database kind and
records each applied step in a `schema_migration` table inside that database;
both provisioning paths apply whatever is outstanding.

To change the schema, append a migration — never edit an applied one, and never
add a `DEFINE` somewhere that runs only at creation. Every statement must stay
`IF NOT EXISTS`: a migration that fails midway is retried from the start.
`make test-live` covers this, including a database provisioned before a later
migration existed.

The shared `markets/documents` database is **not** covered: it is bootstrapped by
`startup.sh.tftpl` with a principal the service does not hold. Changing its
schema is still a manual, bootstrap-level change.

## What genuinely cannot run locally

Be honest about these rather than faking them:

- **The mail agent** needs Firestore and Cloud Tasks, and Cloud Tasks has no
  emulator, so it is not in the local stack. `/api/ingestion/status` answers 503
  locally and the progress view reports it as unavailable. Test its logic through
  `make test-mail`; if you need the HTTP path, point `MAIL_SERVICE_URL` at a
  throwaway stub and delete the stub afterwards.
- **Google connectors** need real OAuth credentials. The unit tests patch the
  Google clients; do not put live tokens in `compose.yaml`.
- **`test_loader`** needs `PROJECT_LOADER_FIXTURES` pointing at extracted dataset
  02, which is not in the repo.

## Reporting

State what you ran and what it printed. "Tests pass" without naming the suite is
not a result, and a suite that skipped six tests has not covered them. If a check
was skipped, blocked, or you could not run it, say which and why — an unrun check
reported as passing is worse than a failing one.
