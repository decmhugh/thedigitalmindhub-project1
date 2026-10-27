import hashlib
import json
import logging
import os
import re
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

COOKIE_NAME = "auth_session"
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
CLEARED_COOKIE = f"{COOKIE_NAME}=; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=0"


def _cookie_token(event: dict[str, Any]) -> str | None:
    for cookie in event.get("cookies") or []:
        name, _, value = cookie.strip().partition("=")
        if name == COOKIE_NAME and TOKEN_PATTERN.match(value):
            return value
    return None


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    token = _cookie_token(event)
    if token is not None:
        try:
            boto3.client("dynamodb").delete_item(
                TableName=os.environ["AUTH_TABLE"],
                Key={"pk": {"S": "SESSION#" + hashlib.sha256(token.encode("utf-8")).hexdigest()}},
            )
        except (BotoCoreError, ClientError, KeyError, OSError) as error:
            logger.warning(json.dumps({"route": "auth_logout", "error_type": type(error).__name__}))
    return {"statusCode": 204, "headers": {"content-type": "application/json"}, "body": "", "cookies": [CLEARED_COOKIE]}
