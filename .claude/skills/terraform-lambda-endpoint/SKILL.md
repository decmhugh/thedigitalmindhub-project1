---
name: terraform-lambda-endpoint
description: Add a new API endpoint as its own AWS Lambda function with Terraform. Use when asked to create, add, or scaffold an API endpoint, route, or Lambda in this project.
---

# Add a Lambda endpoint

0. Require an approved `specs/NNN-<feature>/` (spec, plan, tasks). If missing, use the `spec-driven-development` skill first.
1. Create `src/<name>/handler.py` exposing `lambda_handler(event, context)` that returns `{"statusCode": int, "body": json-string}`.
2. Add a unit test in `tests/test_<name>.py`.
3. In `infra/main.tf`, add a module call:

   ```hcl
   module "<name>" {
     source        = "./modules/lambda_endpoint"
     name          = "<name>"
     source_dir    = "${path.root}/../src/<name>"
     route_key     = "GET /<path>"
     api_id        = aws_apigatewayv2_api.this.id
     api_exec_arn  = aws_apigatewayv2_api.this.execution_arn
     environment   = { LOG_LEVEL = "INFO" }
     policy_json   = null # supply least-privilege IAM policy JSON if AWS access is needed
   }
   ```
4. Run `terraform -chdir=infra fmt -recursive`, `terraform -chdir=infra validate`, `tflint --chdir=infra`, and `pytest tests`.
5. Show the `plan` output to the user. Do NOT apply.
