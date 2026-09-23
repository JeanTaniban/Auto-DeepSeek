# Mission — Agent Auto vraiment autonome pour TestSession et capture cible

**Base :** `main` / `2ee388c3`  
**Branche :** `fix/auto-testsession-lifecycle-capture`

## Constat issu du test réel

Le test utilisateur met en évidence deux défauts de fonctionnement :

1. une fenêtre cible peut être techniquement détectée mais rester visuellement sous Clipboard Agent Relay au moment d'une observation, ce qui rend la capture non fiable ;
2. la plomberie de `TestSession` remonte trop directement au LLM : une session `LOST` ou une session précédente encore ouverte peut forcer le LLM à émettre un `CLOSE_TEST_SESSION`, et le Relay peut arrêter Agent Auto sur un simple conflit de cycle de vie.

Le mode Auto doit gérer ces détails lui-même. Le LLM doit exprimer le test qu'il souhaite effectuer, pas réparer l'état interne du Relay.

## Objectifs

- Garantir que la cible est réellement préparée au premier plan immédiatement avant chaque lecture visuelle liée à une TestSession.
- Renforcer les observations TEMP_TEST pour réactiver/remonter explicitement leur cible juste avant la capture.
- En Agent Auto, remplacer automatiquement une TestSession existante lorsqu'un nouveau `OPEN_TEST_SESSION` est demandé.
- En Agent Auto, nettoyer automatiquement une TestSession persistante avant une action incompatible (`EXECUTION`, `TEMP_TEST`, `SHOW`) au lieu d'arrêter Auto.
- Rendre `CLOSE_TEST_SESSION` idempotent lorsque la session est déjà fermée.
- Transformer les `TEST_ACTIONS` sur session absente/perdue en résultat structuré récupérable, sans arrêter Auto.
- Ne jamais envoyer au LLM les résultats techniques des fermetures internes utilisées uniquement pour réconcilier l'état.
- Clarifier le prompt : le cycle de vie TestSession est géré par Relay ; `OPEN_TEST_SESSION` signifie « je veux une session fraîche » et peut remplacer l'ancienne.
- Clarifier les résultats avec un indicateur de cycle de vie géré et des `RecommendedNext` orientés intention plutôt que ménage interne.

## Invariants de sécurité

- Une intervention physique de l'utilisateur conserve la priorité et peut toujours suspendre/arrêter l'automatisation.
- Une restauration impossible du workspace LLM reste une erreur bloquante : aucune poursuite automatique avec focus incertain.
- Une fermeture interne ne doit jamais relancer une commande sensible ou bloquée sans repasser par la classification existante.
- Aucune fenêtre ne doit être déplacée ou redimensionnée pour résoudre la capture ; seuls focus, TOPMOST et Z-order sont gérés.
- Le protocole Relay V2 reste compatible : aucune action existante n'est supprimée.

## Stratégie

### A. Capture cible

Avant chaque observation persistante :

1. démoter le Relay du TOPMOST via `WindowWorkspaceManager.ensure_target_workspace()` ;
2. activer/remonter la cible ;
3. vérifier qu'elle possède le foreground ;
4. seulement ensuite capturer la zone cliente.

La readiness visuelle applique la même préparation à chaque poll, afin qu'une zone du Relay ne puisse pas être prise à tort pour le rendu de la cible.

### B. Réconciliation TestSession en Auto

Le contrôleur Auto introduit une fermeture interne avec continuation. Si une directive sémantiquement valide nécessite une autre situation de session, le Relay ferme la session précédente puis reprend la directive originale sans round-trip LLM intermédiaire.

Cas attendus :

- `OPEN_TEST_SESSION` + ancienne session -> fermeture interne -> nouvelle ouverture ;
- `EXECUTION` / `TEMP_TEST` / `SHOW` + ancienne session -> fermeture interne -> directive originale ;
- `CLOSE_TEST_SESSION` + CLOSED -> succès idempotent ;
- `TEST_ACTIONS` + CLOSED/LOST -> résultat ERROR récupérable et Auto continue ;
- session active + `TEST_ACTIONS` -> comportement existant.

## Tests requis

- Capture : préparation foreground appelée avant observation et pendant la readiness.
- Capture TEMP_TEST : la cible est activée/remontée immédiatement avant OBSERVE.
- State machine : `TEST_CLOSING` peut enchaîner vers les états nécessaires à une continuation réconciliée.
- App Auto : nouvelle ouverture remplace l'ancienne sans `_stop_auto`.
- App Auto : EXECUTION/TEMP_TEST ferment d'abord une session restante puis continuent.
- App Auto : CLOSE sur CLOSED est idempotent.
- App Auto : TEST_ACTIONS sans session produit un résultat structuré sans arrêter Auto.
- Prompt : explique explicitement que Relay gère le cycle de vie et qu'un nouvel OPEN remplace l'ancien.
- Résultat : `SessionLifecycle: AUTO_MANAGED` et recommandations cohérentes.
- Régression : suite complète Ubuntu/Windows Python 3.11/3.12.

## Gate d'intégration

Fusion uniquement si la matrice CI est verte et si aucun chemin de récupération TestSession prévu ci-dessus n'arrête Agent Auto pour un simple conflit de cycle de vie.