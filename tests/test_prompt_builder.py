from pathlib import Path

from clipboard_agent.prompt_builder import build_initial_prompt


def test_prompt_has_goal_and_single_execution_rule():
    prompt = build_initial_prompt(Path("C:/Project"), "Ship a stable desktop app", shell="powershell", os_name="Windows")
    assert "Ship a stable desktop app" in prompt
    assert "EXACTEMENT UNE exécution" in prompt
    assert "attends le prochain message `#ExecutionResult`" in prompt
    assert "Ne fabrique jamais un résultat" in prompt


def test_prompt_explains_auto_show_and_end_protocol():
    prompt = build_initial_prompt(Path("C:/Project"), "Créer et tester un jeu", shell="powershell", os_name="Windows")
    assert "#Show" in prompt
    assert "#End" in prompt
    assert "UNE SEULE directive" in prompt
    assert "attends le prochain `#ExecutionResult`" in prompt
    assert "ARRÊTERA le mode Agent Auto" in prompt
    assert "réponse ou juste après" in prompt
    assert "n'attends aucun message spécial" in prompt


def test_prompt_explains_multiple_target_app_protocol():
    prompt = build_initial_prompt(Path("C:/Project"), "Tester une interface", shell="powershell", os_name="Windows")
    assert "#Multiple" in prompt
    assert "#Click X;Y" in prompt
    assert "#TypeInput" in prompt
    assert "#Observe" in prompt
    assert "ZONE CLIENTE" in prompt
    assert "ne te permet PAS de réfléchir" in prompt
    assert "#MultipleResult" in prompt
    assert "ferme la cible" in prompt


def test_prompt_explains_persistent_test_session_and_checkpoints():
    prompt = build_initial_prompt(Path("C:/Project"), "Valider l'UI", shell="powershell", os_name="Windows")
    assert "#OpenTestSession" in prompt
    assert "#CloseTestSession" in prompt
    assert "#TestActions" in prompt
    assert "SessionActive: YES" in prompt
    assert "Ready: auto" in prompt
    assert "Ready: content" in prompt
    assert "checkpoint:<nom>" in prompt
    assert "[[CAR_CHECKPOINT:main-window-ready]]" in prompt
    assert "[[CAR_SCREENSHOT:menu-open]]" in prompt
    assert "même application est toujours ouverte" in prompt
    assert "observe → attends le résultat → réfléchis → agis → observe" in prompt
    assert "N'utilise JAMAIS `#Wait` pour deviner" in prompt
    assert "OBSERVATION_WARNINGS" in prompt


def test_prompt_explains_deterministic_llm_and_target_workspaces():
    prompt = build_initial_prompt(Path("C:/Project"), "Tester", shell="powershell", os_name="Windows")
    assert "Z-order" in prompt
    assert "HWND" in prompt
    assert "LLM workspace" in prompt
    assert "Target workspace" in prompt
    assert "mouvement physique de souris" in prompt
