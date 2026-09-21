from clipboard_agent.models import RiskLevel
from clipboard_agent.security import classify_command


def test_low_risk_commands():
    assert classify_command("git status --short").risk == RiskLevel.LOW
    assert classify_command("python -m pytest").risk == RiskLevel.LOW


def test_modify_command():
    assert classify_command("git add .").risk == RiskLevel.MODIFY
    assert classify_command("pip install requests").risk == RiskLevel.MODIFY


def test_sensitive_command():
    assert classify_command("git reset --hard HEAD~1").risk == RiskLevel.SENSITIVE
    assert classify_command("git push origin main").risk == RiskLevel.SENSITIVE


def test_blocked_command():
    assert classify_command("diskpart").risk == RiskLevel.BLOCKED
