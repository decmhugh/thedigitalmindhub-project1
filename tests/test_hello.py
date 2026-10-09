import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "hello"))

from handler import lambda_handler  # noqa: E402


def test_hello():
    res = lambda_handler({}, None)
    assert res["statusCode"] == 200
    assert json.loads(res["body"])["message"] == "hello"
