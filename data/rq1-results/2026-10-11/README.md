# RQ1 results snapshot: 11 Oct 2026, 19:58 UTC (Night 5 in progress)

Copied from the AWS bench host (`~/results/`) and verified by MD5 against the host. Scanned for keys, emails and personal paths before committing: none found.

| File | What it is |
|---|---|
| `full_rq1.jsonl.gz` | **Current results file: 515 runs.** The 456 unaffected catalog-v1 runs, plus 59 Night 5 re-runs on catalog v2 (8B rule v1 23/23, 8B rule v2 24/24, 8B random 12/23 so far) |
| `catalog_v1_archive.jsonl.gz` | The 201 runs that read a broken check (`flagd` / `jaeger-out`), set aside before the re-runs. Kept for the paper's "before vs after the fix" comparison |
| `night4_all_657_runs.jsonl.gz` | The complete 657-run file after Night 4, before anything was archived. Source of every provisional number reported on 11 Oct |
| `night1_before_redo.jsonl.gz` | Night 1 before the `assign_to_non_existent_node` fix was re-run (5 Oct) |
| `full_progress.txt` | Start/end of every run, pauses, waits and stops |
| `full_logs.tar.gz` | One log per (model, setup, problem); the latest attempt only |
| `SHA256SUMS` | Checksums of the files above |

**Status at snapshot time:** Night 5 is still running: 8B random, every-check and LLM-picks re-runs remain. Night 6 (14B re-runs + 14B LLM-picks) has not started. **Don't report numbers from this snapshot**; use the final one after Night 6.

Read it with `zcat full_rq1.jsonl.gz | python3 -m ic.bench.analyze /dev/stdin`, or point `docs/study-guide/tools/*.py` at an unzipped copy.
