# Cahier des charges — Vision, preuves et interaction Unity autonomes

**Projet :** Auto-DeepSeek / Clipboard Agent Relay  
**Statut :** spécification d’architecture — à implémenter  
**Profil concerné :** UnityProfile  
**Base fonctionnelle :** V2.15 + architecture profils  
**Date :** 2026-09-23

---

# 1. Objet

Ce document définit le système visuel du profil Unity.

Le but n’est pas seulement de « prendre des captures d’écran ». Le but est de permettre à un agent autonome de :

- voir ce que rend le jeu ;
- voir la scène de travail ;
- inspecter l’Editor lorsque nécessaire ;
- interagir avec le vrai Player ;
- placer des points de preuve dans le code lorsque le test l’exige ;
- associer une image à un état sémantique réel ;
- connaître la fiabilité de chaque observation ;
- ne pas avoir à choisir lui-même le backend de capture.

Le principe central est :

> **L’agent exprime ce qu’il veut observer ou tester. UnityProfile choisit comment produire la meilleure preuve.**

Le LLM ne doit pas administrer :

- le Z-order ;
- le backend de screenshot ;
- MCP vs Pipeline vs Win32 ;
- le moment exact du frame ;
- le fallback de capture ;
- les retries ;
- la restauration du workspace ;
- la plomberie des fichiers image.

---

# 2. Modèle mental simple pour l’agent

Le contrat LLM doit rester volontairement petit.

L’agent doit pouvoir raisonner avec seulement quatre intentions visuelles :

```text
OBSERVE_GAME      voir ce que voit le joueur
OBSERVE_SCENE     voir la scène de travail Unity
OBSERVE_EDITOR    voir l’interface Unity elle-même
OBSERVE_RUNTIME   voir le vrai Player construit
```

Et trois intentions d’interaction :

```text
INTERACT_RUNTIME  clavier/souris sur le Player
FRAME_OBJECT      cadrer un objet dans la Scene View
PROBE             demander une preuve instrumentée à un moment précis
```

L’agent ne doit pas avoir à écrire :

```text
utilise MCP capture_game_view
```

ou :

```text
mets Unity foreground puis capture avec Win32
```

Il doit demander :

```text
observe le jeu
```

Le Relay choisit le chemin technique.

---

# 3. Principes d’architecture

## 3.1 Modification par API, validation par vision

Pour construire un jeu Unity :

```text
API / Unity CLI / Pipeline
    → modifier

Vision
    → vérifier
```

La vision ne doit pas devenir le mécanisme normal de modification de :

- GameObjects ;
- Components ;
- Transform ;
- Prefabs ;
- Scene hierarchy ;
- ProjectSettings.

Les clics/drag dans Scene View ou Inspector restent des fallbacks exceptionnels.

## 3.2 Une observation n’est pas forcément une preuve fiable

Chaque observation doit déclarer sa provenance et sa qualité.

Une image issue du framebuffer du jeu après fin de frame est plus fiable qu’une capture OS d’une fenêtre partiellement occultée.

Le système ne doit jamais présenter ces deux preuves comme équivalentes.

## 3.3 La meilleure preuve est souvent multimodale

Une validation forte combine :

```text
état Unity structuré
+ valeurs sémantiques
+ image native
+ éventuellement capture Player réelle
+ logs
```

L’image seule ne doit pas porter toute la validation lorsque des données structurées existent.

---

# 4. Architecture cible

```text
                         UnityProfile
                              │
                ┌─────────────┼─────────────┐
                │             │             │
                ▼             ▼             ▼
        Semantic Tools   VisualRouter    RuntimeProvider
                │             │             │
                │      ┌──────┼──────┐      │
                │      │      │      │      │
                │      ▼      ▼      ▼      ▼
                │    Game   Scene   Editor  Player.exe
                │    View   View    Window      │
                │      │      │      │          │
                │      └──────┼──────┘          │
                │             │                 │
                └─────────────┼─────────────────┘
                              ▼
                       EvidenceBuilder
                              │
                              ▼
                       EvidenceBundle
                              │
                              ▼
                             LLM
```

Composants minimum :

```text
UnityVisualRouter
UnityGameViewProvider
UnitySceneViewProvider
UnityEditorWindowProvider
UnityRuntimeVisualProvider
UnityVisualProbeProvider
EvidenceBuilder
VisualArtifactStore
```

---

# 5. UnityVisualRouter

## 5.1 Responsabilité

Le Router reçoit une intention et sélectionne automatiquement le meilleur provider disponible.

Exemple :

```text
Intent: OBSERVE_GAME
```

Ordre cible :

1. capture native Unity Game View ;
2. outil MCP Game View si disponible ;
3. capture OS/Win32 de Game View ;
4. erreur explicite si aucune preuve exploitable.

Pour :

```text
Intent: OBSERVE_RUNTIME
```

Ordre cible :

1. TargetSession Player ;
2. capture native instrumentée du Player si disponible ;
3. backend Windows alternatif ;
4. erreur explicite.

## 5.2 Le Router ne doit pas cacher les fallbacks

Le LLM ne choisit pas le fallback, mais le résultat doit l’indiquer.

Exemple :

```text
CaptureMode: OS_FALLBACK
FallbackUsed: YES
VisualConfidence: MEDIUM
```

---

# 6. Modèle EvidenceBundle

Chaque observation Unity retourne conceptuellement :

```text
EvidenceBundle
    request_id
    intent
    source
    capture_mode
    visual_confidence
    frame_stable
    occlusion_safe
    fallback_used
    project_path
    editor_state
    play_mode
    scene
    timestamp
    image_artifacts[]
    semantic_facts{}
    logs[]
    warnings[]
```

Exemple :

```text
#VisualObservation
Source: UNITY_GAME_VIEW
CaptureMode: NATIVE
VisualConfidence: HIGH
OcclusionSafe: YES
FallbackUsed: NO
FrameStable: YES
Scene: Arena
PlayMode: PLAYING

SemanticFacts:
  playerHealth: 83
  ammo: 11
  enemiesAlive: 3
```

L’image est jointe comme artifact.

---

# 7. VisualConfidence

Niveaux minimum :

## HIGH

Preuve native ou instrumentée avec contexte cohérent.

Exemples :

- capture Game View native après fin de frame ;
- capture Scene View native ;
- VisualProbe ;
- framebuffer Player instrumenté.

## MEDIUM

Capture OS fiable mais dépendante de la fenêtre.

Exemples :

- Windows Graphics Capture ;
- capture de fenêtre Editor correctement identifiée.

## LOW

Capture dégradée ou dont l’occlusion/contexte ne peut pas être garanti.

Exemples :

- screenshot desktop fallback ;
- fenêtre partiellement masquée ;
- capture après timeout/recovery.

## INVALID

La capture ne peut pas être utilisée comme preuve.

Exemples :

- image noire persistante ;
- mauvais HWND ;
- résolution nulle ;
- contenu manifestement hors cible.

Une observation `INVALID` ne doit jamais être présentée à l’agent comme validation réussie.

---

# 8. Game View capture

## 8.1 Chemin nominal

Le profil doit préférer une capture native de la Game View.

Unity fournit des API `ScreenCapture` pour capturer la Game View.

Pour une sortie fiable, la capture doit être effectuée après la fin du rendu du frame.

La primitive Auto-DeepSeek Unity devra donc conceptuellement faire :

```text
request capture
    ↓
attendre fin du frame
    ↓
capture
    ↓
encode PNG
    ↓
artifact
```

## 8.2 Aucun dépendance au Z-order

Une capture native Game View doit rester exploitable même si :

- Clipboard Agent est devant ;
- Chrome est devant ;
- une autre application masque l’Editor.

Le résultat doit alors déclarer :

```text
OcclusionSafe: YES
```

## 8.3 MCP

Si la version de Unity CLI installée expose une capture Game View via MCP, le provider peut l’utiliser comme source native/secondaire.

Le provider MCP ne doit pas devenir nécessaire au pilotage métier général de Unity.

---

# 9. Scene View capture

## 9.1 Objectif

Permettre à l’agent de vérifier visuellement :

- level design ;
- placement ;
- volumes ;
- colliders ;
- lumière ;
- caméras ;
- relations spatiales.

## 9.2 Cadrage sémantique

L’agent ne doit pas manipuler la Scene View avec la souris pour cadrer un objet.

Le profil doit proposer des outils conceptuels tels que :

```text
scene_view.frame_object
scene_view.look_at
scene_view.set_rotation
scene_view.set_size
scene_view.capture
```

Exemple :

```text
FRAME_OBJECT Player
OBSERVE_SCENE
```

Le Relay :

1. trouve l’objet ;
2. positionne la Scene View ;
3. attend le rendu ;
4. capture ;
5. retourne l’image.

## 9.3 Contexte dans le résultat

Inclure lorsque disponible :

```text
SceneViewTarget: Player
SceneViewPivot: ...
SceneViewRotation: ...
SceneViewSize: ...
```

---

# 10. Capture de l’Editor

## 10.1 Cas d’usage

Seulement lorsqu’il faut observer :

- Hierarchy ;
- Inspector ;
- Console ;
- Package Manager ;
- dialogue modal ;
- problème d’UI Unity ;
- état que Pipeline ne permet pas de lire.

## 10.2 Backend

Ordre cible :

1. backend fenêtre moderne Windows si disponible ;
2. capture Win32 existante ;
3. screenshot desktop fallback.

## 10.3 Fiabilité

Une capture Editor basée sur le bureau ne doit pas être `HIGH` par défaut.

Elle doit déclarer explicitement :

```text
OcclusionSafe: NO
```

sauf si le backend utilisé garantit réellement une capture indépendante de l’occlusion.

---

# 11. Runtime Player

Le test gameplay réel doit privilégier un Player construit.

Workflow cible :

```text
build
    ↓
launch Player
    ↓
identify PID/HWND
    ↓
TargetSession
    ↓
input
    ↓
observe
    ↓
semantic probe if needed
    ↓
close/reconcile
```

Pourquoi :

- fenêtre dédiée ;
- environnement plus proche de l’utilisateur final ;
- pas de Scene/Hierarchy/Inspector autour ;
- coordonnées client stables ;
- clavier/souris déjà gérés par Auto-DeepSeek ;
- meilleure validation E2E.

---

# 12. Interaction gameplay

## 12.1 Actions autorisées

Réutiliser les primitives éprouvées :

```text
#Key
#TypeInput
#Click
#Wait
#Observe
```

Mais UnityProfile doit pouvoir construire des actions plus sémantiques par-dessus.

Exemples futurs :

```text
runtime.hold_key W 2.0s
runtime.move_mouse dx dy
runtime.click LEFT
runtime.wait_for_probe boss_ready
runtime.observe GAME
```

## 12.2 Souris relative

Pour FPS/jeux caméra libre, le profil devra supporter l’entrée souris relative de façon fiable.

Cette capacité doit être testée séparément des clics absolus de GUI.

## 12.3 Focus

Avant toute entrée runtime :

- Target doit être foreground ;
- HWND doit appartenir au Player attendu ;
- aucune intervention utilisateur détectée ;
- état runtime compatible.

---

# 13. VisualProbe

## 13.1 Objectif

Permettre une preuve exacte à un point précis du gameplay lorsque l’observation externe est insuffisante.

API conceptuelle :

```csharp
AutoDeepSeek.VisualProbe.Mark("boss_ready");
AutoDeepSeek.VisualProbe.Capture("after_shot");
```

Une version avec données :

```csharp
AutoDeepSeek.VisualProbe.Capture(
    "after_shot",
    new {
        ammo = weapon.Ammo,
        enemyHealth = target.Health,
        playerHealth = player.Health
    }
);
```

## 13.2 Capture

`Capture()` doit :

1. attendre le moment de rendu adéquat ;
2. capturer l’image native ;
3. sauvegarder l’artifact ;
4. enregistrer les données sémantiques ;
5. publier l’événement de probe ;
6. permettre au Relay de poursuivre immédiatement.

## 13.3 Isolation build

Le mécanisme doit pouvoir être désactivé pour un build de production.

Exemple :

```csharp
#if AUTODEEPSEEK_TEST
...
#endif
```

Le symbole exact sera défini lors de l’implémentation.

## 13.4 Usage raisonnable

Les probes ne doivent pas être ajoutés systématiquement.

Ordre préféré :

1. état natif Unity ;
2. test automatisé ;
3. capture native ;
4. VisualProbe seulement si un scénario précis l’exige.

---

# 14. Semantic facts

Une image peut être ambiguë.

Le système doit pouvoir joindre des faits machine-readable :

```text
playerHealth
ammo
score
enemiesAlive
currentWeapon
currentScene
playerPosition
objectiveState
```

Ces données ne doivent pas être supposées universelles.

Elles sont fournies :

- par VisualProbe ;
- par une commande Unity typée ;
- par le système de test du projet.

Le Relay ne doit jamais inventer un fait absent.

---

# 15. Evidence correlation

Chaque preuve doit porter :

```text
EvidenceID
RequestID
Timestamp
FrameNumber si disponible
Scene
RuntimeSessionID
```

L’objectif est d’éviter d’associer :

- une image ancienne ;
- un état récent ;
- des logs d’une autre session.

Lorsque image + faits sémantiques proviennent d’un même VisualProbe, ils doivent partager le même `EvidenceID`.

---

# 16. Stabilité avant capture

Le Router ne doit pas capturer arbitrairement pendant :

- compilation ;
- import ;
- transition PlayMode ;
- changement de scène ;
- démarrage Player ;
- fermeture Player.

Il utilise les Completion Barriers Unity.

Exemple :

```text
OBSERVE_GAME
    ↓
WAIT UNITY_QUIESCENT ou PLAYMODE_STABLE
    ↓
WAIT FRAME_END
    ↓
CAPTURE
```

Pour le runtime :

```text
OBSERVE_RUNTIME
    ↓
WAIT RUNTIME_READY
    ↓
CAPTURE
```

Le LLM ne gère pas ces attentes.

---

# 17. Interaction + capture atomique

Pour certains tests, l’action et l’observation doivent être considérées comme une seule transaction.

Exemple :

```text
runtime.action_and_observe
    actions:
      - click LEFT
    observe: GAME
    probe: after_shot
```

Le Relay garantit :

1. focus ;
2. action ;
3. attente du point de stabilisation ;
4. capture ;
5. collecte sémantique ;
6. résultat unique vers le LLM.

Ceci évite :

```text
LLM tire
→ tour suivant
LLM demande capture
```

avec risque d’avoir raté l’état intéressant.

---

# 18. Contrat LLM — simplicité obligatoire

Le prompt Unity doit présenter les règles suivantes, et pas la plomberie interne.

Texte conceptuel cible :

```text
Pour vérifier visuellement Unity, demande ce que tu veux voir :
- le jeu ;
- la scène ;
- l’éditeur ;
- le Player réel.

Le Relay choisit automatiquement la meilleure méthode de capture et attend que Unity soit dans un état stable.

Ne demande pas un backend de capture précis.
Ne gère pas le Z-order.
Ne boucle pas sur des screenshots.
Ne place un VisualProbe que lorsqu’une preuve à un instant précis est réellement nécessaire.

Chaque observation indique sa fiabilité.
Si VisualConfidence=INVALID, ne tire aucune conclusion visuelle.
Si VisualConfidence=LOW, traite l’image comme indicative.
```

Le LLM doit voir des intentions et résultats, pas `PrintWindow`, `MCP`, `WGC`, `WaitForEndOfFrame`, etc.

---

# 19. Outils métier visibles par l’agent

Surface minimale souhaitée :

```text
unity.observe_game
unity.observe_scene
unity.observe_editor
unity.observe_runtime
unity.scene_view.frame_object
unity.runtime.interact
unity.runtime.action_and_observe
unity.probe.wait
```

Outils avancés possibles :

```text
unity.scene_view.look_at
unity.scene_view.set_camera
unity.evidence.inspect
```

Ne pas exposer au LLM :

```text
capture_with_printwindow
capture_with_mcp
capture_with_wgc
capture_desktop
```

Ces fonctions restent internes au Router.

---

# 20. Résultats visuels simples

Exemple nominal :

```text
#RelayResult
Kind: TOOL
Tool: unity.observe_game
Status: SUCCESS
ProfileState: READY

#VisualObservation
VisualConfidence: HIGH
Source: UNITY_GAME_VIEW
FrameStable: YES
FallbackUsed: NO

SemanticFacts:
  scene: Arena
  playing: true
```

Exemple fallback :

```text
#VisualObservation
VisualConfidence: LOW
Source: DESKTOP_FALLBACK
FrameStable: UNKNOWN
FallbackUsed: YES
Warning: capture native indisponible
```

Le résultat doit rester compréhensible sans connaître l’architecture interne.

---

# 21. Politique de fallback

## Game

```text
Native Game capture
→ MCP Game capture
→ OS capture ciblée
→ INVALID
```

## Scene

```text
Native Scene capture
→ MCP Scene capture
→ OS capture ciblée
→ INVALID
```

## Editor

```text
Window capture moderne
→ Win32 existant
→ Desktop fallback
```

## Runtime

```text
TargetSession capture
→ backend Windows alternatif
→ VisualProbe native si disponible
→ INVALID
```

L’ordre exact pourra être ajusté après tests physiques, mais il doit être centralisé et testable.

---

# 22. Captures noires

Une image noire ou quasi noire ne doit pas automatiquement être rejetée : certains jeux peuvent légitimement afficher du noir.

La détection doit utiliser plusieurs indices :

- variance ;
- historique récent ;
- métadonnées du backend ;
- état de la fenêtre ;
- répétition ;
- comparaison avec une source secondaire si nécessaire.

Après retries/fallbacks épuisés :

```text
VisualConfidence: INVALID
OBSERVATION_WARNINGS: ...
```

Aucun faux succès visuel.

---

# 23. Windows Graphics Capture

Le système actuel Win32 reste utilisable.

Cependant, pour les fenêtres Unity/Player difficiles à capturer, prévoir un provider Windows moderne comme évolution du backend.

Objectif :

- meilleure compatibilité GPU ;
- capture indépendante de certains problèmes d’occlusion ;
- meilleure fiabilité qu’un screenshot desktop.

Ce backend doit rester caché au LLM derrière `UnityVisualRouter`.

---

# 24. Artifacts

Les screenshots ne doivent pas être encodés intégralement dans le texte du résultat.

`VisualArtifactStore` doit gérer :

- fichier PNG/JPEG ;
- EvidenceID ;
- source ;
- taille ;
- résolution ;
- hash ;
- expiration/nettoyage ;
- association à la session.

Un test long ne doit pas remplir indéfiniment le disque.

Prévoir une politique de rétention configurable.

---

# 25. Logs

Chaque capture doit journaliser :

- intent ;
- provider sélectionné ;
- provider essayé puis rejeté ;
- fallback ;
- durée ;
- résolution ;
- VisualConfidence ;
- warnings ;
- EvidenceID.

Cela doit permettre de diagnostiquer :

> « pourquoi cette image était-elle mauvaise ? »

sans demander au LLM de deviner.

---

# 26. Sécurité

Une observation visuelle ne doit pas :

- déplacer arbitrairement les fenêtres ;
- cliquer sauf intention explicite ;
- ignorer l’intervention utilisateur ;
- prendre une capture d’une autre application sans raison métier ;
- conserver des screenshots hors politique de rétention ;
- injecter des données sensibles dans le prompt sans borne.

Les interactions runtime conservent toutes les protections V2.15.

---

# 27. Tests unitaires

## V01 — Router Game

Game native disponible → choisie, aucun fallback.

## V02 — Router Game fallback

Native indisponible → MCP → résultat avec `FallbackUsed` correct.

## V03 — Capture native occlusion

Clipboard Agent foreground → capture native toujours `OcclusionSafe: YES`.

## V04 — Invalid capture

Toutes les sources échouent → `INVALID`, jamais SUCCESS visuel implicite.

## V05 — Frame barrier

Capture Game → attend fin du frame avant artifact.

## V06 — Scene frame object

Cadrage objet → capture correspond au nouvel état Scene View.

## V07 — Editor capture

Capture OS → confiance au plus MEDIUM sauf backend explicitement occlusion-safe.

## V08 — Runtime input

Input → bon HWND Player → capture associée à la même RuntimeSession.

## V09 — Intervention utilisateur

Intervention pendant interaction runtime → annulation fail-safe.

## V10 — Probe

Probe → image + semantic facts partagent le même EvidenceID.

## V11 — Action and observe

Action → barrier → capture → un seul résultat Relay.

## V12 — Stale artifact

Une image d’une ancienne session ne peut pas être associée à la session courante.

## V13 — Black retry

Capture suspecte → retry/fallback → warning correct.

## V14 — Artifact retention

Nettoyage respecte la politique configurée.

---

# 28. Tests physiques Windows

Une CI headless ne suffit pas.

Prévoir une fixture Unity réelle avec :

- Editor Unity 6 ;
- Unity CLI ;
- Pipeline ;
- scène simple ;
- Game View ;
- Player Windows ;
- objet mobile ;
- HUD ;
- probe de test.

Matrice physique :

1. Game View visible ;
2. Game View masquée par Clipboard Agent ;
3. Editor minimisé si backend le permet ;
4. Scene View cadrage Player ;
5. dialogue modal Unity ;
6. Player foreground ;
7. Player masqué ;
8. jeu GPU ;
9. écran noir légitime ;
10. capture pendant transition PlayMode ;
11. VisualProbe synchronisé ;
12. intervention souris utilisateur.

---

# 29. Phasage

## VIS0 — Modèles et Router

- EvidenceBundle ;
- VisualConfidence ;
- VisualIntent ;
- Router ;
- fake providers ;
- tests.

## VIS1 — Game View native

- capture native ;
- frame barrier ;
- artifact ;
- résultat.

## VIS2 — Scene View

- capture ;
- frame object ;
- camera control minimal.

## VIS3 — Runtime Player

- liaison UnityRuntimeProvider ↔ TargetSession ;
- action_and_observe ;
- preuve session-aware.

## VIS4 — VisualProbe

- package Unity ;
- événements ;
- semantic facts ;
- EvidenceID.

## VIS5 — Editor / fallbacks

- capture Editor ;
- provider Windows moderne ;
- politiques fallback.

## VIS6 — MCP optionnel

- détection outils disponibles ;
- provider capture Game/Scene ;
- fallback explicite.

---

# 30. Definition of Done

Le système visuel Unity est accepté si un agent peut :

1. demander simplement « observe le jeu » ;
2. recevoir une capture Game View fiable sans gérer le Z-order ;
3. demander « montre le Player dans la scène » ;
4. obtenir un cadrage Scene View automatique ;
5. construire/lancer le Player ;
6. interagir clavier/souris avec lui ;
7. demander une observation immédiatement après une action ;
8. recevoir image + état sémantique corrélés ;
9. utiliser un VisualProbe pour un événement précis ;
10. savoir si une image est fiable ou fallback ;
11. ne jamais recevoir une image invalide présentée comme preuve valide ;
12. continuer Agent Auto sans micro-gérer les backends de vision.

---

# 31. Règles à retenir

1. **Le LLM demande une intention visuelle, jamais un backend.**
2. **UnityProfile choisit automatiquement la meilleure preuve.**
3. **Capture native avant capture de bureau.**
4. **API pour modifier, vision pour vérifier.**
5. **Player construit pour le vrai test gameplay.**
6. **VisualProbe seulement quand une preuve précise l’exige.**
7. **Chaque image expose sa fiabilité.**
8. **Image + état + logs forment une preuve plus forte qu’une image seule.**
9. **Les barrières de stabilité sont gérées par le Relay.**
10. **Le prompt agent reste court et orienté intention.**

---

# 32. Références de conception

Documentation Unity consultée :

- Unity CLI installé sur la machine de test : `1.0.0-beta.11`, avec notamment `mcp`, `command`, `list`, `recompile`, `status`, `shell`, `test`, `build` et sorties JSON/NDJSON.
- Unity CLI release notes : outils MCP de capture Game View / Scene View et fallback OS documenté.
- Unity `ScreenCapture.CaptureScreenshotAsTexture` : capture Game View ; Unity précise qu’une sortie fiable nécessite d’attendre la fin du rendu du frame.
- Unity `ScreenCapture.CaptureScreenshotIntoRenderTexture` : variante RenderTexture pouvant être associée à `AsyncGPUReadback`.
- Unity `SceneView` : pivot/rotation/caméra contrôlables par API Editor.

Références officielles :

- https://docs.unity.com/en-us/unity-cli/release-notes
- https://docs.unity3d.com/6000.0/Documentation/ScriptReference/ScreenCapture.CaptureScreenshotAsTexture.html
- https://docs.unity3d.com/6000.0/Documentation/ScriptReference/ScreenCapture.CaptureScreenshotIntoRenderTexture.html
- https://docs.unity3d.com/6000.0/Documentation/ScriptReference/SceneView.html

Les capacités Unity CLI étant encore évolutives, l’implémentation doit continuer à découvrir les outils réellement disponibles sur la version installée plutôt que coder en dur la surface complète du CLI.
