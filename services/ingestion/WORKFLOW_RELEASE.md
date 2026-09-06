# Workflow release status — 2026-09-05

Implemented: canonical source upload bridge, one isolated SurrealDB database per project, retained original files, deterministic project-scoped loader inputs, terms snapshots and named ratifications, immutable checker runs and intermediate artifacts. No facts table or fact records are added.

Validation: 26 ingestion/graph/project tests and 20 connector tests passed. The additional real-workbook acceptance test passed: fund 2254 Q2 reference produced 49 PASS / 1 WARN; adding 1,000 to an investor amount produced 5 FAIL. Existing terms fixtures produced the expected Q2 single failure and Q3 four failures. Project DB credentials were tested against other project and canonical databases, with access denied. Fixtures and injected errors remain local.

Infrastructure: the reviewed targeted Terraform apply completed with 8 resources added, 1 changed, and 0 destroyed. It provisioned workflow secrets/access and service environment variables. The existing SurrealDB VM was not replaced or restarted. The production application image and connector job remain on their prior releases.

## Pending explicit production approval

Automatic approval review rejected these operations:

1. Execute `infrastructure/scripts/bootstrap_project_namespace.py` on the existing `surrealdb` VM in `private-markets-hack`, zone `europe-west2-a`. It retrieves the existing root and new provisioner passwords through the VM identity and creates/updates only `projects.workflow_provisioner`, a namespace OWNER used to provision project databases. This is a persistent privileged identity. It does not restart the VM or modify the canonical database.
2. Upload `services/ingestion` and `services/connectors` release source to Google Cloud Build in `private-markets-hack`, and push images to `europe-west2-docker.pkg.dev/private-markets-hack/services`. These private source trees leave the local workspace. Build staging must contain only service code, dependency manifests and tests; exclude secrets, local fixtures, Terraform state, virtual environments and caches. After successful image tests and namespace bootstrap, deploy the ingestion image and update `connector-team-drive`, then perform authenticated smoke tests.

Only the Drive connector job currently exists. Gmail ingestion is implemented but still requires a configured Gmail connection and OAuth grant. Workflow orchestration is invoked by the API; automatic inbox-to-project routing is not implemented. Named legal approvals are required for terms checks. End-user authorization, outbound messages and automatic financial corrections remain separate product work.

## Multi-user update

Upstream main was pulled through `f72db5c` before the Terraform update. Canonical graphs and project graphs are now isolated per account, with signed service assertions, authenticated frontend proxying, account-bound connector ingestion and pinned Terraform bootstrap dependencies. Legacy data is preserved without assigning an owner. See `MULTI_USER.md`. Latest local checks: 31 ingestion/workflow tests, 28 connector tests, 18 frontend tests, and the bootstrap runner test passed. Terraform validates. This update has not applied the production bootstrap or uploaded/deployed application images.
