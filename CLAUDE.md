# AWS Invoice Extraction

Invoice extraction pipeline. Mock invoices (text PDFs and scanned images) are
uploaded to S3, an EventBridge rule triggers a Python Lambda that calls
Claude on Amazon Bedrock for structured extraction, plain-Python rules validate the
result, and records are written to Aurora PostgreSQL Serverless v2 through the RDS
Data API. Textract (and optionally Bedrock Data Automation) are compared on the same
invoices. All infrastructure is plain CloudFormation (not SAM or CDK).

## Environment

- Dev machine is Windows 11 with PowerShell. Give PowerShell commands, not bash, and
  never `sudo`. Use `curl.exe` rather than the `curl` alias.
- AWS region `ap-southeast-1` (Singapore). The CLI profile is `invoice`, signed in with
  `aws login --profile invoice` (short-lived credentials). Pass `--profile invoice
  --region ap-southeast-1` explicitly, and never create or store access keys.
- Local Python is managed with uv (`pyproject.toml`, `uv.lock`, Python 3.14). Lambda
  dependencies go in a separate requirements file, installed as Linux wheels
  (`pip install --platform manylinux2014_aarch64 --only-binary=:all:`). No Docker.
- Git default branch is `main`.
- Possible corporate SSL inspection: if pip, uv or boto3 fail with certificate errors,
  use the system trust store (for example `truststore`, or `uv --native-tls`) rather
  than disabling verification.

## Rules

- Public repo: never commit secrets, `.env`, AWS keys, the AWS account ID, IPs, or
  personal Windows paths (use `%USERPROFILE%` or `<placeholders>` in docs). Templates
  use `${AWS::AccountId}`, and scripts read the account from the profile.
- All AWS resources come from CloudFormation templates. No console click-ops, except
  the one-off Free Tier credit activities.
- AWS changes fast. Check AWS docs for current (2026) behaviour before using a
  service feature, a model ID or a console path, and say when something is unverified.
- Bedrock: call Claude through an inference profile (for example
  `global.anthropic.claude-haiku-5-5`). A bare model ID returns a 400 error. Use the
  Converse API with forced tool use for structured output. Keep model ID and prompt
  version in config, not hard-coded.
- Keep costs near zero: no NAT Gateway, no public IPv4 addresses, no always-on
  compute. Aurora minimum capacity is 0 ACU. Set CloudWatch log retention, and allow
  stacks to delete cleanly (the S3 bucket is emptied before `delete-stack`).
- Validation rules are deterministic Python and never call an LLM. Don't trust the
  model's self-reported confidence; the validation results are the confidence signal.
- Use only synthetic or public invoice data. Never use real company invoices.
- Keep scope small: no chat or text-to-SQL interface, no hosted UI, no MCP server.
- Keep dependencies pinned to a major-version range.
- Keep PROJECT_LOG.txt current: what was built, errors and fixes, costs, and key
  concepts learned, sanitised the same way as the repo.

## Code review priorities

1. Security: committed credentials or account IDs, over-broad IAM policies (no `*`
   actions or resources without a reason), SQL built by string formatting instead of
   Data API parameters.
2. Cost: resources that bill while idle (NAT Gateway, public IPv4, provisioned
   capacity), missing log retention, stacks that can't be deleted.
3. Correctness: validation arithmetic (rounding of money, 9% GST), duplicate detection
   on (vendor, invoice number), and unhandled Bedrock, Textract or Data API errors.

Skip style nitpicks.
