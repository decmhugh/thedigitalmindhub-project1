from pathlib import Path


PAGE = Path(__file__).parent.parent / "src" / "agent_frontend" / "agent.html"


def test_agent_page_uses_pinned_jquery_cdn_and_authenticated_api():
    html = PAGE.read_text(encoding="utf-8")

    assert 'src="https://code.jquery.com/jquery-3.7.1.min.js"' in html
    assert 'integrity="sha256-/JqT3SQfawRcv/BIHPThkBvs0OEvtFFmqPF/lYI/Cxo="' in html
    assert "AWSCognitoIdentityProviderService.InitiateAuth" in html
    assert 'AuthFlow: "USER_AUTH"' in html
    assert 'PREFERRED_CHALLENGE: "PASSWORD"' in html
    assert "AWSCognitoIdentityProviderService.RespondToAuthChallenge" in html
    assert 'ChallengeName: "PASSWORD"' in html
    assert "USER_PASSWORD_AUTH" not in html
    assert '"/agent/invoke"' in html
    assert "Authorization" in html


def test_agent_page_does_not_persist_credentials_or_tokens():
    html = PAGE.read_text(encoding="utf-8").lower()

    assert "localstorage" not in html
    assert "sessionstorage" not in html
    assert "document.cookie" not in html
