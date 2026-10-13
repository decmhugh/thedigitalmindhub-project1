import json
from src.hello.handler import lambda_handler


def test_hello():
    res = lambda_handler({}, None)
    assert res["statusCode"] == 200
    assert json.loads(res["body"])["message"] == "hello"
