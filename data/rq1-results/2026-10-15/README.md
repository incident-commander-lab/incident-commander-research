# RQ1 results snapshot: 15 Oct 2026, 10:03 UTC (FINAL: all nights finished)

Copied from the AWS bench host with `~/ic-bench.sh fetch` (checksum verified) and scanned for keys, emails and personal paths before committing: none found. The archives in `../2026-10-11/` (catalog v1 archive, Night 4 full file, Night 1 before redo) are unchanged; `../2026-10-12/` is the Night 5 snapshot.

| File | What it is |
|---|---|
| `full_rq1.jsonl.gz` | **Final results file: 730 runs, 10 setups, 73 problems each.** Every run that read a broken check was re-run on catalog v2 (274 runs on v2) |
| `full_progress.txt` | Start/end of every run, pauses, waits and stops |
| `full_logs.tar.gz` | One log per (model, setup, problem); the latest attempt only |
| `SHA256SUMS` | Checksums of the files above |

**Audit (`docs/study-guide/tools/audit_runs.py`):** 0 duplicates, 0 LLM errors, 0 catalog-v1 runs left that touched `flagd` / `jaeger-out`, 10 runs interrupted by pauses (re-run from scratch). Remaining tool-error replies are `event_reason` "No resources found" or empty (no Warning events, correctly read as `none`), 5 empty `pod_health` replies and 1 `log_signature` reply, all on catalog v1 runs that did not touch the broken services.

| Model | Plain LLM | LLM picks checks | Rule v1 | Rule v2 | Random | Every check |
|---|---|---|---|---|---|---|
| qwen3:8b | 12% | 45% | 42% | 48% | 40% | 47% |
| qwen3:14b | 18% | 55% | 26% | 52% | – | – |

These are the numbers to report. Read with `zcat full_rq1.jsonl.gz | python3 -m ic.bench.analyze /dev/stdin`.
