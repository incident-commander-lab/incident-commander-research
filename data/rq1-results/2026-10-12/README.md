# RQ1 results snapshot: 12 Oct 2026, 22:40 UTC (Night 5 finished)

Copied from the AWS bench host with `~/ic-bench.sh fetch` (checksum verified) and scanned for keys, emails and personal paths before committing: none found. The archives from the 11 Oct snapshot (catalog v1 archive, Night 4 full file, Night 1 before redo) are unchanged and stay in `../2026-10-11/`.

| File | What it is |
|---|---|
| `full_rq1.jsonl.gz` | **Current results file: 603 runs, 9 setups.** Every 8B setup is complete (73/73) and every 8B run that read a broken check was re-run on catalog v2 |
| `full_progress.txt` | Start/end of every run, pauses, waits and stops |
| `full_logs.tar.gz` | One log per (model, setup, problem); the latest attempt only |
| `SHA256SUMS` | Checksums of the files above |

**Audit (`docs/study-guide/tools/audit_runs.py`):** 0 duplicates, 0 LLM errors, 0 catalog-v1 runs left that touched `flagd` / `jaeger-out`, 9 runs interrupted by pauses (re-run from scratch). Remaining tool-error replies are all `event_reason` ("No resources found" or an empty reply), which means there were no Warning events; reading them as `none` is correct.

**Status:** 8B is final. 14B still has gaps: rule v1 50/73, rule v2 42/73 and no LLM-picks; Night 6 fills them. **Don't report 14B numbers from this snapshot.**

Read it with `zcat full_rq1.jsonl.gz | python3 -m ic.bench.analyze /dev/stdin`, or point `docs/study-guide/tools/*.py` at an unzipped copy.
