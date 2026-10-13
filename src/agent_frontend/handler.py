import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    try:
        bucket = os.environ["AGENT_HTML_BUCKET"]
        key = os.environ.get("AGENT_HTML_KEY", "agent.html")
        response = boto3.client("s3").get_object(Bucket=bucket, Key=key)
        body = response["Body"].read().decode("utf-8")
        return {
            "statusCode": 200,
            "headers": {
                "content-type": "text/html; charset=utf-8",
                "cache-control": "no-store",
            },
            "body": body,
            "isBase64Encoded": False,
        }
    except (BotoCoreError, ClientError, KeyError, UnicodeDecodeError, OSError):
        logger.warning(json.dumps({"status": 502, "resource": "agent.html"}))
        return {
            "statusCode": 502,
            "headers": {"content-type": "application/json"},
            "body": json.dumps({"error": "Unable to load demo page"}),
        }
