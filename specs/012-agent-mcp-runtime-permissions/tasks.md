# Tasks: Allow MCP Lambda to invoke the agent runtime

- [x] T1 Add a regression test that checks the MCP IAM policy includes only the scoped SSM read and AgentCore invoke permissions and resources.
- [x] T2 Add the two least-privilege IAM statements to `infra/agent_mcp.tf` and pass the targeted regression test.
- [x] T3 Run Terraform formatting, validation, targeted and full pytest.
- [ ] T4 Run tflint/checkov and inspect the Terraform plan. Blocked: tflint/checkov are unavailable in this environment, and Terraform plan requires AWS credentials that are not configured.
- [ ] T5 Present the plan for human review; do not apply without explicit approval.
