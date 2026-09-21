from clipboard_agent.redaction import redact_secrets


def test_redacts_env_secret():
    out = redact_secrets("OPENAI_API_KEY=sk-abcdefghijklmnop")
    assert "sk-abcdefghijklmnop" not in out
    assert "[REDACTED]" in out


def test_redacts_bearer():
    out = redact_secrets("Authorization: Bearer abc.def.ghi")
    assert "abc.def.ghi" not in out
