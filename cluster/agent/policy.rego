package cluster

import rego.v1

# What the agent may do on its own in the cluster. Evaluated by OPA, outside the agent, before
# every action. Same rules as the Compose test bed (testbed/policy/testbed.rego), plus: blocking a
# client is never autonomous.

allowlist := {
	"scale_out:juice-shop", "scale_in:juice-shop", "rollout_undo:juice-shop", "rollout_undo:assistant",
	"rate_limit:assistant", "restart:juice-shop", "restart:assistant", "block_client",
	# Pulse in the cluster (same rules as testbed/policy/testbed.rego)
	"rollout_undo:worker", "rollout_undo:collector", "restart:worker", "restart:collector", "restart:api",
	"scale_out:api", "kill_blocking_sessions:postgres", "restore_redis_maxmemory:redis",
}

reasons contains "action not on the allowlist" if not input.action in allowlist

reasons contains "action is not reversible" if not input.reversible

reasons contains "autonomy level does not allow acting without approval" if {
	input.autonomy != "act"
	not input.approved
}

reasons contains "belief below the act threshold" if {
	not input.approved
	input.p < input.threshold
}

reasons contains "blocking a client always needs a human's approval" if {
	input.action == "block_client"
	not input.approved
}

decision := {"allow": count(reasons) == 0, "reasons": reasons}
