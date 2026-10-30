import base64
import hashlib
import json
import logging
import os
import re
import time
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError, ConnectTimeoutError, ReadTimeoutError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

IMAGE_MEDIA_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
DOCUMENT_MEDIA_TYPES = {
    "application/pdf",
    "text/plain",
    "text/csv",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MAX_IMAGE_BYTES = 3_750_000
MAX_DOCUMENT_BYTES = 4_500_000
KEY_PATTERN = re.compile(r"^u/[0-9a-f]{32}/[0-9a-f]{32}$")
SIZE_ERROR_TERMS = ("too long", "too large", "exceeds", "size", "limit")
MODEL_LIMIT_MESSAGE = "File exceeds the model size limit"
JOB_TTL_SECONDS = 3600
ERROR_MESSAGES = {422: "Request blocked by safety policy", 504: "Agent request timed out", 413: MODEL_LIMIT_MESSAGE}


class FileRejected(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


def _owner_prefix(user: str) -> str:
    return "u/" + hashlib.sha256(user.encode("utf-8")).hexdigest()[:32] + "/"


def _name_field(file: Any) -> dict[str, str]:
    name = file.get("name") if isinstance(file, dict) else None
    return {"name": name[:100]} if isinstance(name, str) and name.strip() else {}


def _load_file(file: Any, user: str) -> dict[str, str]:
    key = file.get("key") if isinstance(file, dict) else None
    if not isinstance(key, str) or not KEY_PATTERN.match(key) or not key.startswith(_owner_prefix(user)):
        raise FileRejected(400, "A valid file is required")
    bucket = os.environ["UPLOAD_BUCKET"]
    s3 = boto3.client("s3")
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            raise FileRejected(400, "A valid file is required") from error
        raise
    media_type = head.get("ContentType")
    if media_type in IMAGE_MEDIA_TYPES:
        kind, limit = "image", MAX_IMAGE_BYTES
    elif media_type in DOCUMENT_MEDIA_TYPES:
        kind, limit = "document", MAX_DOCUMENT_BYTES
    else:
        raise FileRejected(400, "A valid file is required")
    if head.get("ContentLength", 0) > limit:
        raise FileRejected(413, MODEL_LIMIT_MESSAGE)
    data = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    if not data:
        raise FileRejected(400, "A valid file is required")
    return {"mediaType": media_type, "data": base64.b64encode(data).decode("ascii"), "kind": kind, **_name_field(file)}


def _agent_text(result: dict[str, Any]) -> tuple[str, bool]:
    content = result.get("response", b"")
    if hasattr(content, "read"):
        content = content.read()
    if isinstance(content, bytes):
        content = content.decode("utf-8")
    if not isinstance(content, str):
        raise ValueError("Unexpected AgentCore response")
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError:
        return content, False
    if isinstance(decoded, dict) and isinstance(decoded.get("response"), str):
        return decoded["response"], decoded.get("blocked") is True
    if isinstance(decoded, str):
        return decoded, False
    raise ValueError("Unexpected AgentCore response")


def _error_status(error: Exception) -> int:
    if isinstance(error, (ReadTimeoutError, ConnectTimeoutError, TimeoutError)):
        return 504
    if isinstance(error, ClientError):
        code = error.response.get("Error", {}).get("Code", "").lower()
        if "guardrail" in code:
            return 422
        message = error.response.get("Error", {}).get("Message", "").lower()
        if code == "validationexception" and any(term in message for term in SIZE_ERROR_TERMS):
            return 413
    return 502


def _log(job_id: str, status: int, started: float, error: Exception | None = None) -> None:
    entry: dict[str, Any] = {
        "job": hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:12],
        "status": status,
        "latency_ms": round((time.monotonic() - started) * 1000),
    }
    if error is not None:
        entry["error_type"] = type(error).__name__
        if isinstance(error, ClientError):
            entry["error_code"] = error.response.get("Error", {}).get("Code", "")
            metadata = error.response.get("ResponseMetadata", {})
            entry["http_status"] = metadata.get("HTTPStatusCode")
            entry["request_id"] = metadata.get("RequestId")
            if entry["error_code"] == "RuntimeClientError":
                entry["hint"] = "Agent container raised an error; see log group /aws/bedrock-agentcore/runtimes/"
    logger.info(json.dumps(entry))


def process(job: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Run one agent request and return (http-style status, body)."""
    started = time.monotonic()
    job_id = job["job_id"]
    agent_payload: dict[str, Any] = {"prompt": job["prompt"]}
    try:
        if job.get("file") is not None:
            agent_payload["file"] = _load_file(job["file"], job["user"])
    except FileRejected as rejected:
        _log(job_id, rejected.status, started)
        return rejected.status, {"error": rejected.message}
    except (BotoCoreError, ClientError, KeyError, OSError) as error:
        _log(job_id, 502, started, error)
        return 502, {"error": "Agent request failed"}

    runtime_session_id = hashlib.sha256(f"{job['user']}:{job['session_id']}".encode("utf-8")).hexdigest()
    try:
        parameter_name = os.environ["AGENT_RUNTIME_ARN_PARAM"]
        runtime_arn = boto3.client("ssm").get_parameter(Name=parameter_name, WithDecryption=False)["Parameter"]["Value"]
        result = boto3.client("bedrock-agentcore").invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=runtime_session_id,
            payload=json.dumps(agent_payload).encode("utf-8"),
            qualifier="DEFAULT",
        )
        text, blocked = _agent_text(result)
    except (BotoCoreError, ClientError, KeyError, ValueError, OSError) as error:
        status_code = _error_status(error)
        _log(job_id, status_code, started, error)
        return status_code, {"error": ERROR_MESSAGES.get(status_code, "Agent request failed")}

    if blocked:
        _log(job_id, 422, started)
        return 422, {"error": ERROR_MESSAGES[422]}
    _log(job_id, 200, started)
    return 200, {"response": text}


def _finish(job: dict[str, Any], status: int, body: dict[str, Any]) -> None:
    ddb = boto3.client("dynamodb")
    table = os.environ["JOBS_TABLE"]
    expires = str(int(time.time()) + JOB_TTL_SECONDS)
    values: dict[str, Any] = {":expires": {"N": expires}, ":http": {"N": str(status)}}
    if status == 200:
        values[":status"] = {"S": "done"}
        values[":response"] = {"S": body["response"]}
        values[":blocked"] = {"BOOL": False}
        expression = "SET #s = :status, response = :response, blocked = :blocked, http_status = :http, expires_at = :expires"
    elif status == 422:
        values[":status"] = {"S": "done"}
        values[":blocked"] = {"BOOL": True}
        expression = "SET #s = :status, blocked = :blocked, http_status = :http, expires_at = :expires"
    else:
        values[":status"] = {"S": "failed"}
        values[":error"] = {"S": body["error"]}
        expression = "SET #s = :status, #e = :error, http_status = :http, expires_at = :expires"
    names = {"#s": "status"}
    if ":error" in values:
        names["#e"] = "error"
    ddb.update_item(
        TableName=table,
        Key={"pk": {"S": "JOB#" + job["job_id"]}},
        UpdateExpression=expression,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    job = event
    try:
        boto3.client("dynamodb").update_item(
            TableName=os.environ["JOBS_TABLE"],
            Key={"pk": {"S": "JOB#" + job["job_id"]}},
            UpdateExpression="SET #s = :running",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":running": {"S": "running"}},
        )
        status, body = process(job)
        _finish(job, status, body)
    except Exception as error:  # noqa: BLE001 - last resort so the job never stays running
        logger.error(json.dumps({"status": 502, "error_type": type(error).__name__}))
        try:
            _finish(job, 502, {"error": "Agent request failed"})
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            boto3.client("dynamodb").delete_item(TableName=os.environ["JOBS_TABLE"], Key={"pk": {"S": job["lock_pk"]}})
        except Exception as error:  # noqa: BLE001
            logger.warning(json.dumps({"error_type": type(error).__name__, "step": "unlock"}))
    return {"ok": True}
