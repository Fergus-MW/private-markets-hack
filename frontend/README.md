# 60x connection screen

A vanilla JavaScript / Vite frontend with a Three.js knowledge graph. The palette follows the rainbow accent on [60x.ai](https://www.60x.ai/): violet `#5a18ff`, blue `#00adfc`, cyan `#00f8e1`, purple `#cf00e4`, pink `#ff00ae`, peach `#ff8262`, with warm ivory and near-black. The separate black overlay is a radial gradient from **80% opacity at the centre to 50% at the edges**. Motion pauses offscreen and respects reduced-motion preferences. The landing page includes Google connection and navigation to the private graph explorer; authorization errors appear only when needed.

## Local preview

Requires Node 22.12+.

```sh
cd frontend
npm ci
npm run dev:server
# In a second terminal, also in frontend:
npm run dev
```

Open http://localhost:5173. The page works without OAuth configuration; clicking Connect returns an honest setup message. To serve the production build locally, run `npm run build` then `npm start` and open http://localhost:8080 (set `PUBLIC_ORIGIN` accordingly for OAuth).

## One Google authorization for every Google connector

The button starts a server-side authorization-code flow with PKCE, an encrypted HttpOnly transaction cookie, state and OpenID nonce validation. It requests `openid`, `email`, `gmail.readonly` and `drive.readonly` together. Drive access covers the existing Docs, Sheets, Slides and file imports; separate editor scopes are unnecessary. Partial consent is rejected. Tokens stay on the server and are saved in the existing connector secrets in the Python worker's `authorized_user` format.

1. Provision the Gmail and Drive connector entries from [CONNECTORS.md](../infrastructure/CONNECTORS.md), initially leaving `connector_image` unset if their secrets have no versions.
2. Create a Google OAuth **Web application** client. Add `http://localhost:5173/api/auth/google/callback` for development and `https://YOUR_FRONTEND_HOST/api/auth/google/callback` for production as exact authorized redirect URIs. This is a different client type from the existing desktop helper.
3. Copy `.env.example` to `.env`, set the client ID/secret, generate a random `SESSION_KEY`, and set `CONNECTOR_PROJECT` and `CONNECTOR_SERVICE_ACCOUNT`. Any verified Google account may connect: its credentials are stored in `connector-u-<hash>-oauth`, derived from the address, created on first connection and readable only by the connector job. Use the same key across replicas; no tokens are kept in process memory between requests.
4. Locally, configure application-default Google Cloud credentials. On Cloud Run, `frontend.tf` attaches a dedicated service account: it may create secrets, but writing versions and setting IAM policy are restricted by IAM condition to `connector-u-*`, so it can never widen access to the database password secrets. It does not need to read those secrets or access the database. The runtime must also be able to read its own OAuth client secret and session key when those are mounted from Secret Manager.
5. Authorize with the mapped account and allow both Gmail and Drive permissions. The UI reports success only after both secrets are written. Keep connector `secret_version = "latest"`, then deploy/run or schedule the existing jobs. Connecting authorizes the integrations; it does not automatically start an import.

Per-account derivation is intentional: the connector name comes from the verified ID token's address, so one visitor cannot reach another's connector, and no shared slot exists to overwrite. Accounts share one Cloud Run job and one bucket, isolated by the `CONNECTOR_PREFIX` key space rather than by separate infrastructure. Google consent-screen setup, test users and any required restricted-scope verification still apply. See [Google web OAuth](https://developers.google.com/identity/protocols/oauth2/web-server) and [Secret Manager version writes](https://docs.cloud.google.com/secret-manager/docs/add-secret-version).

If the second secret write fails after the first succeeds, the UI reports failure; reconnect to finish both writes. Secret Manager has no cross-secret transaction. The one-hour UI cookie records a completed authorization, not a live check of revoked Google permissions. Existing connector jobs are responsible for reporting subsequent token revocation/expiry. No tokens or client secrets are sent to the frontend bundle or browser storage.

## GCP container

The included Dockerfile builds the UI and serves it with the OAuth routes from one Node process on Cloud Run's `PORT`. Configure `PUBLIC_ORIGIN` to the final HTTPS origin. Supply OAuth client secret and `SESSION_KEY` through Secret Manager, with `GOOGLE_CONNECTOR_ACCOUNTS` and client ID as server environment configuration. Do not use `VITE_` variables for secrets. The consent button's public frontend must be reachable by the user and Google's browser redirect; the ingestion service and connector jobs remain IAM protected.

```sh
docker build -t knowledge-frontend frontend
# From repository root. Deploy the image using the existing Artifact Registry / Cloud Run workflow.
```

Google consent needs real configured credentials and a browser; local automated tests do not access user accounts or write cloud secrets.

```sh
npm test
npm run build
```

## Knowledge graph explorer

`/graphs` lists the signed-in account’s canonical graph and associated project graphs. `/graphs/workspace` and `/graphs/<project-id>` open a full-viewport Sigma.js 3 / Graphology WebGL viewer, with pan/zoom, fit, node selection, neighbor highlighting and an All graphs link. Direct links and refreshes work in both Vite and the production server. Project graphs must have been materialized by a workflow; opening one never creates or modifies it.

The ingestion service exposes `/graph/views` and `/graph/views/<id>` through the existing authenticated frontend proxy. Catalog membership and project reads use the request identity. Responses contain only node IDs, names, kinds and relationship metadata; source text, file blobs and run credentials are excluded. Deploy the frontend and ingestion changes together.

The renderer loads only on graph routes. Missing coordinates receive deterministic initial positions, followed by a bounded ForceAtlas2 Web Worker layout with Barnes–Hut optimization. Supplied coordinates bypass layout. Labels and edges hide during camera movement, and navigation disposes the renderer and worker. Reduced-motion users see the settled layout. This version uses JSON and Graphology objects; binary transport, stored layouts, community aggregation and a measured 10k-node/100k-edge frame-rate benchmark remain future work.
