from pathlib import Path

import pytest

PAGE = Path(__file__).parent.parent / "src" / "agent_frontend" / "agent.html"


@pytest.fixture(scope="module")
def html():
    return PAGE.read_text(encoding="utf-8")


def test_page_uses_pinned_jquery_cdn(html):
    assert 'src="https://code.jquery.com/jquery-3.7.1.min.js"' in html
    assert 'integrity="sha256-/JqT3SQfawRcv/BIHPThkBvs0OEvtFFmqPF/lYI/Cxo="' in html


def test_page_has_no_login_ui_or_cognito(html):
    lowered = html.lower()
    for marker in ("sign-in", "sign in", "sign out", "password", "username", "cognito", "authorization", "idtoken"):
        assert marker not in lowered


def test_chat_is_enabled_on_load(html):
    assert "<textarea" in html and "disabled" not in html.split("<textarea", 1)[1].split(">", 1)[0]
    assert 'aria-disabled="true"' not in html


def test_page_posts_to_invoke_and_relies_on_session_cookie(html):
    assert '"/agent/invoke"' in html
    assert "new_session" in html
    assert "session_id" not in html
    assert "localstorage" not in html.lower()
    assert "sessionstorage" not in html.lower()
    assert "document.cookie" not in html.lower()


def test_page_handles_rate_limit(html):
    assert "429" in html
