# Letting `ic-bench` use the GPU quota in Stockholm

AWS approved 4 vCPUs of **Spot** capacity for G and VT instances in **eu-north-1 (Stockholm)**
(15 Oct 2026): exactly one 4-vCPU GPU machine (g4dn/g5/g6.xlarge). The `ic-bench` user is locked
to us-east-1, so it needs a second, narrow policy. Apply it once, signed in as the account owner.

[`gpu-stockholm-policy.json`](gpu-stockholm-policy.json) allows, **only in eu-north-1**: our own
CloudFormation stacks (`ic-gpu*`), EC2 (including Spot) with a **hard deny on any instance type
except** g4dn/g5/g6.xlarge, c7i.xlarge, m7i.xlarge, t3.medium/large, our own role and instance
profile, an idle-stop alarm, Session Manager into instances tagged
`project=incident-commander-gpu`, and read-only cost/quota checks (credits, prices, quotas).

## Steps (about 3 minutes)

1. Console → **IAM** → **Policies** → **Create policy** → **JSON**.
2. Replace everything with the file's contents, with `ACCOUNT_ID` replaced by the account number
   (Shahesta: a ready-filled copy is in the chat).
3. **Next** → name: `ic-gpu-stockholm` → **Create policy**.
4. IAM → **Users** → `ic-bench` → **Add permissions** → **Attach policies directly** → tick
   `ic-gpu-stockholm` → **Add permissions**.
5. Check from the laptop: `~/ic-bench.sh credits` still works, and
   `aws --profile ic-bench --region eu-north-1 ec2 describe-spot-price-history --instance-types g6.xlarge --max-items 3`
   now answers instead of "UnauthorizedOperation".

Too long for an inline user policy (2,048-character limit), hence a managed policy.
