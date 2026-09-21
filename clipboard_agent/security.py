from __future__ import annotations

import re

from .models import RiskLevel, SecurityDecision


BLOCKED_PATTERNS = [
    (r"(?i)\bformat\b\s+[a-z]:", "Formatage de volume détecté"),
    (r"(?i)\bdiskpart\b", "Gestion destructive de disque détectée"),
    (r"(?i)\bshutdown\b|\brestart-computer\b|\bstop-computer\b", "Arrêt/redémarrage système détecté"),
    (r"(?i)\bbcdedit\b|\bbootrec\b", "Modification du démarrage système détectée"),
]

SENSITIVE_PATTERNS = [
    (r"(?i)\bgit\s+reset\s+--hard\b", "Git reset --hard"),
    (r"(?i)\bgit\s+clean\b", "Nettoyage Git potentiellement destructif"),
    (r"(?i)\bgit\s+push\b", "Envoi vers un dépôt distant"),
    (r"(?i)\brm\s+-[^\n]*r", "Suppression récursive"),
    (r"(?i)\bremove-item\b[^\n]*(?:-recurse|-force)", "Suppression PowerShell récursive/forcée"),
    (r"(?i)\bdel\b[^\n]*/s\b|\brmdir\b[^\n]*/s\b", "Suppression récursive"),
    (r"(?i)\bsudo\b|\brunas\b", "Élévation de privilèges"),
    (r"(?i)\breg\s+(?:add|delete)\b|\bset-itemproperty\b.*registry", "Modification du registre"),
]

MODIFY_PATTERNS = [
    (r"(?i)\bgit\s+(?:add|commit|checkout|switch|merge|rebase|restore)\b", "Modification de l'état Git"),
    (r"(?i)\b(?:pip|python\s+-m\s+pip|uv)\s+install\b", "Installation de dépendances Python"),
    (r"(?i)\b(?:npm|pnpm|yarn)\s+(?:install|add|remove|update)\b", "Modification de dépendances JavaScript"),
    (r"(?i)\b(?:mkdir|md|new-item|touch|copy-item|move-item|rename-item)\b", "Modification de fichiers/répertoires"),
]

LOW_PATTERNS = [
    r"(?i)^\s*(?:git\s+(?:status|diff|log|show|branch)|python\s+(?:--version|-V)|python\s+-m\s+pytest|pytest\b|npm\s+(?:test|run\s+test|run\s+build)|pnpm\s+(?:test|build)|yarn\s+(?:test|build)|cargo\s+(?:test|check|build)|go\s+test\b|dotnet\s+(?:test|build)|dir\b|ls\b|pwd\b|get-childitem\b)"
]

CHAIN_RE = re.compile(r"(?:&&|\|\||;|\n\s*(?![#`]))")


def classify_command(command: str) -> SecurityDecision:
    text = command.strip()
    for pattern, reason in BLOCKED_PATTERNS:
        if re.search(pattern, text):
            return SecurityDecision(RiskLevel.BLOCKED, reason, True)
    for pattern, reason in SENSITIVE_PATTERNS:
        if re.search(pattern, text):
            return SecurityDecision(RiskLevel.SENSITIVE, reason, True)
    for pattern, reason in MODIFY_PATTERNS:
        if re.search(pattern, text):
            return SecurityDecision(RiskLevel.MODIFY, reason, True)
    for pattern in LOW_PATTERNS:
        if re.search(pattern, text):
            if CHAIN_RE.search(text):
                return SecurityDecision(RiskLevel.MODIFY, "Commande composée : confirmation recommandée", True)
            return SecurityDecision(RiskLevel.LOW, "Commande de lecture/test reconnue", False)
    return SecurityDecision(RiskLevel.MODIFY, "Commande non reconnue : confirmation recommandée", True)
