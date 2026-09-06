# Bundled deterministic checkers

Adapted from the user-supplied partner pack:

- `02-loader-gate/eval/eval_loader.py`
- `05-terms-and-side-letter-demo/eval/terms_checks.py`

The original files remain unchanged. These copies accept project-local file inputs and emit checks JSON. The loader's machine-specific paths/quarter are CLI inputs. Timestamped run records, appended triples, and standalone report generation are removed; `app.workflows` stores those outputs as project-local run and artifact records. The terms comparison helper explicitly uses `rtol=0`.

These are trusted bundled programs, not user-uploaded scripts. Their exact bytes and runtime versions are retained with each workflow evaluation.
