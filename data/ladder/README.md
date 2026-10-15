# Paper 2, Experiment 1: the model ladder

Code: `ic/bench/ladder.py` (method in its docstring). Driver: `run_ladder.sh` (resumable).

| File | What it is |
|---|---|
| `inputs.json.gz` | Per problem: AIOpsLab's exact task text (dumped on the bench host from `get_task_description()`, AIOpsLab commit `ccf08d0d1d5fa5b30f120e2e8549662d44411b35`), and one fixed snapshot of `kubectl get svc` / workloads / `kubectl get pods` from a catalog-v2 RQ1 log (`source` says which) |
| `bank.json.gz` | Reading bank: every paid reading from all saved RQ1 runs of each problem, majority label per check (2,565 checks, 19 with disagreeing readings) |
| `generations.jsonl` | One LLM generation per (model, problem, findings), shared by rule v1 and v2 |
| `ladder_runs.jsonl` | One row per (model, problem, rule version), scored against RQ1 ground truth |

**Validation of the fake cluster (before any new generation):** replaying the saved RQ1 runs with their own saved hypotheses gives the same answer in 220 of 232 runs (95%): 8B rule v2 46/47, 14B rule v2 64/68, 8B rule v1 47/50, 14B rule v1 63/67. Differences come from checks no run ever took (answered as unavailable) and pod states that changed between nights.

**Known limits:** readings are labels, so the log reader is not re-run per model; one generation per model and problem (temperature 0.3), so small-model results carry sampling noise; checks never run in RQ1 are unavailable.
