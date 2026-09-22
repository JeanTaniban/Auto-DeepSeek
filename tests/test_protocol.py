from pathlib import Path

import pytest

from clipboard_agent.models import ExecutionResult, ExecutionStatus
from clipboard_agent.protocol import ProtocolError, format_result, parse_execution, resolve_cwd, truncate_output


def test_parse_valid_execution():
    text = """Je vérifie d'abord le projet.\n#Execution\nID: inspect-1\nCWD: .\nTimeout: 30\n\n```powershell\ngit status --short\n```\n"""
    req = parse_execution(text)
    assert req is not None
    assert req.request_id == "inspect-1"
    assert req.command == "git status --short"
    assert req.cwd == "."
    assert req.timeout == 30
    assert req.shell == "powershell"


def test_no_marker_returns_none():
    assert parse_execution("bonjour") is None


def test_multiple_execution_markers_rejected():
    text = "#Execution\n```powershell\npwd\n```\n#Execution\n```powershell\ndir\n```"
    with pytest.raises(ProtocolError):
        parse_execution(text)


def test_missing_fence_rejected():
    with pytest.raises(ProtocolError):
        parse_execution("#Execution\npwd")


def test_timeout_is_capped():
    req = parse_execution("#Execution\nTimeout: 999999\n```bash\npwd\n```", max_timeout=60)
    assert req.timeout == 60


def test_resolve_cwd_inside_project(tmp_path: Path):
    child = tmp_path / "src"
    child.mkdir()
    assert resolve_cwd(tmp_path, "src") == child.resolve()


def test_resolve_cwd_outside_project_rejected(tmp_path: Path):
    with pytest.raises(ProtocolError):
        resolve_cwd(tmp_path, "..")


def test_format_result():
    result = ExecutionResult("x1", ExecutionStatus.SUCCESS, 0, 1.2, Path("."), "ok\n", "", "echo ok")
    text = format_result(result, goal_reminder="Finish the app")
    assert "#ExecutionResult" in text
    assert "Status: SUCCESS" in text
    assert "#GoalReminder" in text


def test_truncate_output():
    value = "x" * 1000
    out = truncate_output(value, 300)
    assert "OUTPUT TRUNCATED" in out
    assert len(out) < 500


def test_parse_one_click_copy_box():
    text = """```text
#Execution
ID: copy-1
Shell: powershell
CWD: .
Timeout: 45

git status --short
```"""
    req = parse_execution(text)
    assert req is not None
    assert req.request_id == "copy-1"
    assert req.shell == "powershell"
    assert req.timeout == 45
    assert req.command == "git status --short"


def test_parse_one_click_copy_box_with_intro():
    text = """Je vérifie l'état du dépôt.

```text
#Execution
ID: copy-2
CWD: .

git status
```"""
    req = parse_execution(text, default_shell="bash")
    assert req is not None
    assert req.shell == "bash"
    assert req.command == "git status"


def test_parse_deepseek_copy_button_payload_without_markdown_fences():
    text = """#Execution
ID: inspect-root
Shell: powershell
CWD: .
Timeout: 30

Get-ChildItem -Force"""
    req = parse_execution(text)
    assert req is not None
    assert req.request_id == "inspect-root"
    assert req.shell == "powershell"
    assert req.cwd == "."
    assert req.timeout == 30
    assert req.command == "Get-ChildItem -Force"


def test_parse_bare_copy_payload_multiline_command():
    text = """#Execution
ID: multiline-1
Shell: powershell
CWD: .
Timeout: 30

@'
hello
'@ | Set-Content test.txt"""
    req = parse_execution(text)
    assert req is not None
    assert req.command == "@'\nhello\n'@ | Set-Content test.txt"


def test_bare_execution_without_protocol_metadata_is_rejected():
    with pytest.raises(ProtocolError):
        parse_execution("#Execution\n\npwd")


def test_parse_show_directive_raw_copy():
    from clipboard_agent.models import DirectiveKind
    from clipboard_agent.protocol import parse_agent_directive

    text = """#Show
ID: demo-1
Shell: powershell
CWD: .
Timeout: 120

cargo run"""
    directive = parse_agent_directive(text)
    assert directive is not None
    assert directive.kind == DirectiveKind.SHOW
    assert directive.request is not None
    assert directive.request.request_id == "demo-1"
    assert directive.request.command == "cargo run"


def test_parse_end_directive_with_summary():
    from clipboard_agent.models import DirectiveKind
    from clipboard_agent.protocol import parse_agent_directive

    directive = parse_agent_directive("#End\n\nTests OK. Jeu terminé.")
    assert directive is not None
    assert directive.kind == DirectiveKind.END
    assert "Tests OK" in directive.summary


def test_multiple_control_directives_rejected():
    from clipboard_agent.protocol import parse_agent_directive

    with pytest.raises(ProtocolError):
        parse_agent_directive("#Execution\nID: a\n\npwd\n#End")


def test_show_fenced_copy_box():
    from clipboard_agent.models import DirectiveKind
    from clipboard_agent.protocol import parse_agent_directive

    text = """```text
#Show
ID: show-2
Shell: powershell
CWD: .
Timeout: 30

python game.py
```"""
    directive = parse_agent_directive(text)
    assert directive is not None
    assert directive.kind == DirectiveKind.SHOW
    assert directive.request is not None
    assert directive.request.command == "python game.py"


def test_parse_multiple_sequence_raw_copy():
    from clipboard_agent.models import DirectiveKind, InteractionKind
    from clipboard_agent.protocol import parse_agent_directive

    text = '''#Multiple
ID: ui-1
Shell: powershell
CWD: .
Timeout: 90
Launch: cargo run

#Observe initial
#Click 300;240
#TypeInput "test"
#Key CTRL+S
#Wait 500
#Observe apres
'''
    directive = parse_agent_directive(text)
    assert directive is not None
    assert directive.kind == DirectiveKind.MULTIPLE
    assert directive.request is not None
    assert directive.request.request_id == "ui-1"
    assert directive.request.command == "cargo run"
    assert [action.kind for action in directive.actions] == [
        InteractionKind.OBSERVE,
        InteractionKind.CLICK,
        InteractionKind.TYPE_INPUT,
        InteractionKind.KEY,
        InteractionKind.WAIT,
        InteractionKind.OBSERVE,
    ]
    assert directive.actions[1].x == 300
    assert directive.actions[1].y == 240
    assert directive.actions[2].text == "test"
    assert directive.actions[3].key == "CTRL+S"
    assert directive.actions[4].wait_ms == 500


def test_parse_multiple_accepts_typeinout_alias():
    from clipboard_agent.models import InteractionKind
    from clipboard_agent.protocol import parse_agent_directive

    text = '''```text
#Multiple
ID: ui-typo
CWD: .
Launch: python app.py

#Typeinout "bonjour\\nmonde"
```'''
    directive = parse_agent_directive(text)
    assert directive is not None
    assert directive.actions[0].kind == InteractionKind.TYPE_INPUT
    assert directive.actions[0].text == "bonjour\nmonde"


def test_multiple_without_launch_is_test_actions_alias_and_requires_actions():
    from clipboard_agent.models import DirectiveKind, InteractionKind
    from clipboard_agent.protocol import parse_agent_directive

    directive = parse_agent_directive("#Multiple\nID: a\nCWD: .\n\n#Observe")
    assert directive is not None
    assert directive.kind == DirectiveKind.TEST_ACTIONS
    assert directive.actions[0].kind == InteractionKind.OBSERVE
    with pytest.raises(ProtocolError, match="aucune liste d'actions|au moins une action"):
        parse_agent_directive("#Multiple\nID: a\nLaunch: app.exe\n")


def test_multiple_rejects_negative_click_and_global_shortcut():
    from clipboard_agent.protocol import parse_agent_directive

    with pytest.raises(ProtocolError, match="coordonnées"):
        parse_agent_directive("#Multiple\nID: a\nLaunch: app.exe\n\n#Click -1;20")
    with pytest.raises(ProtocolError, match="global"):
        parse_agent_directive("#Multiple\nID: a\nLaunch: app.exe\n\n#Key ALT+TAB")


def test_multiple_rejects_more_than_25_actions():
    from clipboard_agent.protocol import parse_agent_directive

    actions = "\n".join("#Wait 1" for _ in range(26))
    with pytest.raises(ProtocolError, match="25 actions"):
        parse_agent_directive(f"#Multiple\nID: a\nLaunch: app.exe\n\n{actions}")


def test_parse_open_test_session_with_readiness_and_initial_observe():
    from clipboard_agent.models import DirectiveKind, InteractionKind
    from clipboard_agent.protocol import parse_agent_directive

    text = '''#OpenTestSession
ID: gui-1
Shell: powershell
CWD: .
Timeout: 45
Launch: python app.py
Ready: checkpoint:main-ready

#Observe startup
'''
    directive = parse_agent_directive(text)
    assert directive is not None
    assert directive.kind == DirectiveKind.OPEN_TEST_SESSION
    assert directive.request is not None
    assert directive.request.request_id == "gui-1"
    assert directive.request.command == "python app.py"
    assert directive.ready == "checkpoint:main-ready"
    assert directive.actions[0].kind == InteractionKind.OBSERVE


def test_parse_test_actions_close_and_single_action_shortcut():
    from clipboard_agent.models import DirectiveKind, InteractionKind
    from clipboard_agent.protocol import parse_agent_directive

    actions = parse_agent_directive('''#TestActions
ID: act-1

#Click 20;30
#TypeInput "hello"
#Observe after
''')
    assert actions is not None
    assert actions.kind == DirectiveKind.TEST_ACTIONS
    assert actions.request_id == "act-1"
    assert [a.kind for a in actions.actions] == [InteractionKind.CLICK, InteractionKind.TYPE_INPUT, InteractionKind.OBSERVE]

    single = parse_agent_directive("#Key ENTER")
    assert single is not None
    assert single.kind == DirectiveKind.TEST_ACTIONS
    assert single.actions[0].kind == InteractionKind.KEY

    close = parse_agent_directive("#CloseTestSession\nID: close-1")
    assert close is not None
    assert close.kind == DirectiveKind.CLOSE_TEST_SESSION
    assert close.request_id == "close-1"


def test_open_test_session_ready_modes_are_validated():
    from clipboard_agent.protocol import parse_agent_directive

    for ready in ("auto", "content", "window", "delay:1500", "checkpoint:ready-ui"):
        directive = parse_agent_directive(
            f"#OpenTestSession\nID: x\nLaunch: app.exe\nReady: {ready}\n"
        )
        assert directive.ready == ready
    with pytest.raises(ProtocolError, match="Ready invalide"):
        parse_agent_directive("#OpenTestSession\nID: x\nLaunch: app.exe\nReady: magic\n")


def test_parse_relay_v2_execution_and_show():
    from clipboard_agent.models import DirectiveKind
    from clipboard_agent.protocol import parse_agent_directive

    execution = parse_agent_directive("""#Relay
Protocol: 2
Action: EXECUTION
ID: inspect-v2
Shell: powershell
CWD: .
Timeout: 45

git status --short
""")
    assert execution is not None
    assert execution.kind == DirectiveKind.EXECUTION
    assert execution.request is not None
    assert execution.request.request_id == "inspect-v2"
    assert execution.request.command == "git status --short"

    show = parse_agent_directive("""#Relay
Protocol: 2
Action: SHOW
ID: show-v2
CWD: .

python main.py
""")
    assert show is not None
    assert show.kind == DirectiveKind.SHOW
    assert show.request is not None
    assert show.request.command == "python main.py"


def test_parse_relay_v2_test_session_actions_close_temp_and_end():
    from clipboard_agent.models import DirectiveKind, InteractionKind
    from clipboard_agent.protocol import parse_agent_directive

    opened = parse_agent_directive("""#Relay
Protocol: 2
Action: OPEN_TEST_SESSION
ID: open-v2
Shell: powershell
CWD: .
Timeout: 120
Launch: python main.py
Ready: content

#Observe startup
""")
    assert opened is not None
    assert opened.kind == DirectiveKind.OPEN_TEST_SESSION
    assert opened.ready == "content"
    assert opened.request is not None
    assert opened.request.command == "python main.py"
    assert opened.actions[0].kind == InteractionKind.OBSERVE

    actions = parse_agent_directive("""#Relay
Protocol: 2
Action: TEST_ACTIONS
ID: act-v2

#Click 20;30
#Observe after
""")
    assert actions is not None
    assert actions.kind == DirectiveKind.TEST_ACTIONS
    assert actions.request_id == "act-v2"
    assert [a.kind for a in actions.actions] == [InteractionKind.CLICK, InteractionKind.OBSERVE]

    closed = parse_agent_directive("""#Relay
Protocol: 2
Action: CLOSE_TEST_SESSION
ID: close-v2
""")
    assert closed is not None
    assert closed.kind == DirectiveKind.CLOSE_TEST_SESSION
    assert closed.request_id == "close-v2"

    temporary = parse_agent_directive("""#Relay
Protocol: 2
Action: TEMP_TEST
ID: temp-v2
CWD: .
Launch: python main.py

#Observe once
""")
    assert temporary is not None
    assert temporary.kind == DirectiveKind.MULTIPLE
    assert temporary.request is not None
    assert temporary.request.command == "python main.py"

    ended = parse_agent_directive("""#Relay
Protocol: 2
Action: END
ID: end-v2

Mission validée.
""")
    assert ended is not None
    assert ended.kind == DirectiveKind.END
    assert ended.request_id == "end-v2"
    assert ended.summary == "Mission validée."


def test_relay_v2_strict_validation_and_prose_safety():
    from clipboard_agent.protocol import parse_agent_directive

    assert parse_agent_directive("Le marqueur suivant est seulement cité :\n#Relay\nmais sans bloc canonique.") is None

    with pytest.raises(ProtocolError, match="Protocol: 2"):
        parse_agent_directive("#Relay\nProtocol: 1\nAction: END\nID: x\n")

    with pytest.raises(ProtocolError, match="Action #Relay inconnue"):
        parse_agent_directive("#Relay\nProtocol: 2\nAction: MAGIC\nID: x\n")

    with pytest.raises(ProtocolError, match="Métadonnée"):
        parse_agent_directive("#Relay\nProtocol: 2\nAction: TEST_ACTIONS\nID: x\nCWD: .\n\n#Observe")

    with pytest.raises(ProtocolError, match="ID"):
        parse_agent_directive("#Relay\nProtocol: 2\nAction: END\n")


def test_parse_execution_accepts_relay_v2_execution():
    req = parse_execution("""#Relay
Protocol: 2
Action: EXECUTION
ID: exec-v2
CWD: .

pwd
""", default_shell="bash")
    assert req is not None
    assert req.request_id == "exec-v2"
    assert req.shell == "bash"
    assert req.command == "pwd"


def test_execution_result_has_common_relay_v2_header():
    result = ExecutionResult("x2", ExecutionStatus.SUCCESS, 0, 0.1, Path("."), "ok", "", "echo ok")
    text = format_result(result)
    assert text.startswith("#RelayResult\nProtocol: 2\nKind: EXECUTION")
    assert "LegacyMarker: #ExecutionResult" in text
    assert "Status: SUCCESS" in text


def test_relay_v2_fenced_block_must_be_the_entire_message():
    from clipboard_agent.models import DirectiveKind
    from clipboard_agent.protocol import parse_agent_directive

    fence = chr(96) * 3
    exact = (
        fence + "text\n"
        + "#Relay\nProtocol: 2\nAction: EXECUTION\nID: fenced-v2\nCWD: .\n\ngit status\n"
        + fence
    )
    directive = parse_agent_directive(exact)
    assert directive is not None
    assert directive.kind == DirectiveKind.EXECUTION
    assert directive.request is not None
    assert directive.request.request_id == "fenced-v2"

    with pytest.raises(ProtocolError, match="uniquement le bloc copiable"):
        parse_agent_directive("Je vérifie le dépôt.\n\n" + exact)

    with pytest.raises(ProtocolError, match="uniquement le bloc copiable"):
        parse_agent_directive(exact + "\n\nTerminé.")

    duplicate = (
        fence + "text\n#Relay\nProtocol: 2\nAction: END\nID: one\n" + fence
        + "\n\n"
        + fence + "text\n#Relay\nProtocol: 2\nAction: END\nID: two\n" + fence
    )
    with pytest.raises(ProtocolError, match="Plusieurs directives #Relay"):
        parse_agent_directive(duplicate)


def test_typeinput_preserves_unicode_and_key_accepts_accented_character():
    from clipboard_agent.models import InteractionKind
    from clipboard_agent.protocol import parse_agent_directive

    directive = parse_agent_directive("""#Relay
Protocol: 2
Action: TEST_ACTIONS
ID: unicode-input

#TypeInput "été déjà reçu — 5€ 😀"
#Key é
""")
    assert directive is not None
    assert directive.actions[0].kind == InteractionKind.TYPE_INPUT
    assert directive.actions[0].text == "été déjà reçu — 5€ 😀"
    assert directive.actions[1].kind == InteractionKind.KEY
    assert directive.actions[1].key == "é"
