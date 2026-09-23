# Mission — Agent Auto vraiment autonome pour TestSession et capture cible

**Base :** `main` / `2ee388c3`  
**Branche :** `fix/auto-testsession-lifecycle-capture`

## Constat issu du test réel

Le test utilisateur met en évidence deux défauts de fonctionnement :

1. une fenêtre cible peut être techniquement détectée mais rester visuellement sous Clipboard Agent Relay au moment d'une observation, ce qui rend la capture non fiable ;
2. la plomberie de `TestSession` remonte trop directement au LLM : une session `LOST` ou une session précédente encore ouverte peut forcer le LLM à émettre un `CLOSE_TEST_SESSION`, et le Relay peut arrêter Agent Auto sur un simple conflit de cycle de vie.

Le mode Auto doit gérer ces détails lui-même. Le LLM doit exprimer le test qu'il souhaite effectuer, pas réparer l'état interne du Relay.

## Objectifs

- Garantir que la cible est réellement préparée au premier plan immédiatement avant chaque lecture visuelle liée à une TestSession persistante.
- Préserver le comportement TEMP_TEST existant : Relay démoté, Target activée pendant la séquence et capture cliente avec réactivation de la cible.
- En Agent Auto, remplacer automatiquement une TestSession existante lorsqu'un nouveau `OPEN_TEST_SESSION` est demandé.
- En Agent Auto, nettoyer automatiquement une TestSession persistante avant une action incompatible (`EXECUTION`, `TEMP_TEST`, `SHOW`) au lieu d'arrêter Auto.
- Rendre `CLOSE_TEST_SESSION` idempotent lorsque la session est déjà fermée.
- Transformer les `TEST_ACTIONS` sur session absente/perdue en résultat structuré récupérable, sans arrêter Auto.
- Ne jamais envoyer au LLM les résultats techniques des fermetures internes utilisées uniquement pour réconcilier l'état.
- Normaliser une session `LOST` ordinaire vers `CLOSED` après nettoyage local avant le prochain round-trip LLM.
- Clarifier le prompt : le cycle de vie TestSession est géré par Relay ; `OPEN_TEST_SESSION` signifie « je veux une session fraîche » et peut remplacer l'ancienne.

## Invariants de sécurité

- Une intervention physique de l'utilisateur conserve la priorité et peut toujours suspendre/arrêter l'automatisation.
- Une restauration impossible du workspace LLM reste une erreur bloquante : aucune poursuite automatique avec focus incertain.
- Une fermeture interne ne doit jamais relancer une commande sensible ou bloquée sans repasser par la classification existante.
- Aucune fenêtre ne doit être déplacée ou redimensionnée pour résoudre la capture ; seuls focus, TOPMOST et Z-order sont gérés.
- Le protocole Relay V2 reste compatible : aucune action existante n'est supprimée.

## Stratégie retenue

### A. Capture cible

`ManagedPersistentTestSession` ajoute une frontière de capture au-dessus de la TestSession historique. Avant chaque observation persistante et avant chaque échantillonnage de readiness :

1. `WindowWorkspaceManager.ensure_target_workspace()` démote le Relay du TOPMOST ;
2. la cible est activée/remontée ;
3. le foreground cible est vérifié par le workspace ;
4. seulement ensuite la zone cliente visible est lue.

La readiness rejoue cette préparation à chaque poll, afin qu'une zone du Relay ne puisse pas être prise à tort pour le rendu de la cible. Aucun déplacement/redimensionnement n'est utilisé.

### B. Réconciliation TestSession en Auto

La réconciliation est faite dans `ProfiledClipboardAgentApp` depuis l'état `PROCESSING_*`, avant le routage normal. Elle ne nécessite donc pas de nouveaux états dans `AutoStateMachine` et ne contourne pas la classification de sécurité des commandes.

Cas implémentés :

- `OPEN_TEST_SESSION` + ancienne session -> fermeture interne -> nouvelle ouverture ;
- `EXECUTION` / `TEMP_TEST` / `SHOW` + ancienne session -> fermeture interne -> directive originale ;
- `CLOSE_TEST_SESSION` + CLOSED -> succès idempotent ;
- `TEST_ACTIONS` + CLOSED/LOST -> résultat ERROR récupérable et Auto continue ;
- résultat `LOST` sans intervention utilisateur -> force-close local -> `SessionState: CLOSED` avant formatage ;
- session active + `TEST_ACTIONS` -> comportement existant.

Une fermeture de réconciliation n'est jamais envoyée au LLM comme résultat intermédiaire.

## Tests réalisés

- `tests/test_managed_test_session.py` vérifie que chaque sample visible de readiness est précédé de la préparation workspace Target et que l'observation prépare la cible avant capture.
- `tests/test_profiled_app.py` vérifie le nettoyage Auto, le remplacement par nouvel OPEN, la reprise EXECUTION, CLOSE idempotent, TEST_ACTIONS après LOST et la normalisation LOST→CLOSED.
- `tests/test_prompt_builder.py` vérifie que le prompt décrit désormais un cycle de vie géré par Relay et ne contient plus l'ancien invariant « fermer d'abord ou être refusé ».
- La suite de régression complète conserve les tests TestSession, TEMP_TEST, workspaces, protocole, sécurité, profils et timings existants.
- Matrice GitHub Actions validée avant cette clôture documentaire : Ubuntu/Windows × Python 3.11/3.12, compile + pytest + smoke launcher Windows, entièrement verte sur `eb8b55c`.

## Documentation alignée

- `CDC.md` passe en V2.15 et spécifie le cycle intent-based.
- `README.md` décrit le remplacement automatique, CLOSE idempotent et la capture foreground au point de lecture.
- `STATE_MACHINE.md` explique que la réconciliation est locale à `PROCESSING_*` et ne crée pas un faux round-trip LLM.
- Le prompt initial enseigne au LLM de demander l'action utile directement au lieu de réparer la plomberie de session.

## Gate d'intégration

Fusion uniquement si la matrice CI finale de la branche est verte et si aucun chemin de récupération TestSession prévu ci-dessus n'arrête Agent Auto pour un simple conflit de cycle de vie.

## Limite de validation physique

La CI non interactive ne peut pas reproduire un bureau Windows réel avec une fenêtre GPU/Chromium effectivement recouverte par le Relay. Le changement est donc validé structurellement (ordre de préparation workspace→capture) et devra être rejoué sur le PC utilisateur pour confirmer le Z-order réel. Si une application GPU reste visuellement noire malgré foreground correct, l'étape suivante est un backend de capture Windows Graphics Capture/Desktop Duplication, pas l'ajout de délais arbitraires.