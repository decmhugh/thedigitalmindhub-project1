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

COOKIE_NAME = "auth_session"
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": status_code, "headers": {"content-type": "application/json"}, "body": json.dumps(body)}


def _cookie_token(event: dict[str, Any]) -> str | None:
    for cookie in event.get("cookies") or []:
        name, _, value = cookie.strip().partition("=")
        if name == COOKIE_NAME and TOKEN_PATTERN.match(value):
            return value
    return None


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    unauthenticated = _response(401, {"authenticated": False})
    token = _cookie_token(event)
    if token is None:
        return unauthenticated
    try:
        item = (
            boto3.client("dynamodb")
            .get_item(
                TableName=os.environ["AUTH_TABLE"],
                Key={"pk": {"S": "SESSION#" + hashlib.sha256(token.encode("utf-8")).hexdigest()}},
            )
            .get("Item")
        )
    except (BotoCoreError, ClientError, KeyError, OSError) as error:
        logger.warning(json.dumps({"route": "auth_session", "status": 502, "error_type": type(error).__name__}))
        return _response(502, {"error": "Unable to check session"})
    if not item or int(item["expires_at"]["N"]) <= int(time.time()):
        return unauthenticated
    return _response(200, {"authenticated": True, "phone_masked": item["phone_masked"]["S"]})
