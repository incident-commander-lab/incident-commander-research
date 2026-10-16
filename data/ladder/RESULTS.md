# Model ladder results (Paper 2, Experiment 1), 16 Oct 2026

Offline replay, method and validation in [`README.md`](README.md) (fake cluster reproduces 220/232
real RQ1 answers). Each model wrote hypotheses once per problem; rule v1 and rule v2 judged the same
hypotheses. 73 AIOpsLab problems: 32 detection (29 with a fault), 28 localization, 13 analysis.
**Always answering "Yes" scores 29/73 (40%)**, all of it on detection.

## Strict hypothesis contract (as deployed)

| Model | Rule v1 | Rule v2 | Detection correct v1 → v2 (of 32) | "No fault" answers on detection v1 → v2 (truth: 3) | Localization + analysis v1 / v2 (of 41) |
|---|---|---|---|---|---|
| qwen3:1.7b | 4/73 (5%) | 6/73 (8%) | 4 → 6 | 31 → 29 | 0 / 0 |
| qwen3:4b (original hybrid, `4b-q4_K_M`) | 22/73 (30%) | 35/73 (48%) | 13 → 26 | 22 → 5 | 9 / 9 |
| qwen3:8b | 25/73 (34%) | 35/73 (48%) | 16 → 25 | 17 → 4 | 9 / 10 |
| qwen3:14b | 20/73 (27%) | 34/73 (47%) | 11 → 24 | 24 → 5 | 9 / 10 |

95% Wilson intervals overall: v1 18–46%, v2 36–59% (all models ≥ 4B); per model, see `python -m ic.bench.ladder table`.

## Lenient format (missing `confidence` → equal prior; only strict failures re-asked)

Only qwen3:1.7b changes (it omitted `confidence` in 69/76 strict generations; the others never failed):

| Model | Rule v1 | Rule v2 | "No fault" answers v1 → v2 |
|---|---|---|---|
| qwen3:1.7b | 21/73 (29%) | 32/73 (44%) | 23 → 11 |

## What it shows

1. **The Upgrade Paradox replicates offline, in direction.** Under rule v1, 14B is the worst of the
   working models (27% vs 34% at 8B; the real RQ1 runs: 42% → 26%). Under rule v2 the drop is gone
   (47–48% from 4B to 14B). The 8B→14B gap alone is inside the confidence intervals; the evidence is
   that two independent setups (live RQ1 runs and this replay) show the same direction and mechanism.
2. **The mechanism is visible.** Rule v1 answers "no fault" on 17 (8B) to 24 (14B) of 32 detection
   problems where only 3 are truly fault-free; rule v2 brings it to 4–5 for every model. The effect
   is **not monotonic in size** (4B: 22), so the claim is "a different/stronger hypothesis writer can
   make v1 worse", not "bigger is always worse".
3. **It is entirely a detection effect.** On the 41 localization and analysis problems every model
   and both rules score 9–10/41 (22–24%): model size changes nothing there.
4. **Caveat for the paper:** rule v2's detection (24–26/32) is still below always-"Yes" (29/32).
   Paper 2 claims "v2 removes the paradox", not "v2 detects well".
5. **Below ~4B the bottleneck is format, not reasoning.** qwen3:1.7b breaks the strict contract
   (omits `confidence`); forgiven, it reaches 29% / 44%, close to 4B and 8B. Its lenient runs asked
   for 4–6 checks per problem that no RQ1 run took (answered "unavailable"), so they are less certain.

## Not included

- **qwen3:0.6b:** the download fails at the manifest step ("EOF") with Ollama 0.35.0 under both tags
  (`0.6b`, `0.6b-q4_K_M`) while the registry answers 200: an Ollama-side problem. Retry after an
  Ollama update.
- **qwen3:4b (default tag):** since Ollama's 2507 update it is Qwen3-4B-Thinking-2507 (thinking cannot
  be turned off; newer generation). Its 4 generations / 8 runs are kept in
  `*_excluded_qwen3-4b-thinking-2507.jsonl` and not used.
