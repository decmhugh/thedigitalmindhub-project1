import hashlib
import json
import logging
import os
import re
import time
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

AUTH_COOKIE_NAME = "auth_session"
AUTH_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
STALE_RUNNING_SECONDS = 360


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": status_code, "headers": {"content-type": "application/json", "cache-control": "no-store"}, "body": json.dumps(body)}


def _authenticated_user(event: dict[str, Any]) -> str | None:
    for cookie in event.get("cookies") or []:
        name, _, value = cookie.strip().partition("=")
        if name == AUTH_COOKIE_NAME and AUTH_TOKEN_PATTERN.match(value):
            key = {"pk": {"S": "SESSION#" + hashlib.sha256(value.encode("utf-8")).hexdigest()}}
            item = boto3.client("dynamodb").get_item(TableName=os.environ["AUTH_TABLE"], Key=key).get("Item")
            if item and int(item["expires_at"]["N"]) > int(time.time()):
                return item["phone_hash"]["S"]
    return None


def _job_body(item: dict[str, Any]) -> dict[str, Any]:
    status = item["status"]["S"]
    if status in ("pending", "running") and int(time.time()) - int(item["created_at"]["N"]) > STALE_RUNNING_SECONDS:
        return {"status": "failed", "error": "Agent request failed", "http_status": 502}
    body: dict[str, Any] = {"status": status}
    if status == "done":
        if item.get("blocked", {}).get("BOOL"):
            body["blocked"] = True
        else:
            body["response"] = item["response"]["S"]
    elif status == "failed":
        body["error"] = item["error"]["S"]
        body["http_status"] = int(item["http_status"]["N"])
    return body


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    try:
        user = _authenticated_user(event)
    except (BotoCoreError, ClientError, KeyError, ValueError, OSError) as error:
        logger.warning(json.dumps({"status": 502, "error_type": type(error).__name__}))
        return _response(502, {"error": "Agent request failed"})
    if user is None:
        return _response(401, {"error": "Sign in required"})

    job_id = (event.get("pathParameters") or {}).get("job_id")
    if not isinstance(job_id, str) or not JOB_ID_PATTERN.match(job_id):
        return _response(404, {"error": "Job not found"})
    try:
        item = boto3.client("dynamodb").get_item(
            TableName=os.environ["JOBS_TABLE"], Key={"pk": {"S": "JOB#" + job_id}}
        ).get("Item")
        if not item or item["owner"]["S"] != user or int(item["expires_at"]["N"]) < int(time.time()):
            return _response(404, {"error": "Job not found"})
        return _response(200, _job_body(item))
    except (BotoCoreError, ClientError, KeyError, ValueError, OSError) as error:
        logger.warning(json.dumps({"status": 502, "error_type": type(error).__name__}))
        return _response(502, {"error": "Agent request failed"})
