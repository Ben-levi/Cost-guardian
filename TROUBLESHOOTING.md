# CostGuardian Troubleshooting

This document captures the most common failure modes for CostGuardian deployments and operations.

---

## 1) GitHub OIDC: `Not authorized to perform sts:AssumeRoleWithWebIdentity`

### Symptoms
- GitHub Actions step `aws-actions/configure-aws-credentials@v4` fails with:
  - `Not authorized to perform sts:AssumeRoleWithWebIdentity`

### Root causes / fixes
1) **Wrong IAM OIDC provider URL**
   - Must be: `https://token.actions.githubusercontent.com`
   - A common typo is adding `...githubusercontentcontent.com` (extra “content”), which breaks issuer matching.

2) **Role trust policy doesn't match repo/ref**
   - Trust policy condition `sub` must match the workflow’s ref:
     - `repo:Ben-levi/Cost-guardian:ref:refs/heads/main` for `main`
     - Broaden carefully if you need PRs/tags:
       - `repo:Ben-levi/Cost-guardian:ref:refs/heads/*`

3) **Workflow missing OIDC permissions**
   - Ensure:
     ```yaml
     permissions:
       id-token: write
       contents: read
     ```

4) **Role doesn’t exist**
   - AWS error `NoSuchEntity` on `GetRole` means you never created the role (or you used the wrong ARN/name).

---

## 2) GitHub deploy: push rejected / merge in progress

### Symptoms
- `git push` rejected with non-fast-forward
- `git pull` fails with `MERGE_HEAD exists`

### Fix
- Conclude the merge:
  - `git add <files>`
  - `git commit`
- Then:
  - `git pull --rebase origin main` (if needed)
  - `git push origin main`

---

## 3) DynamoDB queries returning 0 items

### Symptom
- Query by pk like `COST#YYYY-MM-DD` returns `Count: 0`

### Root cause
Your current configuration has:
- `ENABLE_DAILY_PK=false`

So history items are stored as:
- `pk = "COST"`
- `sk = "MINUTE#YYYY-MM-DDTHH:MMZ"`

### Fix (query correctly)
Use `pk="COST"` and optionally filter by day prefix on the sort key.

---

## 4) PowerShell JSON quoting issues with AWS CLI (DynamoDB)

### Symptoms
- AWS CLI errors like:
  - `Invalid JSON: Expecting property name enclosed in double quotes`
  - AWS shows it received `{key:{S:latest}}` instead of `{"key":{"S":"latest"}}`

### Fix
Pass JSON via `file://...` and write files **without UTF-8 BOM**:
```powershell
[System.IO.File]::WriteAllText($path,'{"key":{"S":"latest"}}',(New-Object System.Text.UTF8Encoding($false)))
aws dynamodb get-item --table-name <TABLE> --key ("file://$path") --region us-east-1
```

---

## 5) SNS alerts not arriving

### Checklist
1) Subscription must be **confirmed**:
```powershell
aws sns list-subscriptions-by-topic --topic-arn <TOPIC_ARN> --region us-east-1
```

2) Test delivery:
```powershell
aws sns publish --topic-arn <TOPIC_ARN> --subject "test" --message "hello" --region us-east-1
```

3) Check Lambda logs for:
- `breach alert suppressed by cooldown` (means it *did not* publish)
- any `Publish`/alert log lines

---

## 6) Alerts suppressed by cooldown (expected behavior)

### Symptom
- Logs show: `breach alert suppressed by cooldown`

### Explanation
Cooldown is controlled by:
- `ALERT_COOLDOWN_MINUTES` (e.g. 240)
- State key: `last_breach_alert`

---

## 7) Enforcement not stopping instances (expected in safe mode)

### If you see
- `enforcement result` with `matched: []`
- status `DRY_RUN`

### Reasons
- No running instances have the tag key/value (default: `CostGuardianManaged=true`)
- `ENFORCEMENT_DRY_RUN=true` prevents stopping
- `ENFORCEMENT_ARMED=false` blocks stopping

### Safe test approach
1) Keep `DRY_RUN=true`, `ARMED=false`
2) Tag a single test instance
3) Confirm it appears in `describe-instances` filter
4) Confirm enforcement log records it as “would stop”
5) Only then consider disabling dry-run / arming (change-controlled)

---

## 8) Cost Explorer numbers differ from expectations

### Notes
- `ResultsByTime[].Estimated=true` indicates AWS is still estimating the period’s spend.
- With `COST_EXPLORER_GRANULARITY=MONTHLY`, the cost is month-to-date and may not change every run.
- For daily monitoring, prefer `DAILY` granularity and query completed days (requires stack/code choice).
