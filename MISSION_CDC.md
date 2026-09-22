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


## Mission corrective — readiness/capture TestSession avancée

### Problèmes observés
- Un agent peut encore utiliser `#Wait` avec une durée arbitraire avant `#Observe`, ce qui rend les tests dépendants de timings devinés.
- `Ready: window` prouve seulement l'existence d'un HWND, pas que le contenu applicatif est réellement rendu.
- `Ready: auto` et `checkpoint:<nom>` reposent actuellement sur la stabilité visuelle ; une application animée en continu (jeu, rendu temps réel) peut ne jamais devenir stable.
- Une capture GDI de la zone cliente peut être noire pendant le démarrage ou avec certains moteurs accélérés, sans diagnostic explicite.
- Le panneau UI affiche encore `MULTIPLE` pour `#OpenTestSession`, ce qui brouille le diagnostic.

### Objectif concret
Rendre les tests visuels persistants pilotés par l'état réel de la cible plutôt que par des délais arbitraires, et rendre les captures plus robustes/diagnostiquées, notamment pour les applications temps réel.

### Périmètre inclus
- Faire de `Ready: auto` un mode adaptatif : prêt si l'interface devient stable OU si plusieurs frames non noires indiquent un rendu dynamique actif.
- Pour `Ready: checkpoint:<nom>`, attendre le checkpoint logique puis une surface rendue exploitable, sans exiger une stabilité impossible pour un jeu.
- Ajouter `Ready: content` comme mode explicite pour attendre un contenu visuel non noir.
- Lors d'un `#Observe`, réessayer brièvement une capture noire et conserver la frame la plus informative.
- Ajouter un fallback Win32 `PrintWindow` lorsque la capture visible de la zone cliente est quasi noire.
- Remonter un avertissement explicite au LLM si l'observation reste quasi noire après les retries.
- Guider le prompt agent : ne pas utiliser `#Wait` pour deviner une readiness ; préférer `Ready: auto`, `Ready: content` ou un checkpoint instrumenté.
- Corriger le libellé UI de `#OpenTestSession`.

### Hors périmètre
- Implémentation Windows Graphics Capture/Desktop Duplication complète.
- OCR ou interprétation locale du contenu.
- Suppression de `#Wait` : il reste utile pour des délais fonctionnels volontairement imposés.
- Modification des règles de sécurité HWND/processus.

### Briques et tests
1. **Qualité frame Win32** : détection pure d'une frame quasi noire + fallback `PrintWindow`; tests unitaires des heuristiques et chemin de fallback.
2. **Readiness adaptative** : tests d'une UI stable, d'un flux animé non noir, d'un flux noir puis rendu, et d'un checkpoint sur rendu dynamique.
3. **Observe robuste** : tests de retry noir→contenu et avertissement si toutes les captures restent noires.
4. **Prompt/UI/docs** : tests du prompt et non-régression parser/protocole.

### Critères de validation
- Aucun `#Wait` arbitraire n'est nécessaire pour le scénario nominal observer→agir→observer.
- Une application animée en continu peut passer `Ready: auto`.
- Une capture initialement noire est retentée avant d'être envoyée.
- Une capture toujours noire est signalée explicitement, sans être présentée comme observation fiable.
- Suite pytest + compileall + matrice GitHub Actions Ubuntu/Windows Python 3.11/3.12 vertes.
- Validation physique finale à rejouer sur le PC Windows utilisateur avec le jeu de test.


### Validation de la mission corrective
- Détection/capture noire : heuristique BGRA + fallback Win32 `PrintWindow` testés.
- Observation : retry borné noir→contenu et avertissement persistant-noir testés.
- Readiness : `auto` valide désormais les UI stables et les rendus dynamiques ; `content` ajouté ; checkpoint ne requiert plus l'immobilité d'un rendu temps réel.
- Prompt agent : `#Wait` n'est plus recommandé pour deviner startup/rendu ; les mécanismes état/contenu/checkpoint sont explicitement prioritaires.
- UI : `#OpenTestSession` n'est plus affiché comme `MULTIPLE`.
- Documentation : README, CDC global et machine d'état alignés.
- CI d'intégration avant clôture : Ubuntu/Windows × Python 3.11/3.12, étapes compile + pytest toutes vertes.
- Limite restante : la capture/focus réels d'un jeu accéléré doivent être rejoués sur le bureau Windows interactif utilisateur ; la CI ne peut pas reproduire cette couche graphique physique.


## Mission — Protocole V2 lisible et déterministe

### Constat côté agent
- Le prompt expose trop de variantes de contrôle : marqueurs distincts, alias, raccourcis unitaires et `#Multiple` surchargé.
- Plusieurs chemins font la même chose, donc l'agent doit mémoriser des exceptions au lieu de choisir une intention simple.
- Les résultats donnent l'état technique, mais pas toujours la prochaine action recommandée.
- Les marqueurs de contrôle ressemblent à du texte normal et peuvent être cités accidentellement.

### Constat côté Relay
- Le parser doit reconnaître plusieurs grammaires et branches selon le marqueur.
- `#Multiple` change de sémantique selon la présence de `Launch:`.
- Les raccourcis d'action sans enveloppe explicite réduisent la traçabilité et augmentent les cas de parsing.
- Les formats de résultat ne partagent pas de préambule commun permettant au LLM de reconnaître rapidement le type de retour.

### Objectif
Introduire un protocole canonique V2 à enveloppe unique `#Relay`, tout en conservant la compatibilité de lecture des anciens formats. Le prompt initial doit enseigner uniquement ce chemin canonique.

### Format canonique
Une directive V2 commence par :
- `#Relay`
- `Protocol: 2`
- `Action: <type>`
- `ID: <id>`

Actions canoniques :
- `EXECUTION`
- `OPEN_TEST_SESSION`
- `TEST_ACTIONS`
- `CLOSE_TEST_SESSION`
- `TEMP_TEST`
- `SHOW`
- `END`

Les métadonnées restantes dépendent de l'action. Le parser traduit ensuite vers les `DirectiveKind` existants afin de limiter les régressions internes.

### Principes de lisibilité
- Une seule enveloppe de commande à apprendre.
- Un tableau de décision compact dans le prompt avant les détails.
- Aucun alias ni raccourci implicite enseigné à l'agent.
- Les formats historiques restent acceptés mais sont documentés uniquement comme compatibilité.
- Chaque résultat canonique contient un préambule commun `#RelayResult / Protocol: 2 / Kind / ID / Status`.
- Les résultats de TestSession indiquent explicitement `RecommendedNext`.

### Fiabilité
- Rejeter les actions V2 inconnues avec une erreur explicite.
- Rejeter les métadonnées V2 incompatibles avec l'action choisie.
- Conserver les protections existantes : ID anti-doublon, CWD, classification sécurité, HWND/processus, transitions d'état.
- Ne pas supprimer les parsers V1 dans cette mission.

### Tests prévus
1. Parser V2 pour chaque action canonique.
2. Rejet action inconnue, protocol incorrect, métadonnée invalide et directives multiples.
3. Équivalence V2 → mêmes `DirectiveKind` / modèles internes que V1.
4. Prompt : tableau de décision, enveloppe unique, absence d'enseignement des alias historiques.
5. Résultats : préambule V2 commun et `RecommendedNext` TestSession.
6. Non-régression complète de la suite V1.

### Critères de validation
- Le chemin nominal documenté ne nécessite de connaître qu'un marqueur top-level : `#Relay`.
- `#Multiple`, `#Typeinout` et les actions unitaires n'apparaissent plus comme options normales dans le prompt.
- Tous les anciens tests de compatibilité passent.
- Compile + pytest verts sur Ubuntu/Windows Python 3.11/3.12.
- Fusion sur `main` uniquement après CI verte.
