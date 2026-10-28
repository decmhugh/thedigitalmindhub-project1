import base64
import hashlib
import json
import logging
import os
import re
import time
import uuid
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

AUTH_COOKIE_NAME = "auth_session"
AUTH_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
ALLOWED_MEDIA_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "application/pdf",
    "text/plain",
    "text/csv",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
URL_EXPIRY_SECONDS = 300
ENCRYPTION = "AES256"


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": status_code, "headers": {"content-type": "application/json"}, "body": json.dumps(body)}


def _body(event: dict[str, Any]) -> dict[str, Any] | None:
    body = event.get("body")
    if event.get("isBase64Encoded") and isinstance(body, str):
        try:
            body = base64.b64decode(body, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
    if not isinstance(body, str):
        return None
    try:
        decoded = json.loads(body)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _authenticated_user(event: dict[str, Any]) -> str | None:
    for cookie in event.get("cookies") or []:
        name, _, value = cookie.strip().partition("=")
        if name == AUTH_COOKIE_NAME and AUTH_TOKEN_PATTERN.match(value):
            key = {"pk": {"S": "SESSION#" + hashlib.sha256(value.encode("utf-8")).hexdigest()}}
            item = boto3.client("dynamodb").get_item(TableName=os.environ["AUTH_TABLE"], Key=key).get("Item")
            if item and int(item["expires_at"]["N"]) > int(time.time()):
                return item["phone_hash"]["S"]
    return None


def owner_prefix(owner: str) -> str:
    return "u/" + hashlib.sha256(owner.encode("utf-8")).hexdigest()[:32] + "/"


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    try:
        user = _authenticated_user(event)
    except (BotoCoreError, ClientError, KeyError, ValueError, OSError) as error:
        logger.warning(json.dumps({"event": "upload_url_failed", "error_type": type(error).__name__}))
        return _response(502, {"error": "Upload request failed"})
    if user is None:
        return _response(401, {"error": "Sign in required"})

    body = _body(event)
    media_type = body.get("mediaType") if body else None
    size = body.get("size") if body else None
    if (
        media_type not in ALLOWED_MEDIA_TYPES
        or not isinstance(size, int)
        or isinstance(size, bool)
        or not 1 <= size <= MAX_UPLOAD_BYTES
    ):
        return _response(400, {"error": "A supported mediaType and a size up to 25 MB are required"})

    key = owner_prefix(user) + uuid.uuid4().hex
    try:
        post = boto3.client("s3").generate_presigned_post(
            Bucket=os.environ["UPLOAD_BUCKET"],
            Key=key,
            Fields={"Content-Type": media_type, "x-amz-server-side-encryption": ENCRYPTION},
            Conditions=[
                {"Content-Type": media_type},
                {"x-amz-server-side-encryption": ENCRYPTION},
                ["content-length-range", 1, size],
            ],
            ExpiresIn=URL_EXPIRY_SECONDS,
        )
    except (BotoCoreError, ClientError, KeyError, OSError) as error:
        logger.error(json.dumps({"event": "upload_url_failed", "error_type": type(error).__name__}))
        return _response(502, {"error": "Upload request failed"})
    logger.info(json.dumps({"event": "upload_url_issued", "status": 200}))
    return _response(200, {"url": post["url"], "fields": post["fields"], "key": key, "expiresIn": URL_EXPIRY_SECONDS})
