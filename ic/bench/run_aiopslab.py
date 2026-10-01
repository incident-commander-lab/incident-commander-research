"""Run the VoI agent on AIOpsLab problems and append one JSON line per run.

Needs AIOpsLab installed in the same Python environment (on the benchmark
machine, not locally) and an LLM provider configured, e.g.:

    IC_USE_LLM=1 IC_STRICT_PROVIDER=1 IC_PROVIDER=vllm IC_VLLM_BASE_URL=http://localhost:8001/v1 \\
    IC_VLLM_MODEL=Qwen/Qwen3-32B \\
    python -m ic.bench.run_aiopslab --policy voi --seeds 0 1 2 --out results/bench.jsonl

Policies: react (A), llm_choose (B), voi (C), random (D), exhaustive (E).
Mitigation problems are skipped: these agents diagnose, they do not act.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

from .agent import task_type
from .baselines import POLICIES, make_agent


def main() -> None:
    from aiopslab.orchestrator import Orchestrator
    from aiopslab.orchestrator.problems.registry import ProblemRegistry

    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", default="voi", choices=POLICIES)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0])
    ap.add_argument("--problems", nargs="*", help="problem ids (default: all non-mitigation)")
    ap.add_argument("--max-steps", type=int, default=30)
    ap.add_argument("--out", default="results/bench.jsonl")
    args = ap.parse_args()

    pids = args.problems or ProblemRegistry().get_problem_ids()
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    for seed in args.seeds:
        for pid in pids:
            if "mitigation" in pid:
                continue
            orch = Orchestrator()
            agent = make_agent(args.policy, seed=seed)
            orch.register_agent(agent, name=f"ic-voi-{args.policy}")
            problem_desc, instructions, apis = orch.init_problem(pid)
            if task_type(problem_desc) == "mitigation":
                continue
            agent.init_context(problem_desc, instructions, apis)
            final = asyncio.run(orch.start_problem(max_steps=args.max_steps))
            row = {"problem": pid, "seed": seed, "policy": args.policy,
                   "aiopslab_results": final.get("results") if isinstance(final, dict) else None,
                   **agent.result}
            with open(args.out, "a") as f:
                f.write(json.dumps(row, default=str) + "\n")
            print(f"{pid} seed={seed}: {agent.result.get('submission')} "
                  f"probes={agent.result.get('probes_run')} tokens="
                  f"{agent.result.get('llm_usage', {}).get('input_tokens', 0)}")


if __name__ == "__main__":
    main()
