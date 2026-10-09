# Constitution

Non-negotiable principles; plans must comply.

1. **Spec first**: no code or Terraform without an approved spec, plan, and tasks.
2. **One Lambda per endpoint**: no monolithic or Flask-style API layer.
3. **Least privilege**: one IAM role per Lambda, scoped actions and resources.
4. **Infrastructure as code only**: no console changes; all resources in Terraform.
5. **Test-first**: acceptance criteria become pytest tests before implementation.
6. **No secrets in repo**: SSM Parameter Store or Secrets Manager.
7. **Observable**: explicit log groups with retention; structured JSON logs.
8. **Safe changes**: `plan` is reviewed by a human; `apply` and `destroy` require explicit approval.
