package testbed

import rego.v1

# What the agent may do on its own. Evaluated by OPA, outside the agent, before every action.

allowlist := {
	"rollback_release:worker", "rollback_release:collector",
	"restart:worker", "restart:collector", "restart:api",
	"kill_blocking_sessions:postgres", "restore_redis_maxmemory:redis",
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

decision := {"allow": count(reasons) == 0, "reasons": reasons}
