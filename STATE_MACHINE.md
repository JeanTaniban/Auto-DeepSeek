# Machine d’état — Clipboard Agent Relay V2.16

Le code de référence est `clipboard_agent/state_machine.py`. Une transition Agent Auto non autorisée déclenche un arrêt fail-safe. La V2.16 conserve la réconciliation locale TestSession de la V2.15 et ajoute une voie explicite `RECOVERING_SYSTEM_ERROR` pour les incidents système récupérables lorsque l'option `Auto repair self` est activée.

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

En V2.15+, la préparation `TARGET_WORKSPACE` est aussi réappliquée **au point exact de chaque lecture visuelle** : avant les polls de readiness et avant chaque observation/screenshot marker. Cela empêche une fenêtre Relay topmost de masquer la cible tout en fournissant malgré tout des pixels non noirs.

## 2. États Agent Auto

- `OFF`
- `STARTING`
- `SYNCING_EXISTING_REPLY`
- `WAITING_INITIAL_CLIPBOARD`
- `PROCESSING_INITIAL_REPLY`
- `RECOVERING_LAST_RESULT`
- `RECOVERING_SYSTEM_ERROR`
- `EXECUTING`
- `PROFILE_TOOL_RUNNING`
- `TARGET_STARTING` / `TARGET_RUNNING` / `TARGET_RESTORING`
- `TEST_OPENING`
- `TEST_ACTING`
- `TEST_RESTORING`
- `TEST_CLOSING`
- `SENDING`
- `WAITING_VISUAL`
- `WAITING_CLIPBOARD`
- `PROCESSING_REPLY`
- `PAUSED`

`PAUSED` ne reprend jamais automatiquement : il faut arrêter puis redémarrer Agent Auto.

`RECOVERING_SYSTEM_ERROR` n'est pas un état de travail métier. Il sert uniquement à transformer un défaut système récupérable en résultat structuré adressé au LLM. Il ne peut ensuite aller que vers `SENDING`, `PAUSED` ou `OFF`.

La réconciliation TestSession ne crée pas un nouvel état Auto : elle est exécutée synchroniquement depuis `PROCESSING_*` avant de router la directive utile.

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

Les délais d’UI configurables sont séquentiels. Les timeouts et durées de stabilité ne sont pas randomisés.

### 4.1 Auto repair self

Lorsque `Settings.auto_repair_self == true`, tout état Auto actif admissible peut signaler un incident récupérable :

```text
<état Auto actif>
      |
      | erreur système récupérable
      v
RECOVERING_SYSTEM_ERROR
      |
      | #RelayResult Kind: SYSTEM_ERROR
      v
SENDING
      v
WAITING_VISUAL
      v
WAITING_CLIPBOARD
      v
PROCESSING_REPLY
```

Le message contient au minimum `Severity: RECOVERABLE`, `Source: RELAY`, l'état d'origine, un code stable et une description. Le LLM reprend ensuite avec une directive `#Relay` normale.

Cette voie est interdite lorsque :

- une opération locale est encore réellement en cours ;
- l'erreur est une violation de machine d'état ;
- une politique de sécurité `BLOCKED`/`SENSITIVE` est en jeu ;
- le workspace/canal LLM ne peut plus être restauré ;
- Win32 nécessaire au canal d'envoi est indisponible ;
- l'utilisateur a repris physiquement la souris ;
- un premier `SYSTEM_ERROR` est encore en cours de transmission.

Anti-boucle : au plus 3 récupérations dans une fenêtre glissante de 120 s. Au-delà, `OFF` fail-safe.

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

Cette fermeture technique n’est pas un tour envoyé au LLM. Une commande sensible passe à `PAUSED`; une commande bloquée provoque `OFF` et n'est jamais auto-réparée.

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

### Invariant : intention plutôt que ménage

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
SENDING
```

Readiness : `window`, `delay:<ms>`, `content`, `auto`, `checkpoint:<nom>`.

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
SENDING
```

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
SENDING
```

Si la session est déjà `CLOSED`, le succès idempotent est directement renvoyé.

### 6.4 Réconciliation invisible au LLM

Une fermeture déclenchée uniquement pour permettre une autre directive n’est pas formatée comme un `#RelayResult` indépendant.

## 7. Checkpoints stdout

```text
[[CAR_CHECKPOINT:main-window-ready]]
[[CAR_SCREENSHOT:menu-open]]
```

Le premier peut servir à `Ready: checkpoint:main-window-ready`. Le second programme une capture au prochain point sûr.

## 8. `Action: TEMP_TEST` et compatibilité historique

```text
PROCESSING_*
 → [cleanup TestSession persistante éventuelle]
 → TARGET_STARTING
 → TARGET_RUNNING
 → TARGET_RESTORING
 → SENDING
```

Il lance, agit, observe, ferme puis restitue.

## 9. Priorité utilisateur et fail-safe

Depuis tout état actif, y compris `RECOVERING_SYSTEM_ERROR` :

```text
mouvement souris physique
 → annuler timers/actions restantes
 → PAUSED
 → aucun clic navigateur tardif
```

Autres invariants :

- une action Target n’est jamais dirigée vers un HWND arbitraire fourni par le LLM ;
- `TARGET_WINDOW` doit appartenir au processus lancé ou à un descendant ;
- les clics Target sont relatifs à la zone cliente ;
- raccourcis Windows globaux interdits ;
- restauration LLM impossible → aucun clic/paste navigateur et aucun Auto repair self ;
- copie invalide/inchangée : `SYSTEM_ERROR` si Auto repair self peut encore joindre le LLM, sinon arrêt ;
- nouvelle directive dupliquée en cycle normal : récupérable uniquement si la politique la classe sans conflit local ;
- reprise d’un dernier résultat uniquement au démarrage Auto avec ID + type exacts ;
- échec pendant la transmission d'un `SYSTEM_ERROR` → `OFF`.

## 10. Contrats de résultat V2

Résultat TestSession :

```text
#RelayResult
Protocol: 2
Kind: TEST_SESSION
...
SessionState: ACTIVE_BACKGROUND
RecommendedNext: TEST_ACTIONS,CLOSE_TEST_SESSION
```

Résultat système V2.16 :

```text
#RelayResult
Protocol: 2
Kind: SYSTEM_ERROR
ID: system-...
Status: ERROR
Severity: RECOVERABLE
Source: RELAY
AutoRepairSelf: ACTIVE
AutoState: WAITING_VISUAL
Code: VISUAL_TIMEOUT
RecoveryAttempt: 1/3
Description: ...
Instruction: ...
```

`SYSTEM_ERROR` ne modifie pas le protocole de directives : le tour suivant du LLM reste exactement une directive `#Relay` canonique.

## 11. Invariants V2.16 — réponse unique, clavier, capture et réparation

- Une réponse de contrôle V2 contient uniquement une directive `#Relay` brute ou un unique bloc fenced qui constitue tout le message.
- `#TypeInput` transporte l'Unicode jusqu'à `SendInput(KEYEVENTF_UNICODE)`.
- Un `#Key` caractère simple non alphabétique utilise le layout du thread de `TARGET_WINDOW` via `GetKeyboardLayout` + `VkKeyScanExW`, avec fallbacks existants.
- Avant actions et chaque lecture visuelle importante, le workspace Target démote le Relay de topmost puis remonte explicitement la Target.
- La géométrie des fenêtres n’est pas modifiée pour réparer le Z-order/focus.
- `Auto repair self` n'efface jamais la cause d'une erreur : elle est journalisée localement et renvoyée explicitement au LLM.
- `Auto repair self` ne reprend jamais un état `PAUSED` et ne contourne jamais une politique de sécurité.
