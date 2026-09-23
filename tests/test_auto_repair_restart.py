from __future__ import annotations

from clipboard_agent.app import ClipboardAgentApp
from clipboard_agent.profiled_app import ProfiledClipboardAgentApp


def test_hard_stop_clears_inflight_repair_for_later_manual_restart(monkeypatch):
    app = object.__new__(ProfiledClipboardAgentApp)
    app._auto_repair_inflight = True
    stopped = []
    monkeypatch.setattr(
        ClipboardAgentApp,
        "_stop_auto",
        lambda self, reason, **kwargs: stopped.append((reason, kwargs)),
    )

    ProfiledClipboardAgentApp._hard_stop_auto(app, "fatal recovery failure", set_status=False)

    assert app._auto_repair_inflight is False
    assert stopped == [("fatal recovery failure", {"set_status": False})]
