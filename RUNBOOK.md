# CostGuardian Runbook

This runbook is for operating and validating CostGuardian in production.

> Region used in examples: `us-east-1`  
> Shell: **PowerShell** (Windows)

---

## 1) Identify resources

### 1.1 Stack health
```powershell
$Region="us-east-1"
$StackName="CostGuardianStack"
aws cloudformation describe-stacks --stack-name $StackName --region $Region
```

### 1.2 Resolve deployed resource names from the stack
```powershell
$Region="us-east-1"
$StackName="CostGuardianStack"
$resources = (aws cloudformation describe-stack-resources --stack-name $StackName --region $Region | ConvertFrom-Json).StackResources

$lambdaFn = ($resources | Where-Object { $_.LogicalResourceId -like "MonitorLambda*" -and $_.ResourceType -eq "AWS::Lambda::Function" }).PhysicalResourceId
$ruleName = ($resources | Where-Object { $_.LogicalResourceId -like "MonitorSchedule*" -and $_.ResourceType -eq "AWS::Events::Rule" }).PhysicalResourceId
$topicArn = ($resources | Where-Object { $_.LogicalResourceId -like "AlertsTopic*" -and $_.ResourceType -eq "AWS::SNS::Topic" }).PhysicalResourceId

"Lambda: $lambdaFn"
"Rule:   $ruleName"
"SNS:    $topicArn"
```

---

## 2) Verify it’s running (end-to-end)

### 2.1 CloudWatch metrics: invocations + errors
```powershell
$Region="us-east-1"
$Fn="<YOUR_LAMBDA_NAME>"
$End=(Get-Date).ToUniversalTime()
$Start=$End.AddHours(-2)

aws cloudwatch get-metric-statistics `
  --namespace AWS/Lambda --metric-name Invocations `
  --dimensions Name=FunctionName,Value=$Fn `
  --start-time $Start.ToString("o") --end-time $End.ToString("o") `
  --period 300 --statistics Sum --region $Region

aws cloudwatch get-metric-statistics `
  --namespace AWS/Lambda --metric-name Errors `
  --dimensions Name=FunctionName,Value=$Fn `
  --start-time $Start.ToString("o") --end-time $End.ToString("o") `
  --period 300 --statistics Sum --region $Region
```

### 2.2 Duration + throttles
```powershell
$Region="us-east-1"
$Fn="<YOUR_LAMBDA_NAME>"
$End=(Get-Date).ToUniversalTime()
$Start=$End.AddHours(-6)

aws cloudwatch get-metric-statistics `
  --namespace AWS/Lambda --metric-name Duration `
  --dimensions Name=FunctionName,Value=$Fn `
  --start-time $Start.ToString("o") --end-time $End.ToString("o") `
  --period 300 --statistics Average Maximum --region $Region

aws cloudwatch get-metric-statistics `
  --namespace AWS/Lambda --metric-name Throttles `
  --dimensions Name=FunctionName,Value=$Fn `
  --start-time $Start.ToString("o") --end-time $End.ToString("o") `
  --period 300 --statistics Sum --region $Region
```

### 2.3 Tail logs (latest stream)
```powershell
$Region="us-east-1"
$Fn="<YOUR_LAMBDA_NAME>"
$logGroup="/aws/lambda/$Fn"

$stream = (aws logs describe-log-streams --log-group-name $logGroup --order-by LastEventTime --descending --max-items 1 --region $Region |
  ConvertFrom-Json).logStreams[0].logStreamName

aws logs get-log-events --log-group-name $logGroup --log-stream-name $stream --limit 50 --region $Region |
  ConvertFrom-Json | Select-Object -ExpandProperty events | ForEach-Object { $_.message }
```

---

## 3) Verify data is being written

### 3.1 State table keys: `latest` and `last_breach_alert`
**PowerShell tip:** pass DynamoDB key JSON via `file://...` (avoid quoting/BOM issues).

```powershell
$Region="us-east-1"
$StateTable="<STATE_TABLE_NAME>"

$latestFile = Join-Path $env:TEMP "ddb-key-latest.json"
$cooldownFile = Join-Path $env:TEMP "ddb-key-cooldown.json"
[System.IO.File]::WriteAllText($latestFile,   '{"key":{"S":"latest"}}',            (New-Object System.Text.UTF8Encoding($false)))
[System.IO.File]::WriteAllText($cooldownFile, '{"key":{"S":"last_breach_alert"}}',(New-Object System.Text.UTF8Encoding($false)))

aws dynamodb get-item --table-name $StateTable --key ("file://$latestFile")   --region $Region
aws dynamodb get-item --table-name $StateTable --key ("file://$cooldownFile") --region $Region
```

### 3.2 History table: newest entries
Depending on configuration, history can be stored with different partitioning strategies.

If `ENABLE_DAILY_PK=false`, history uses:
- `pk="COST"`
- `sk="MINUTE#YYYY-MM-DDTHH:MMZ"`

```powershell
$env:AWS_PAGER=""
$Region="us-east-1"
$HistoryTable="<HISTORY_TABLE_NAME>"

$eavJson = (@{":pk"=@{S="COST"}} | ConvertTo-Json -Compress)
$eavFile = Join-Path $env:TEMP "ddb-eav-cost.json"
[System.IO.File]::WriteAllText($eavFile, $eavJson, (New-Object System.Text.UTF8Encoding($false)))

aws dynamodb query `
  --table-name $HistoryTable `
  --key-condition-expression "pk = :pk" `
  --expression-attribute-values ("file://$eavFile") `
  --no-scan-index-forward `
  --limit 10 `
  --region $Region `
  --output table `
  --query "Items[].{sk:sk.S,cost:cost.N,status:status.S}"
```

To filter to a specific day:
```powershell
$Day="2026-03-05"
$eavJson = (@{
  ":pk"=@{S="COST"}
  ":prefix"=@{S=("MINUTE#{0}" -f $Day)}
} | ConvertTo-Json -Compress)

$eavFile = Join-Path $env:TEMP "ddb-eav-day.json"
[System.IO.File]::WriteAllText($eavFile, $eavJson, (New-Object System.Text.UTF8Encoding($false)))

aws dynamodb query `
  --table-name $HistoryTable `
  --key-condition-expression "pk = :pk AND begins_with(sk, :prefix)" `
  --expression-attribute-values ("file://$eavFile") `
  --no-scan-index-forward `
  --limit 10 `
  --region $Region `
  --output table `
  --query "Items[].{sk:sk.S,cost:cost.N,status:status.S}"
```

---

## 4) Alerts not arriving (SNS)

### 4.1 Confirm subscriptions
```powershell
$Region="us-east-1"
$TopicArn="<SNS_TOPIC_ARN>"
aws sns get-topic-attributes --topic-arn $TopicArn --region $Region
aws sns list-subscriptions-by-topic --topic-arn $TopicArn --region $Region
```

### 4.2 Send a test message
```powershell
$Region="us-east-1"
$TopicArn="<SNS_TOPIC_ARN>"
aws sns publish --topic-arn $TopicArn --subject "CostGuardian SNS test" --message "If you got this, SNS delivery works." --region $Region
```

---

## 5) Cooldown behavior (how it works)

CostGuardian stores a cooldown marker in the **state table** (key `last_breach_alert`) so repeated breaches don’t spam notifications.

When spend is above the threshold:
- If the last alert time is within `ALERT_COOLDOWN_MINUTES`, it logs something like **“breach alert suppressed by cooldown”** and does not publish.
- Otherwise, it publishes to SNS and updates the cooldown key.

---

## 6) Safe enforcement testing (DRY_RUN / ARMED)

**Safety switches:**
- `ENFORCEMENT_ENABLED=true` enables evaluation.
- `ENFORCEMENT_DRY_RUN=true` logs what it *would* do, but doesn’t stop instances.
- `ENFORCEMENT_ARMED=false` is an additional safety block (recommended to keep false until you trust the system).

### 6.1 Check for candidate instances (tag-based)
```powershell
$Region="us-east-1"
aws ec2 describe-instances `
  --filters "Name=tag:CostGuardianManaged,Values=true" "Name=instance-state-name,Values=running" `
  --query "Reservations[].Instances[].{Id:InstanceId,State:State.Name,Tags:Tags}" `
  --output table `
  --region $Region
```

### 6.2 Confirm enforcement log records activity
```powershell
$Region="us-east-1"
$EnfTable="<ENFORCEMENT_LOG_TABLE>"
aws dynamodb scan --table-name $EnfTable --max-items 10 --region $Region
```

---

## 7) Common operational checklist

- [ ] Lambda Invocations > 0 and Errors = 0
- [ ] Latest log stream shows “collected” message
- [ ] State table has `latest` and `last_breach_alert` keys
- [ ] History table is filling (new MINUTE entries)
- [ ] SNS subscription is **confirmed**
- [ ] Enforcement stays in DRY_RUN until you explicitly arm it
