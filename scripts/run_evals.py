"""Run deterministic eval cases against the AgentCore runtime and publish results to CloudWatch.

Usage: python scripts/run_evals.py --runtime-arn <arn> --namespace project1-dev/agent-evals
"""

import argparse
import json
import sys
import uuid
from pathlib import Path

CASES_PATH = Path(__file__).resolve().parents[1] / "evals" / "cases.json"


def check_case(case: dict, result: dict) -> bool:
    if case.get("expect_blocked"):
        return bool(result.get("blocked"))
    if result.get("blocked"):
        return False
    text = str(result.get("response", "")).lower()
    return all(expected.lower() in text for expected in case.get("expect_contains", []))


def run(cases: list[dict], invoke, publish, namespace: str) -> int:
    passed = 0
    for case in cases:
        try:
            ok = check_case(case, invoke(case["prompt"]))
        except Exception as error:
            print(f"FAIL {case['name']}: {type(error).__name__}")
            continue
        print(f"{'PASS' if ok else 'FAIL'} {case['name']}")
        passed += ok

    failed = len(cases) - passed
    publish(
        namespace,
        [
            {"MetricName": "Passed", "Value": passed, "Unit": "Count"},
            {"MetricName": "Failed", "Value": failed, "Unit": "Count"},
            {"MetricName": "PassRate", "Value": 100.0 * passed / len(cases) if cases else 0.0, "Unit": "Percent"},
        ],
    )
    return failed


def _invoke_factory(client, runtime_arn: str):
    def invoke(prompt: str) -> dict:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=uuid.uuid4().hex + uuid.uuid4().hex,
            payload=json.dumps({"prompt": prompt}).encode("utf-8"),
            qualifier="DEFAULT",
        )
        body = response["response"].read().decode("utf-8")
        try:
            data = json.loads(body)
        except ValueError:
            return {"response": body}
        return data if isinstance(data, dict) else {"response": str(data)}

    return invoke


def main() -> int:
    import boto3

    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-arn", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--region", default=None)
    args = parser.parse_args()

    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    agentcore = boto3.client("bedrock-agentcore", region_name=args.region)
    cloudwatch = boto3.client("cloudwatch", region_name=args.region)
    publish = lambda namespace, metrics: cloudwatch.put_metric_data(Namespace=namespace, MetricData=metrics)
    return 1 if run(cases, _invoke_factory(agentcore, args.runtime_arn), publish, args.namespace) else 0


if __name__ == "__main__":
    sys.exit(main())
