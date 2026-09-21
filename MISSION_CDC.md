# CDC Mission — V2.12 Workspaces + TestSession persistante

## Objectif
Faire évoluer la V2.11 validée sans casser le relais LLM existant. La V2.12 doit rendre le comportement Windows déterministe autour de trois fenêtres identifiées (`RELAY_WINDOW`, `LLM_WINDOW`, `TARGET_WINDOW`) et permettre une **session de test persistante** de l'application en développement entre plusieurs réponses du LLM.

## Périmètre inclus
- Lier `LLM_WINDOW` au démarrage Agent Auto à partir du Z-order Windows : rechercher la première fenêtre utilisateur visible située sous `RELAY_WINDOW`, en excluant le PID du relais et les fenêtres techniques/non exploitables.
- Conserver le HWND sélectionné pendant toute la session Auto ; ne jamais redétecter le navigateur par nom de processus.
- Vérifier que les points Prompt/Envoyer configurés appartiennent à `LLM_WINDOW` avant de lancer Auto.
- Gérer deux workspaces déterministes :
  - `LLM_WORKSPACE` : `LLM_WINDOW` active, fenêtre Relay visible sans prendre le focus ;
  - `TARGET_WORKSPACE` : fenêtre cible active au premier plan, LLM/Relay derrière.
- Restaurer et vérifier `LLM_WORKSPACE` après chaque interaction Target App avant tout clic/paste dans le navigateur.
- Nouvelle directive `#OpenTestSession` : lancer l'application en développement une seule fois et conserver son processus/fenêtre ouverts.
- Nouvelle directive `#CloseTestSession` : fermer proprement la session persistante, récupérer stdout/stderr finaux, restaurer le workspace LLM et continuer Auto.
- Nouvelle directive `#TestActions` : exécuter une ou plusieurs actions sur la session persistante ouverte sans relancer l'application.
- Autoriser les actions unitaires `#Click`, `#TypeInput`/`#Typeinout`, `#Key`, `#Wait`, `#Observe` comme raccourcis de `#TestActions` quand une TestSession est active.
- Dans une TestSession active, accepter `#Multiple` **sans `Launch:`** comme alias de `#TestActions`; conserver le `#Multiple` V2.11 avec `Launch:` comme session temporaire rétrocompatible.
- `#OpenTestSession` accepte des actions initiales optionnelles afin de compacter les cas rapides (`lancer + observer` en une seule réponse LLM).
- Readiness configurable par directive : `Ready: auto`, `window`, `delay:<ms>`, `checkpoint:<nom>`.
- Readiness `auto` : fenêtre détectée, géométrie stable, rendu visuel client stable, puis délai de settle configurable.
- Checkpoints stdout : reconnaître `[[CAR_CHECKPOINT:nom]]` pour synchroniser le LLM avec un point précis du code.
- Screenshots stdout : reconnaître `[[CAR_SCREENSHOT:label]]`; la prochaine phase d'interaction capture la fenêtre avec ce label et l'inclut au retour.
- Capturer stdout/stderr de la TestSession pendant toute sa durée et renvoyer uniquement le delta utile à chaque résultat, puis le récapitulatif complet à la fermeture.
- Timings persistants dédiés : poll readiness, stabilité visuelle cible, settle après activation, délai entre actions, délai de restauration LLM, fermeture.
- Intervention souris physique : `PAUSED`, aucune action restante ni retour navigateur automatique.

## Hors périmètre
- OCR local ou compréhension d'image côté application.
- DOM/API du navigateur.
- Contrôle d'un HWND arbitraire fourni par le LLM.
- Raccourcis Windows globaux (`WIN`, `ALT+TAB`, etc.).
- Réorganisation agressive/redimensionnement du navigateur : le workspace conserve les géométries capturées et joue principalement sur le Z-order/focus pour ne pas invalider les coordonnées Prompt/Envoyer.
- Reprise automatique après `PAUSED`.

## Machine d'état
La machine Agent Auto reste typée. Ajouter :
- `TEST_OPENING`
- `TEST_ACTING`
- `TEST_RESTORING`
- `TEST_CLOSING`

Une machine orthogonale `TestSessionState` décrit la durée de vie persistante :
`CLOSED -> OPENING -> ACTIVE_BACKGROUND <-> ACTIVE_FOREGROUND -> CLOSING -> CLOSED`, avec `LOST` en cas de fin/crash inattendu du processus.

Flux nominal :

```text
PROCESSING_REPLY
  -> TEST_OPENING
  -> TEST_RESTORING
  -> SENDING
  -> WAITING_VISUAL

... LLM réfléchit ...

PROCESSING_REPLY
  -> TEST_ACTING
  -> TEST_RESTORING
  -> SENDING
  -> WAITING_VISUAL

... répéter ...

PROCESSING_REPLY
  -> TEST_CLOSING
  -> SENDING
  -> WAITING_VISUAL
```

## Sécurité / invariants
- `LLM_WINDOW`, `RELAY_WINDOW` et `TARGET_WINDOW` sont des HWND explicitement mémorisés.
- Avant une action navigateur : foreground attendu = `LLM_WINDOW`, sinon restauration vérifiée avant action.
- Avant une action Target : foreground attendu = `TARGET_WINDOW`, sinon activation vérifiée avant action.
- Les clics Target restent relatifs à la zone cliente de `TARGET_WINDOW`.
- La cible doit appartenir au processus lancé ou à un descendant.
- `#OpenTestSession`/legacy `#Multiple Launch:` passent par `classify_command`.
- Une TestSession déjà active refuse un second `#OpenTestSession`.
- Si la cible disparaît : session `LOST`, résultat explicite au LLM, aucune saisie clavier/souris hors cible.
- Si restauration LLM impossible : Agent Auto passe fail-safe en pause/arrêt sans clic navigateur.

## Tests prévus
- Sélection de `LLM_WINDOW` depuis un Z-order simulé avec Relay en tête et plusieurs navigateurs/fenêtres.
- Rejet de points Prompt/Envoyer n'appartenant pas au HWND lié.
- Workspace LLM/Target : ordre des activations, restauration, vérification foreground.
- Parser `#OpenTestSession`, `#CloseTestSession`, `#TestActions`, actions unitaires, `#Multiple` alias en session active.
- Readiness `auto/window/delay/checkpoint`, timeout et checkpoint absent.
- Capture stdout/stderr et parsing checkpoints/screenshots.
- Session persistante : open -> observe -> send -> action -> observe -> send -> close.
- Process cible perdu/crash.
- Intervention souris pendant ouverture/action/restauration.
- Non-régression de la suite V2.11 + nouveaux tests V2.12 (127 tests collectés).

## Critères de validation
- Tous les tests existants et nouveaux passent.
- `#Execution`, `#Show`, `#End` et `#Multiple` V2.11 restent compatibles.
- Le premier HWND LLM choisi est déterministe et loggé dans l'UI.
- Une TestSession reste réellement ouverte entre deux directives LLM.
- Le navigateur est restauré/vérifié avant chaque envoi automatique.
- Aucune action Target n'est envoyée hors de la fenêtre cible mémorisée.
- Aucune action navigateur n'est envoyée hors de la fenêtre LLM mémorisée.
- Documentation `CDC.md`, `README.md`, `STATE_MACHINE.md` mise à jour.
- Commit local propre puis push vers `JeanTaniban/Auto-DeepSeek`.


## Validation finale V2.12
- Parser : directives TestSession, actions unitaires, alias `#Multiple` validés.
- Workspaces : sélection Z-order, validation points LLM, Target/LLM restore testés.
- TestSession : open → observe → actions → observe → close avec même processus simulé.
- Checkpoints/screenshot markers stdout testés.
- Readiness checkpoint + stabilité visuelle testée.
- Suite : 127 tests collectés ; 125 passent sans display (2 tests Tk skip), 127/127 passent sous Xvfb.
- `compileall` : OK.
- Scripts Bash : syntaxe OK.
- Démarrage Tk sous Xvfb : OK.
- Limite : Win32 physique (SendInput/hook/GDI/Z-order interactif) à valider sur le PC Windows utilisateur.

## Correction CI Windows — livraison V2.12
- Le premier passage GitHub Actions a révélé que `tests/test_execution.py` imposait `shell="bash"` sur Windows ; le runner résolvait alors `bash.exe` vers le lanceur WSL sans distribution installée.
- Le test d’intégration d’`ExecutionManager` utilise désormais le shell runtime de la plateforme : `powershell` sous Windows, `bash` sur Unix.
- Le comportement runtime reste inchangé : l’application choisit déjà `powershell` sous Windows et `bash` ailleurs.
- Critère de livraison ajouté : matrice GitHub Actions Ubuntu/Windows × Python 3.11/3.12 entièrement verte.
- Le CI Windows a également exposé que PowerShell ne retransmettait pas automatiquement le code de sortie d’un exécutable natif ; l’invocation non interactive propage désormais `$LASTEXITCODE` afin que `#ExecutionResult` conserve le code exact.
