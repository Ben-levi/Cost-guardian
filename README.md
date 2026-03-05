# CostGuardian

CostGuardian is a serverless AWS cost-monitoring tool that queries AWS Cost Explorer on a schedule, records cost history in DynamoDB, and sends alerts via SNS when spend exceeds a configured threshold. Optionally, it can **enforce** cost controls by stopping tagged EC2 instances (with safety switches like *dry-run* and *armed* mode).

> **Repo:** Ben-levi/Cost-guardian  
> **Deploy:** AWS CDK + GitHub Actions (OIDC)

---

## What it does

- Pulls spend from **AWS Cost Explorer** (`GetCostAndUsage`).
- Persists:
  - **Latest state** (for cooldown/idempotency) in a DynamoDB state table.
  - **Time-series history** in a DynamoDB history table (minute-level entries in the current configuration).
  - **Enforcement actions** in a DynamoDB enforcement log table.
- Sends an **SNS alert** when spend is above threshold (with cooldown to reduce spam).
- Optionally stops **EC2 instances** that match a tag filter.

---

## Architecture

**EventBridge Schedule** → **Lambda (CostGuardian Monitor)**  
Lambda calls:
- **Cost Explorer** (read cost)
- **DynamoDB**
  - State table: `latest`, `last_breach_alert`, etc.
  - History table: cost entries (pk/sk; see Troubleshooting)
  - Enforcement log: records enforcement results
- **SNS** topic: breach notifications
- **EC2** (optional): describe/stop instances when enforcement is enabled

---

## Deployment (CDK + GitHub Actions)

### Prerequisites
- AWS account with Cost Explorer enabled (Cost Explorer API permissions required).
- AWS CDK v2
- A GitHub Actions OIDC role in AWS (see `TROUBLESHOOTING.md` for common OIDC pitfalls).

### GitHub Actions setup (high level)
1. Create an IAM role that GitHub Actions can assume via OIDC (trusts `token.actions.githubusercontent.com`).
2. In GitHub repo settings, add secret:
   - `AWS_ROLE_TO_ASSUME` = role ARN (e.g. `arn:aws:iam::<acct>:role/GitHubActionsDeployRole`)
3. Workflow permissions must include:
   - `id-token: write`
   - `contents: read`

### Deploy
- On push to `main`, the workflow runs tests + `cdk synth`.
- Deployment is gated by the GitHub **Environment** (e.g. `prod`) and runs `cdk deploy`.

---

## Runtime configuration (Lambda Environment Variables)

These are set by CDK and can be adjusted in the stack (recommended), or via Lambda configuration if you manage it manually.

### Cost collection
- `COST_EXPLORER_GRANULARITY`: `MONTHLY` or `DAILY`
- `COST_EXPLORER_METRIC`: e.g. `UnblendedCost`
- `THRESHOLD`: numeric string (e.g. `0.01`)
- `ENABLE_DAILY_PK`: `true|false` (history partition strategy)
- `ENABLE_MONTHLY_ROLLUP`: `true|false`

### Persistence
- `TABLE_NAME`: DynamoDB state table name
- `COST_HISTORY_TABLE`: DynamoDB history table name
- `HISTORY_TTL_DAYS`: e.g. `30`
- `MONTHLY_ROLLUP_TTL_DAYS`: e.g. `400`

### Alerts
- `ALERTS_TOPIC_ARN`: SNS topic ARN
- `ALERT_COOLDOWN_MINUTES`: e.g. `240`
- `ALERT_COOLDOWN_KEY`: e.g. `last_breach_alert`
- `PENDING_ALERT_KEY`: e.g. `alert_pending`

### Enforcement (optional)
- `ENFORCEMENT_ENABLED`: `true|false`
- `ENFORCEMENT_DRY_RUN`: `true|false`
- `ENFORCEMENT_ARMED`: `true|false` (final safety switch)
- `ENFORCEMENT_REGIONS`: e.g. `us-east-1` (comma-separated supported)
- `ENFORCEMENT_TAG_KEY`: e.g. `CostGuardianManaged`
- `ENFORCEMENT_TAG_VALUE`: e.g. `true`
- `ENFORCEMENT_LOG_TABLE`: DynamoDB table name for enforcement log

---

## Quick links

- **Operations / verification:** `RUNBOOK.md`
- **Common failures:** `TROUBLESHOOTING.md`
