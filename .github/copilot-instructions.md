# Copilot / AI agent instructions — cost-guardian

This project is an AWS CDK Python app that deploys a scheduled Lambda which queries Cost Explorer,
writes state/history to DynamoDB, and publishes SNS alerts on breaches. Keep suggestions and edits
small, focused, and testable. Below are the concrete patterns and conventions an AI coding agent
should follow when contributing.

1. Big-picture architecture
   - The CDK app is `app.py` and defines `CostGuardianStack` in `cost_guardian/cost_guardian_stack.py`.
   - The stack creates:
     - A DynamoDB state table (latest state) and a `cost_history` table for historical records.
     - An SNS topic `cost-guardian-alerts`.
     - A Lambda function (code in `lambda/handler.py`) scheduled every 15 minutes.
   - Data flow: Lambda -> AWS Cost Explorer (ce:GetCostAndUsage) -> DynamoDB (latest + history) -> SNS when breach.

2. Key files to reference when changing behavior
   - `lambda/handler.py` — primary logic: cost read, threshold check, DynamoDB writes, SNS publish.
   - `cost_guardian/cost_guardian_stack.py` — resource wiring, environment variables, IAM grants.
   - `app.py` — CDK entrypoint; used by `cdk synth` / `cdk deploy`.
   - `tests/test_handler.py` — examples of unit tests and common mocking patterns.

3. Environment variables and feature flags (use these exact names)
   - `TABLE_NAME` — latest state table name
   - `COST_HISTORY_TABLE` — history table name (table name in stack is `cost_history`)
   - `ALERTS_TOPIC_ARN` — SNS topic ARN
   - `THRESHOLD` — float USD/day threshold (default `0.10`)
   - `COST_EXPLORER_GRANULARITY` — default `DAILY`
   - `COST_EXPLORER_METRIC` — default `UnblendedCost`
   - `HISTORY_TTL_DAYS` — integer TTL for history items (default `30`)
   - `ENABLE_DAILY_PK` — feature flag (true/false) to write per-day partition keys (pk=`COST#YYYY-MM-DD`)

4. Testing and mocking patterns
   - Unit tests in `tests/` patch `boto3.client` and `boto3.resource` (see `tests/test_handler.py`).
   - Tests assume the handler module is importable as `handler` (the handler source is in `lambda/handler.py`).
     To run tests locally on Windows PowerShell, set PYTHONPATH to the `lambda` folder. Example:

       $env:PYTHONPATH = "lambda";
       python -m unittest discover -s tests -v

   - When adding code that calls AWS APIs, ensure unit tests patch `boto3.client`/`boto3.resource` the same way.
   - The Lambda handler file contains fallback exception classes for static analysis; keep those when editing.

5. CDK / local developer workflow
   - Create/activate venv and install deps:
       python -m venv .venv
       .\.venv\Scripts\activate.bat
       pip install -r requirements.txt
   - Synthesize and deploy (run from repository `cost-guardian` folder):
       cdk synth
       cdk deploy
   - CDK stack uses RemovalPolicy.DESTROY on DynamoDB tables — this repo is wired for development/testing, not production.

6. Project-specific patterns to follow
   - Use Decimal for DynamoDB numeric writes (see `_d()` helper in `lambda/handler.py`). Avoid writing floats directly.
   - History items: always write a global `pk: COST` record; optionally write a `pk: COST#YYYY-MM-DD` when `ENABLE_DAILY_PK`.
   - The stack grants explicit read/write to the Lambda for each table (use the same explicit grant pattern when adding resources).
   - Lambda runtime is pinned to `PYTHON_3_12` in the stack — maintain compatibility with 3.12 features.

7. Safety and infra notes
   - The stack grants `ce:GetCostAndUsage` on `*` — when changing permissions, preserve this intent.
   - Because tables use `RemovalPolicy.DESTROY`, be cautious when changing `table_name` or deployment in shared accounts.

8. When making changes, supply these artifacts
   - Updated or new unit tests (mock boto3 clients as in `tests/test_handler.py`).
   - A short README note if you introduce a new environment variable or change table names.
   - For CDK changes, a successful `cdk synth` output summary or failing synth errors to iterate.

If anything here is unclear or you want more detail (for example, exact test commands for PowerShell vs. WSL), tell me which sections to expand or correct.
