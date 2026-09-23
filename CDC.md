# Cahier des charges — Auto-DeepSeek / Clipboard Agent Relay

**Version : 2.15**
**Cible principale : Windows 10/11, Python 3.11+**

## 1. Objectif

Fournir un agent de développement local semi-autonome autour d’un LLM utilisé dans un navigateur. L’utilisateur effectue le transfert initial vers le chat ; le logiciel peut ensuite automatiser localement le cycle directive→exécution→résultat tout en gardant une priorité humaine immédiate.

Le navigateur n’est pas piloté par DOM/API privée. Les interfaces locales reposent sur presse-papiers, capture écran et Win32.

### 1.1 Documents de cadrage spécialisés

- `CDC_PROFILES_UNITY.md` — architecture générique des profils et spécification du premier profil métier Unity.

### 1.2 Socle profils V2.15 / P0

Le runtime possède désormais une couche de profils générique. La V2.15 P0 n’active qu’un seul profil exécutable : `generic` / **Développement général**. Il encapsule le comportement V2.14 sans modifier son protocole, sa sécurité, ses timings ni ses TestSessions.

Le profil actif est persistant. Un profil mémorisé mais indisponible retombe sur `generic`. Le changement de profil est interdit pendant Agent Auto, une commande, une Target App ou une TestSession non fermée. Les futurs profils spécialisés doivent s’enregistrer via `ProfileRegistry`/`ProfileManager` plutôt que d’ajouter leur logique directement au cœur.

`Action: TOOL` et les providers métier restent hors périmètre P0 ; ils appartiennent à la phase P1 du CDC spécialisé.

## 2. Destination Goal

Chaque projet possède un **Topic / Destination Goal** persistant dans la session. Il est intégré au prompt initial et peut être rappelé périodiquement dans les résultats. L’utilisateur peut recadrer le LLM directement dans le chat.

## 3. Modes

### Manuel assisté

L’utilisateur copie une directive du LLM. Le Relay la parse, exécute localement et copie le résultat prêt à coller.

### Agent Auto

Sous Windows, le Relay :

1. lie une fenêtre LLM précise ;
2. observe la réponse déjà affichée ;
3. détecte sa stabilité ;
4. localise visuellement le bouton Copier ;
5. traite la directive ;
6. exécute/observe localement ;
7. restaure le workspace LLM ;
8. colle et envoie le résultat ;
9. répète.

## 4. Fenêtres déterministes

Identités :

- `RELAY_WINDOW` : HWND du Relay ;
- `LLM_WINDOW` : premier HWND utilisateur valide sous Relay dans le Z-order au démarrage Auto ;
- `TARGET_WINDOW` : HWND appartenant au processus de test ou à un descendant.

Le point Prompt et le point Envoyer doivent appartenir à `LLM_WINDOW`. Si ce n’est pas le cas, démarrage Auto refusé.

### LLM workspace

- `LLM_WINDOW` au foreground ;
- Relay visible/topmost sans activation ;
- géométries originales conservées.

### Target workspace

- `TARGET_WINDOW` au foreground ;
- Relay perd le topmost ;
- après cette démotion, `TARGET_WINDOW` est explicitement remontée dans le Z-order même si elle possédait déjà le foreground ;
- aucune readiness, capture ou interaction Target si le foreground vérifié n’est pas la cible.

Après interaction, le LLM workspace est restauré et vérifié avant tout clic/paste navigateur.

## 5. Protocole LLM

### 5.1 Format canonique V2

Le chemin nominal expose un seul marqueur top-level :

```text
#Relay
Protocol: 2
Action: <ACTION>
ID: <id-unique>
...
```

Une seule directive de contrôle est autorisée par réponse. Le champ `Action` détermine explicitement la sémantique :

- `EXECUTION` : commande shell atomique ;
- `OPEN_TEST_SESSION` : ouverture d’une application persistante ;
- `TEST_ACTIONS` : actions sur la TestSession active ;
- `CLOSE_TEST_SESSION` : fermeture de la TestSession ;
- `TEMP_TEST` : session temporaire launch→actions→close ;
- `SHOW` : lancement visible avec reprise utilisateur ;
- `END` : fin de mission.

Le parser V2 doit :
- exiger que la réponse de contrôle soit uniquement la directive brute ou exactement un seul bloc fenced `#Relay`, sans prose avant/après ;
- exiger `Protocol: 2`, `Action` et un `ID` valide ;
- rejeter une action inconnue ;
- rejeter les métadonnées incompatibles avec l’action ;
- ne reconnaître le protocole canonique que si `#Relay` ouvre effectivement la réponse brute ou le premier contenu du bloc copiable ; une simple citation de `#Relay` dans une phrase n’est pas une directive ;
- traduire vers les modèles internes existants.

Le parser historique V1 reste disponible en dessous pour compatibilité avec les conversations déjà démarrées. Il ne doit plus être enseigné dans le prompt normal.

### 5.2 Résultats canoniques

Les résultats commencent par un en-tête commun :

```text
#RelayResult
Protocol: 2
Kind: ...
LegacyMarker: ...
ID: ...
Status: ...
RecommendedNext: ...
```

`LegacyMarker` facilite la migration mais n’est pas une directive à réutiliser.

Pour les TestSessions, le résultat expose aussi :

- `SessionState` ;
- `SessionActive` ;
- `LLMWorkspaceRestored` ;
- `RecommendedNext`.

Le but est que le LLM n’ait pas à reconstruire implicitement la machine d’état à partir de texte libre.

Tant que `TestSessionState` n'est pas `CLOSED`, Agent Auto doit refuser les actions incompatibles avec la session persistante (`EXECUTION`, `TEMP_TEST`, `SHOW`, nouvel `OPEN_TEST_SESSION`). Une session active accepte `TEST_ACTIONS`/`CLOSE_TEST_SESSION`; `LOST` exige `CLOSE_TEST_SESSION` avant de poursuivre.

## 6. Readiness TestSession

Modes :

- `auto` : surface rendue non noire, puis interface stable **ou** rendu dynamique actif ;
- `content` : plusieurs frames non noires, sans exigence de stabilité ;
- `checkpoint:<nom>` : attend `[[CAR_CHECKPOINT:nom]]`, puis une surface rendue exploitable ;
- `window` : existence du HWND uniquement ;
- `delay:<ms>` : délai explicite, réservé aux cas où aucun signal plus robuste n’existe.

`#Wait` dans les actions reste possible pour des délais fonctionnels testés, mais ne doit pas servir à deviner la readiness.

## 7. Captures et observations

`#Observe` capture la zone cliente originale de la Target.

Comportement :

1. capture normale GDI ;
2. si quasi noire, retry borné ;
3. fallback Win32 `PrintWindow` ;
4. conservation de la frame la plus informative ;
5. si toujours quasi noire, résultat avec `OBSERVATION_WARNINGS`.

Une capture noire signalée ne doit pas être présentée au LLM comme preuve visuelle fiable.

## 8. Timings

Tous les timings Auto/Target/TestSession sont persistants et volontairement conservateurs. Ils doivent rester configurables et ne pas être resserrés sans validation physique.

Une variation bornée des délais UI peut être configurée pour absorber des latences locales ; les timeouts et seuils de stabilité restent déterministes.

## 9. Sécurité

- LOW → exécution Auto possible ;
- MODIFY → exécution Auto possible ;
- SENSITIVE → validation humaine ;
- BLOCKED → refus ;
- Target limitée au PID lancé/descendants ;
- raccourcis globaux Windows interdits ;
- `#TypeInput` conserve l'Unicode complet ; un `#Key` caractère simple utilise le layout clavier du thread de `TARGET_WINDOW` via `VkKeyScanExW` (avec fallback Unicode lorsque nécessaire) ;
- aucun HWND arbitraire fourni par le LLM ;
- impossible de reprendre `PAUSED` automatiquement ;
- reprise d’une directive déjà traitée uniquement au démarrage Auto avec ID/type exacts ;
- un profil métier ne peut pas contourner ces protections.

## 10. Tests / CI

Validation minimale à chaque intégration :

- `python -m compileall -q clipboard_agent main.py` ;
- `python -m pytest -q` ;
- GitHub Actions Ubuntu/Windows × Python 3.11/3.12 ;
- smoke-test launcher Windows.

Les extensions de profil doivent ajouter leurs propres tests sans affaiblir la suite générique.
