# Week 2 mechanics test on AWS, 23 Oct 2026

g5.xlarge (Spot; no g6), ops/apps on-demand. Code 4b2ebb8. Held-out faults were not given to the agent:
`attack_burst` was tested by hand (attack job, the check, the block), never as an agent decision.
Logs: `run1.log` (deploy + sections 1-5), `run2-followup.log` (why bad_release ended "fixed"; block test).

| Part | Result |
|---|---|
| Deploy (shop, AI, monitoring, Pulse) | all pods running, no manual step |
| Log replay (NASA 13 Jul 1995) | runs as designed, ~3,300 req/min at the start of the day |
| Traefik | 2.11.18; access log on; `traefik.io` CRDs present |
| Attack check, first pattern | **missed**: 250 attack lines but 27% of the attacker's requests (bar 30%) |
| Attack check, fixed pattern (decode, then match) | **attack**: 734 lines, 78% share (ceiling 80%: login POST bodies are not in the log). Offline: 97% of the 240 GET payload lines, 0 of 1,528 normal requests |
| `block_client` (run directly, as after approval) | attacker **403**, normal shopper **200**; undo removes it |
| Plain LLM agent, `assistant_bad_url` (seen) | right service, remedy `none` at confidence 1.0, escalated (twice); setup-2 behaviour, data for RQ3 |
| Pulse in the cluster | 8 pods, 34.6 events/s stored, every Pulse check reads |
| `bad_release_worker` (seen), rule agent | 1st incident escalated (`rollout_undo:worker` p = 0.44); 2nd rolled the worker back, fixed 7.1 min after inject |
| `db_lock` (seen), rule agent | `db_locks:postgres` read **blocked**, but the alert was "postgres-exporter not answering" and no hypothesis led to `kill_blocking_sessions` (p <= 0.007); escalated 3x, timeout |

Fixed after the test: attack pattern (decode first; tests hold recall >= 95% and no false alarms);
attack window 2 min -> 90 s, so a verification 2 min after a block reads only post-block traffic
(the check 105 s after the block still counted 15 s of pre-block lines).

Open: `db_lock` in the cluster raises ComponentDown (the exporter's scrape blocks behind the lock)
rather than Compose's DbWriteErrors first. Seen fault, so it may be studied; decide with Nour whether
the alert set or the hypotheses prompt should change before cluster RQ3 sessions.

Cost: ~1 h 35 min of the session, about $1.
