# QC gate pages

Static pages, one per draft per run, generated outside this app and served as files (build plan section 4b; PRD F5). Vite copies `public/` into `dist/` on build, so the Node server serves them at `/gate/`.

Regenerate from the run records with `eval/render_results.py` in the demo folder (see `DEMO_RUNBOOK.md` there), then copy the output here. `q3_fee_schedule.html` was rendered with `--graph-url http://127.0.0.1:18080/graph/sources`, the ingestion service's local port in `compose.yaml`; change it when the graph has a public URL.

The pages link to the graph by run id and fact id; they never call it.
