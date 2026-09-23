# Machine d’état — Clipboard Agent Relay V2.15

Le code de référence est `clipboard_agent/state_machine.py`. Une transition Agent Auto non autorisée déclenche un arrêt fail-safe. La V2.15 conserve la machine d’état runtime typée et ajoute une réconciliation locale du cycle de vie TestSession dans la couche `ProfiledClipboardAgentApp` : les états techniques ne doivent plus imposer au LLM des tours de ménage.

## 1. Fenêtres et workspaces

Trois identités Windows sont utilisées, toujours sous forme de HWND précis :

- `RELAY_WINDOW` : fenêtre Clipboard Agent Relay ;
- `LLM_WINDOW` : fenêtre du chat Web sélectionnée au démarrage Auto ;
- `TARGET_WINDOW` : fenêtre appartenant au processus de TestSession/Target App.

Au démarrage Auto, Windows `EnumWindows` fournit le Z-order. Le Relay choisit la première fenêtre utilisateur exploitable située sous `RELAY_WINDOW`, en excluant son propre PID. Les points Prompt et Envoyer doivent tous deux appartenir à cette fenêtre ; sinon Auto refuse de démarrer.

Deux workspaces sont ensuite déterministes :

```text
LLM_WORKSPACE
  LLM_WINDOW : foreground
  RELAY_WINDOW : visible/topmost sans activation

TARGET_WORKSPACE
  TARGET_WINDOW : foreground + explicitement remontée après démotion topmost du Relay
  LLM_WINDOW + RELAY_WINDOW : derrière
```

Le Relay ne cherche plus « Chrome » ou « DeepSeek » par nom. Le HWND lié reste la référence pendant la session Auto. Avant toute saisie/clic navigateur ou Target, le foreground est vérifié/restauré.

En V2.15, la préparation `TARGET_WORKSPACE` est aussi réappliquée **au point exact de chaque lecture visuelle** : avant les polls de readiness et avant chaque observation/screenshot marker. Cela empêche une fenêtre Relay topmost de masquer la cible tout en fournissant malgré tout des pixels non noirs.

## 2. États Agent Auto

- `OFF`
- `STARTING`
- `SYNCING_EXISTING_REPLY`
- `WAITING_INITIAL_CLIPBOARD`
- `PROCESSING_INITIAL_REPLY`
- `RECOVERING_LAST_RESULT`
- `EXECUTING`
- `TARGET_STARTING` / `TARGET_RUNNING` / `TARGET_RESTORING` : `Action: TEMP_TEST` (ancien format temporaire conservé en compatibilité)
- `TEST_OPENING` : ouverture d’une TestSession persistante
- `TEST_ACTING` : actions sur la TestSession persistante
- `TEST_RESTORING` : retour du workspace Target vers le LLM
- `TEST_CLOSING` : fermeture explicite de la TestSession
- `SENDING`
- `WAITING_VISUAL`
- `WAITING_CLIPBOARD`
- `PROCESSING_REPLY`
- `PAUSED`

`PAUSED` ne reprend jamais automatiquement : il faut arrêter puis redémarrer Agent Auto.

La réconciliation V2.15 ne crée pas un nouvel état Auto : elle est exécutée synchroniquement depuis `PROCESSING_*` avant de router la directive utile, puis la machine entre normalement dans `EXECUTING`, `TARGET_STARTING`, `TEST_OPENING`, etc.

## 3. Démarrage Auto

```text
OFF
 ↓
STARTING
 ├─ validation setup / écran / template
 ├─ snapshot Z-order
 ├─ liaison RELAY_WINDOW + LLM_WINDOW
 ├─ validation Prompt/Envoyer ∈ LLM_WINDOW
 └─ installation hook souris
 ↓
SYNCING_EXISTING_REPLY
 ↓ stabilité de la réponse déjà affichée
WAITING_INITIAL_CLIPBOARD
 ↓ clic Copier
PROCESSING_INITIAL_REPLY
```

La première action Auto **n’envoie rien**. Elle récupère la dernière directive déjà visible du LLM.

Si cette directive a déjà été traitée et que son ID/type correspondent exactement au dernier résultat local :

```text
PROCESSING_INITIAL_REPLY
 ↓
RECOVERING_LAST_RESULT
 ↓ aucun replay de la commande/action
SENDING
```

Cette récupération couvre les actions canoniques `EXECUTION`, `TEMP_TEST`, `OPEN_TEST_SESSION`, `TEST_ACTIONS` et `CLOSE_TEST_SESSION` ; les anciens marqueurs sont traduits vers les mêmes types internes.

## 4. Cycle normal LLM

```text
SENDING
 ↓ texte (+ image éventuelle) vers LLM_WINDOW
WAITING_VISUAL
 ↓ mouvement puis stabilité
WAITING_CLIPBOARD
 ↓ Copier détecté visuellement
PROCESSING_REPLY
 ↓ directive suivante
```

Les délais d’UI configurables sont séquentiels : résultat→prompt, prompt→collage, collage→Envoyer, Envoyer→surveillance, stabilité→Copier. Les timeouts et durées de stabilité ne sont pas randomisés.

## 5. `Action: EXECUTION`

Sans session persistante :

```text
PROCESSING_*
 ↓
EXECUTING
 ↓ #RelayResult Kind: EXECUTION réel
SENDING
```

Avec une TestSession résiduelle en Agent Auto :

```text
PROCESSING_*
 ├─ force-close technique de l’ancienne TestSession
 ├─ vérification LLM_WORKSPACE
 ↓
EXECUTING
```

Cette fermeture technique n’est pas un tour envoyé au LLM. Une commande sensible passe à `PAUSED`; une commande bloquée provoque `OFF`.

## 6. TestSession persistante

Machine de durée de vie interne (`clipboard_agent/test_session.py`) :

```text
CLOSED
 ↓ Action: OPEN_TEST_SESSION
OPENING
 ↓ fenêtre + readiness
ACTIVE_FOREGROUND
 ↓ restauration LLM
ACTIVE_BACKGROUND
 ↕ Action: TEST_ACTIONS
ACTIVE_FOREGROUND
 ↓ restauration LLM
ACTIVE_BACKGROUND
 ↓ Action: CLOSE_TEST_SESSION
CLOSING
 ↓
CLOSED
```

`LOST` indique que le processus ou sa fenêtre a disparu de manière inattendue.

### Invariant V2.15 : intention plutôt que ménage

En Agent Auto, une TestSession existante n’interdit plus à elle seule la directive suivante. Le Relay réconcilie d’abord l’état :

- nouvel `OPEN_TEST_SESSION` : ferme la session précédente puis ouvre la nouvelle ;
- `EXECUTION`, `TEMP_TEST`, `SHOW` : ferme la session restante puis route l’action demandée ;
- `CLOSE_TEST_SESSION` sur `CLOSED` : résultat de succès idempotent ;
- `TEST_ACTIONS` sur `CLOSED`/`LOST` : résultat d’erreur structuré, récupérable, Auto reste actif ;
- résultat normal `LOST` : nettoyage local puis normalisation du résultat vers `CLOSED` avant envoi au LLM.

La réconciliation n’est appliquée que lorsque le worker TestSession n’est plus occupé. Une vraie concurrence locale, une restauration LLM impossible ou une intervention utilisateur restent fail-safe.

### 6.1 Ouvrir / remplacer

```text
PROCESSING_*
 ↓ Action: OPEN_TEST_SESSION
[cleanup ancienne session si nécessaire]
 ↓
TEST_OPENING
 ├─ launch avec stdout/stderr capturés
 ├─ détection fenêtre appartenant au PID ou descendants
 ├─ TARGET_WORKSPACE
 ├─ readiness
 ├─ actions initiales optionnelles
 └─ capture(s) optionnelle(s)
 ↓
TEST_RESTORING
 ↓ LLM_WORKSPACE vérifié
SENDING  (#RelayResult Kind: TEST_SESSION / Operation: OPENED)
```

Le processus reste ouvert après le résultat si `SessionActive: YES`.

Readiness :

- `Ready: window` : fenêtre exploitable trouvée ;
- `Ready: delay:<ms>` : fenêtre trouvée puis délai explicite ;
- `Ready: content` : plusieurs frames rendues non noires ;
- `Ready: auto` : accepte une surface non noire stable **ou** un rendu dynamique actif ;
- `Ready: checkpoint:<nom>` : attend `[[CAR_CHECKPOINT:<nom>]]` dans stdout puis exige seulement une surface rendue exploitable, sans imposer l’immobilité.

Un settle configurable après activation absorbe la latence focus/peinture avant la vérification. Pendant la boucle de readiness, `ensure_target_workspace()` est rejoué avant chaque échantillonnage visible.

### 6.2 Agir/observer

```text
PROCESSING_REPLY
 ↓ Action: TEST_ACTIONS
TEST_ACTING
 ↓ TARGET_WORKSPACE
[Click / TypeInput / Key / Wait / Observe] × N
 ↓
TEST_RESTORING
 ↓ LLM_WORKSPACE vérifié
SENDING (#RelayResult Kind: TEST_SESSION / Operation: ACTIONS)
```

`#Observe` dans le payload TEST_ACTIONS ne redonne pas la main au LLM au milieu d’une séquence. Pour raisonner sur une image : terminer la séquence par `#Observe`, attendre le retour, puis envoyer une nouvelle directive.

Avant chaque observation, y compris un screenshot marker devenu pending pendant que le LLM était au premier plan, le workspace Target est reconstruit immédiatement. La capture ne dépend donc pas d’un focus obtenu plusieurs secondes plus tôt.

### 6.3 Fermer

```text
PROCESSING_REPLY
 ↓ Action: CLOSE_TEST_SESSION
TEST_CLOSING
 ├─ fermeture fenêtre
 ├─ terminaison arbre si nécessaire
 ├─ stdout/stderr complets
 └─ restauration LLM_WORKSPACE
 ↓
SENDING (#RelayResult Kind: TEST_SESSION / Operation: CLOSED)
```

Si la session est déjà `CLOSED`, V2.15 suit le même chemin logique de résultat sans lancer de worker de fermeture ; le succès idempotent est directement renvoyé.

### 6.4 Réconciliation invisible au LLM

Une fermeture déclenchée uniquement pour permettre une autre directive n’est pas formatée comme un `#RelayResult` indépendant. Par exemple :

```text
LLM demande OPEN B alors que OPEN A est encore ACTIVE_BACKGROUND
 ↓
Relay ferme A localement
 ↓ aucun round-trip LLM
Relay lance B
 ↓
#RelayResult de B uniquement
```

Le LLM peut ainsi raisonner sur le produit à tester plutôt que sur la plomberie de session du Relay.

## 7. Checkpoints stdout

Le logiciel surveille stdout pendant toute la TestSession :

```text
[[CAR_CHECKPOINT:main-window-ready]]
[[CAR_SCREENSHOT:menu-open]]
```

Le premier peut servir à `Ready: checkpoint:main-window-ready`. Le second programme une capture au prochain point d’interaction/readiness où la Target App peut être observée en sécurité.

## 8. `Action: TEMP_TEST` et compatibilité historique

`Action: TEMP_TEST` est le format canonique du comportement temporaire launch→actions→close. L’ancien format V1 correspondant reste accepté par compatibilité :

```text
PROCESSING_*
 → [cleanup TestSession persistante éventuelle]
 → TARGET_STARTING
 → TARGET_RUNNING
 → TARGET_RESTORING
 → SENDING
```

Il lance, agit, observe, ferme puis restitue. Les anciens alias d’actions restent compris par le parser, mais le prompt V2 n’en émet plus.

## 9. Priorité utilisateur et fail-safe

Depuis tout état actif :

```text
mouvement souris physique
 → annuler timers/actions restantes
 → PAUSED
 → aucun clic navigateur tardif
```

Pendant une interaction Target/TestSession, le drapeau d’intervention reste persistant jusqu’à la fin du worker. L’application cible est laissée à l’utilisateur si celui-ci reprend la main.

Autres invariants :

- une action Target n’est jamais dirigée vers un HWND arbitraire fourni par le LLM ;
- `TARGET_WINDOW` doit appartenir au processus lancé ou à un descendant ;
- les clics Target sont relatifs à la zone cliente ;
- raccourcis Windows globaux interdits ;
- restauration LLM impossible → aucun clic/paste navigateur ;
- copie invalide/inchangée non justifiée → arrêt Auto ;
- nouvelle directive dupliquée en cycle normal → arrêt fail-safe ;
- reprise d’un dernier résultat uniquement au démarrage Auto avec ID + type exacts.

## Readiness adaptative et observations

La readiness d'une TestSession ne dépend plus uniquement d'une interface immobile :

- `Ready: auto` accepte une surface non noire devenue stable ou un rendu dynamique actif sur plusieurs frames ;
- `Ready: content` attend plusieurs frames non noires sans demander de stabilité ;
- `Ready: checkpoint:<nom>` attend d'abord le checkpoint logique, puis une surface rendue exploitable ;
- `Ready: window` ne prouve que l'existence du HWND et reste un mode volontairement faible.

Un `#Observe` quasi noir est retenté de manière bornée. Si aucune frame exploitable n'est obtenue, l'observation reste jointe pour diagnostic mais est annotée `OBSERVATION_WARNINGS`. L'agent ne doit alors ni inventer le contenu attendu ni compenser par des délais arbitraires.

## 10. Contrat de résultat V2

Le runtime interne reste basé sur `AutoState` et `TestSessionState`, mais les résultats envoyés au LLM rendent désormais cet état explicite :

```text
#RelayResult
Protocol: 2
Kind: TEST_SESSION
...
SessionState: ACTIVE_BACKGROUND
RecommendedNext: TEST_ACTIONS,CLOSE_TEST_SESSION
```

Cette information est descriptive de l’état réel après traitement. En V2.15, un état `LOST` ordinaire est auto-nettoyé avant envoi et devient `CLOSED`, afin de ne pas obliger l’agent à produire un CLOSE de maintenance. `LegacyMarker` n’a aucun rôle dans la machine d’état ; il sert seulement à la compatibilité avec les conversations V1.

## 11. Invariants V2.15 — réponse unique, clavier et capture

- Une réponse de contrôle V2 contient uniquement une directive `#Relay` brute ou un unique bloc fenced qui constitue tout le message. Un bloc `#Relay` entouré de prose est rejeté.
- `#TypeInput` transporte l'Unicode jusqu'à `SendInput(KEYEVENTF_UNICODE)`.
- Un `#Key` caractère simple non alphabétique utilise le layout du thread de `TARGET_WINDOW` via `GetKeyboardLayout` + `VkKeyScanExW` (notamment chiffres AZERTY et caractères accentués), avec fallback `VkKeyScanW` lorsqu'aucun HWND cible n'est disponible puis fallback Unicode si aucune combinaison physique n'existe.
- Avant actions et chaque lecture visuelle importante, le workspace Target démote le Relay de topmost puis remonte explicitement la Target, y compris si elle était déjà foreground.
- La géométrie des fenêtres n’est pas modifiée pour réparer le Z-order/focus.