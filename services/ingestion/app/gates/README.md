# Bundled deterministic checkers

Adapted from the user-supplied partner pack:

- `02-loader-gate/eval/eval_loader.py`
- `05-terms-and-side-letter-demo/eval/terms_checks.py`

The original files remain unchanged. These copies accept project-local file inputs and emit checks JSON. The loader's machine-specific paths/quarter are CLI inputs. Timestamped run records, appended triples, and standalone report generation are removed; `app.workflows` stores those outputs as project-local run and artifact records. The terms comparison helper explicitly uses `rtol=0`.

## Eval updates carried across

From the 6 September eval revision (`KG_handover2/built-tonight/eval/terms_checks.py`):

- **Amount at stake is the tier a total only.** Tier b findings are components of the same
  money, so adding every tier is double counting. Emitted as `amount_at_stake` in the checks
  JSON and shown on the QC dashboard.
- **A terms register with no `version` column no longer crashes the header line.**
- The checker also emits `entity`, `entity_id`, `as_of`, `mode` and `terms_rows_in_force`.
  These are things only the checker can determine from its inputs, so the dashboard header
  reads them here rather than re-deriving them.

Deliberately **not** carried across: `--terms-url` / `--terms-json`. The revision lets the
checker fetch the register straight from `GET /graph/funds/{fund_id}/terms`. Here the checker
runs in a subprocess with no credentials and no network, and a terms snapshot must be ratified
by a named reviewer before it can be an input (`app.workflows.check_coverage`). The same seam
is served by `POST /projects/{id}/terms-snapshot`, which materializes `terms_as_of` into a
project-local artifact that can then be ratified.

The comparison helper keeps `rtol=0`; the revision's `np.isclose` default would accept
proportionally large differences on large balances.

These are trusted bundled programs, not user-uploaded scripts. Their exact bytes and runtime versions are retained with each workflow evaluation.
