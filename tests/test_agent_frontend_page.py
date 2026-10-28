from pathlib import Path

import pytest

PAGE = Path(__file__).parent.parent / "src" / "agent_frontend" / "agent.html"


@pytest.fixture(scope="module")
def html():
    return PAGE.read_text(encoding="utf-8")


def test_page_uses_pinned_jquery_cdn(html):
    assert 'src="https://code.jquery.com/jquery-3.7.1.min.js"' in html
    assert 'integrity="sha256-/JqT3SQfawRcv/BIHPThkBvs0OEvtFFmqPF/lYI/Cxo="' in html


def test_page_has_no_password_cognito_or_token_handling(html):
    lowered = html.lower()
    for marker in ("password", "cognito", "authorization", "idtoken", "bearer"):
        assert marker not in lowered


def test_page_implements_sms_sign_in_flow(html):
    for marker in ('"/auth/session"', '"/auth/start"', '"/auth/verify"', '"/auth/logout"'):
        assert marker in html
    assert 'id="phone"' in html and 'id="code"' in html
    assert "phone" in html and "code" in html


def test_page_returns_to_sign_in_on_401(html):
    assert "401" in html


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


def test_page_has_image_attachment_controls(html):
    assert 'type="file"' in html
    assert 'accept="image/png,image/jpeg,image/gif,image/webp"' in html
    assert 'id="attach-button"' in html
    assert 'id="image-preview"' in html
    assert 'id="remove-image-button"' in html


def test_page_enforces_image_limits_and_sends_image(html):
    assert "3750000" in html
    assert "mediaType" in html
    assert "payload.image" in html
    assert "FileReader" in html
