from pathlib import Path

from clipboard_agent.prompt_builder import build_initial_prompt


def _prompt():
    return build_initial_prompt(
        Path("C:/Project"),
        "Ship a stable desktop app",
        shell="powershell",
        os_name="Windows",
    )


def test_prompt_starts_with_goal_environment_and_simple_control_loop():
    prompt = _prompt()
    assert "Ship a stable desktop app" in prompt
    assert "Action dans le tableau" in prompt
    assert "RÈGLE DE FORMAT ABSOLUE" in prompt
    assert "TA RÉPONSE ENTIÈRE doit être exactement UN SEUL bloc copiable" in prompt
    assert "RIEN avant le bloc" in prompt
    assert "RIEN après le bloc" in prompt
    assert "exactement un `#Relay`" in prompt
    assert "Relay V2 rejettera volontairement la réponse" in prompt
    assert "Attends le prochain `#RelayResult`" in prompt
    assert "Ne fabrique jamais stdout" in prompt


def test_prompt_teaches_one_canonical_top_level_protocol():
    prompt = _prompt()
    assert "`#Relay` est l'unique marqueur de contrôle top-level" in prompt
    assert "Protocol: 2" in prompt
    assert "Action: EXECUTION" in prompt
    assert "Action: OPEN_TEST_SESSION" in prompt
    assert "Action: TEST_ACTIONS" in prompt
    assert "Action: CLOSE_TEST_SESSION" in prompt
    assert "Action: TEMP_TEST" in prompt
    assert "Action: SHOW" in prompt
    assert "Action: END" in prompt

    # Historical aliases stay parser-only and are no longer choices presented
    # to the agent.
    assert "#Multiple" not in prompt
    assert "#OpenTestSession" not in prompt
    assert "#TestActions" not in prompt
    assert "#CloseTestSession" not in prompt
    assert "#Show" not in prompt
    assert "#End" not in prompt
    assert "#Typeinout" not in prompt


def test_prompt_explains_persistent_testing_without_guessing_timings():
    prompt = _prompt()
    assert "Ready: auto" in prompt
    assert "`content`" in prompt
    assert "checkpoint:<nom>" in prompt
    assert "[[CAR_CHECKPOINT:main-window-ready]]" in prompt
    assert "[[CAR_SCREENSHOT:menu-open]]" in prompt
    assert "N'ajoute pas de `#Wait` « au cas où »" in prompt
    assert "`#Observe label`" in prompt
    assert "ZONE CLIENTE" in prompt
    assert "saisie Unicode" in prompt
    assert "`é`, `à`, `ç`" in prompt
    assert "layout clavier Windows actif" in prompt


def test_prompt_explains_result_state_and_recommended_next_action():
    prompt = _prompt()
    assert "#RelayResult" in prompt
    assert "LegacyMarker" in prompt
    assert "SessionState" in prompt
    assert "RecommendedNext" in prompt
    assert "ACTIVE_BACKGROUND" in prompt
    assert "LOST" in prompt
    assert "OBSERVATION_WARNINGS" in prompt
    assert "l'image n'est PAS une preuve visuelle fiable" in prompt


def test_prompt_preserves_workspace_safety_and_development_discipline():
    prompt = _prompt()
    assert "HWND" in prompt
    assert "mouvement physique de souris" in prompt
    assert "petites briques testables" in prompt
    assert "Préfère les tests automatisés" in prompt
    assert "Action: EXECUTION non destructive" in prompt
