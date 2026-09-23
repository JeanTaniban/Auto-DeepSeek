# Cahier des charges — Auto-DeepSeek / Clipboard Agent Relay

**Version : 2.15**
**Cible principale : Windows 10/11, Python 3.11+**

## 1. Objectif

Fournir un agent de développement local semi-autonome autour d’un LLM utilisé dans un navigateur. L’utilisateur effectue le transfert initial vers le chat ; le logiciel peut ensuite automatiser localement le cycle directive→exécution→résultat tout en gardant une priorité humaine immédiate.

Le navigateur n’est pas piloté par DOM/API privée. Les interfaces locales reposent sur presse-papiers, capture écran et Win32.

### 1.1 Documents de cadrage spécialisés

- `CDC_PROFILES_UNITY.md` — architecture générique des profils et spécification du premier profil métier Unity.
- `MISSION_AUTO_TESTSESSION.md` — mission V2.15 de cycle de vie TestSession autonome et capture cible fiable.

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

Le mode Auto prend aussi en charge le ménage de cycle de vie des TestSessions. Le LLM exprime l’intention utile ; une session persistante précédente ne doit pas imposer un tour de protocole artificiel uniquement pour être fermée.

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

La préparation Target est rejouée au point de lecture visuelle, y compris pour chaque poll de readiness et pour les screenshot markers différés. Une capture ne doit donc jamais supposer qu’un focus obtenu plus tôt est encore suffisant.

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
- `CLOSE_TEST_SESSION` : fermeture volontaire de la TestSession ;
- `TEMP_TEST` : test UI temporaire launch→actions→close ;
- `SHOW` : démonstration utilisateur et arrêt Auto ;
- `END` : fin de mission.

Le parser V2 doit :
- exiger que la réponse de contrôle soit uniquement la directive brute ou exactement un seul bloc fenced `#Relay`, sans prose avant/après ;
- exiger `Protocol: 2`, `Action` et un `ID` valide ;
- rejeter les actions inconnues ;
- rejeter les métadonnées incompatibles avec l’action ;
- reconnaître `#Relay` seulement comme première ligne utile du payload copié ou d’un bloc de code, afin qu’une citation dans de la prose ne déclenche pas le protocole ;
- traduire la directive V2 vers les modèles internes `DirectiveKind` existants.

### 5.2 Compatibilité V1

Les anciens marqueurs restent acceptés afin de ne pas casser les conversations déjà initialisées avec un ancien prompt. Ils ne sont plus enseignés dans le prompt canonique et ne doivent plus être utilisés pour les nouvelles missions.

Les alias historiques et raccourcis d’actions restent donc une **couche de compatibilité parser**, pas une surface utilisateur normale.

### 5.3 Résultats V2 et cycle de vie TestSession

Les résultats canoniques commencent par un préambule commun :

```text
#RelayResult
Protocol: 2
Kind: ...
LegacyMarker: ...
ID: ...
Status: ...
```

`LegacyMarker` maintient la reconnaissance par les anciens prompts.

Pour les TestSessions, le résultat expose aussi :
- `SessionState` : état réel de la session après réconciliation locale ;
- `RecommendedNext` : actions techniquement pertinentes depuis cet état ;
- `SessionActive` ;
- `LLMWorkspaceRestored`.

Le but est que le LLM n’ait pas à reconstruire implicitement la machine d’état à partir de texte libre ni à gérer le ménage interne du Relay.

En **Agent Auto**, le cycle de vie est intent-based :

- un nouvel `OPEN_TEST_SESSION` ferme/remplace automatiquement une TestSession précédente ;
- `EXECUTION`, `TEMP_TEST` ou `SHOW` nettoient d’abord une TestSession restante au lieu d’arrêter Auto pour ce seul conflit ;
- `CLOSE_TEST_SESSION` sur une session déjà fermée est idempotent et renvoie un succès ;
- `TEST_ACTIONS` sans session active produit un résultat structuré récupérable au lieu d’arrêter Auto ;
- un résultat `LOST` sans intervention utilisateur est nettoyé localement et normalisé vers `CLOSED` avant d’être renvoyé au LLM ;
- une fermeture technique de réconciliation n’est pas exposée comme tour LLM intermédiaire.

Une opération concurrente réellement encore en cours, une restauration LLM impossible ou une intervention physique de l’utilisateur restent des conditions fail-safe et peuvent interrompre Auto.

## 6. Readiness TestSession

Modes :

- `auto` : fenêtre détectée + contenu rendu ; valide ensuite une UI stable **ou** un rendu dynamique actif ;
- `content` : plusieurs frames non noires, sans exigence de stabilité ;
- `window` : fenêtre détectée uniquement ;
- `delay:<ms>` : délai explicite, non recommandé comme mécanisme de readiness ;
- `checkpoint:<nom>` : marqueur stdout puis présence d'une surface rendue exploitable.

Un délai de settle après activation est configurable avant évaluation.

Chaque lecture visible de readiness doit immédiatement rappeler `ensure_target_workspace()` afin que le Relay soit démoté du TOPMOST et que la Target soit remontée avant l’échantillonnage. Une zone non noire appartenant au Relay ne doit jamais valider la readiness de la Target.

Marqueurs stdout :

```text
[[CAR_CHECKPOINT:nom]]
[[CAR_SCREENSHOT:label]]
```

Le premier synchronise une readiness. Le second programme une observation à la prochaine phase où la Target App est activée/observable.

## 7. Captures visuelles Target

`#Observe` capture uniquement la zone cliente de `TARGET_WINDOW`. Immédiatement avant la capture, le workspace Target est réconcilié : Relay non-topmost, Target activée/remontée, géométrie conservée. Cette préparation s’applique aussi aux captures demandées par `[[CAR_SCREENSHOT:...]]` qui peuvent arriver après restauration du workspace LLM.

Une capture quasi noire déclenche un fallback Win32 puis des retries bornés ; la frame la plus informative est conservée. Si elle reste quasi noire, le résultat la marque explicitement dans `OBSERVATION_WARNINGS` afin qu'elle ne soit pas interprétée comme preuve visuelle fiable. Plusieurs captures peuvent être composées en une planche envoyée au LLM. Le redimensionnement éventuel de la planche ne change jamais le repère des futurs `#Click`, qui reste la taille cliente originale indiquée dans le résultat.

## 8. Détection réponse LLM

La zone Réponse agent configurée sert uniquement au mouvement/stabilité.

Le bouton Copier est détecté séparément sur l’écran virtuel entier :

- image de référence persistante ;
- template non redimensionné ;
- matching 1:1 ;
- seuil configurable ;
- clic uniquement si le déplacement réel du curseur vers le point calculé est confirmé.

Un bouton diagnostic déplace le curseur sans cliquer et sauvegarde écran/template/match.

## 9. Timings

Persistants et configurables :

- résultat→début renvoi ;
- clipboard→prompt ;
- prompt→paste ;
- paste→Envoyer ;
- Envoyer→surveillance ;
- stabilité→Copier ;
- timeout/stabilité réponse ;
- détection fenêtre Target ;
- délai entre actions ;
- fermeture/restauration ;
- readiness stable/poll ;
- settle activation TestSession.

Une variation bornée des délais UI peut être configurée pour absorber des latences d’interface. Elle ne modifie ni timeouts, ni durées de stabilité, ni règles de sécurité.

## 10. Sécurité

- mouvement souris physique → pause immédiate ;
- événement souris injecté par le logiciel ignoré par le détecteur ;
- commande shell classée LOW/MODIFY/SENSITIVE/BLOCKED ;
- SENSITIVE → validation/pause ;
- BLOCKED → refus ;
- Target limitée au PID lancé/descendants ;
- raccourcis globaux Windows interdits ;
- `#TypeInput` conserve l'Unicode complet ; un `#Key` caractère simple utilise le layout clavier du thread de `TARGET_WINDOW` via `VkKeyScanExW` (avec fallback Unicode lorsque nécessaire) ;
- aucun HWND arbitraire fourni par le LLM ;
- impossible de reprendre `PAUSED` automatiquement ;
- reprise d’une directive déjà traitée uniquement au démarrage Auto avec ID/type exacts ;
- clipboard interne normalisé pour éviter l’auto-lecture.

## 11. Données / confidentialité

- fonctionnement local ;
- pas de télémétrie par défaut ;
- le presse-papiers général n’est pas historisé ;
- seuls les messages conformes au protocole sont traités ;
- redaction de secrets avant renvoi au LLM ;
- fichiers `.env` non intégrés automatiquement.

## 12. Persistance

Configuration : `~/.clipboard_agent_relay/settings.json`.

Persistants notamment : Goal/projet, setup Auto, template Copier, timings, seuils.

`Nouveau projet` remet le contexte de travail à zéro mais ne supprime pas le setup Auto global. Une TestSession doit être fermée avant changement de projet manuel ; en Agent Auto, la réconciliation de session est gérée par le Relay entre directives.

## 13. Architecture

```text
clipboard_agent/
  app.py                  UI + orchestration compatible
  profiled_app.py         couche active profils + cycle TestSession Auto
  managed_test_session.py capture/readiness persistante réconciliée
  protocol.py             parser/formatter protocole
  models.py               modèles typés
  state_machine.py        machine Agent Auto
  workspace.py            binding Z-order/workspaces LLM/Target
  test_session.py         TestSession persistante de base
  target_session.py       TEMP_TEST temporaire / compatibilité V1
  win32_input.py          Win32 mouse/keyboard/window/capture
  execution.py            commandes/processus
  visual_watch.py         mouvement/stabilité
  visual_match.py         détection Copier
  security.py             classification
  redaction.py            secrets
  storage.py              settings
  prompt_builder.py       instructions LLM
```

## 14. Machine d’état

Voir `STATE_MACHINE.md`. Les transitions sont typées et les transitions impossibles lèvent une erreur fail-safe.

## 15. Tests / critères de recette

Obligatoires :

- parser de toutes les directives ;
- anti-doublon/reprise ;
- machine d’état ;
- persistance settings ;
- clipboard ;
- timings ;
- matching visuel ;
- workspaces Z-order/focus ;
- TestSession open→actions→close avec même processus ;
- réconciliation Auto d’une session restante avant nouvel `OPEN_TEST_SESSION`/`EXECUTION` ;
- `CLOSE_TEST_SESSION` idempotent et `TEST_ACTIONS` sans session récupérable ;
- normalisation Auto `LOST`→`CLOSED` ;
- préparation Target avant chaque poll de readiness/capture différée ;
- checkpoints et screenshot markers ;
- `TEMP_TEST` + ancien format temporaire V1 non régressés ;
- intervention souris ;
- redaction ;
- démarrage interface Tk ;
- compilation Python ;
- scripts Bash ;
- archive de distribution réextraite/retestée.

La validation Windows physique (`SendInput`, hook global, GDI/Z-order sur bureau interactif) doit être rejouée sur une vraie session Windows ; la CI non interactive ne peut pas la remplacer.

## 16. Hors périmètre actuel

- DOM/endpoint privé du chat Web ;
- OCR du chat ;
- sandbox système complète ;
- contrôle générique du bureau par le LLM ;
- contournement de protections anti-bot ;
- exécution parallèle de plusieurs TestSessions.