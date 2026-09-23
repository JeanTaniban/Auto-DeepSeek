# Mission — Machine d'état Unity complète et conformité du runtime

**Branche :** `feat/unity-state-machine`  
**Base :** `main` @ `d4f2770`

## Objectif

Définir une machine d'état explicite, compréhensible et testable pour l'ensemble du fonctionnement Auto-DeepSeek avec Unity CLI, puis aligner le code existant sur cette machine.

La machine doit permettre à un agent de développer un jeu Unity sur une longue durée sans devoir gérer lui-même la plomberie de compilation, import, domain reload, Play Mode, tests, build, lancement du Player, observation visuelle ou nettoyage des sessions.

## Principe d'architecture

Le système n'est pas une seule machine géante. Quatre machines orthogonales se combinent :

1. **Relay / Agent Auto** — cycle conversationnel LLM ↔ Relay ;
2. **Profile Tool Host** — une opération métier de profil à la fois ;
3. **Unity Domain State** — état réel du projet/Editor/CLI ;
4. **TargetSession / Runtime** — interaction avec une fenêtre Player ou application externe.

Le LLM ne doit voir qu'un état métier simple (`READY`, `BUSY`, `USER_ACTION_REQUIRED`, `DEGRADED`, `ERROR`) et les résultats utiles. Les transitions techniques restent internes.

Le document de référence détaillé de cette mission est `UNITY_STATE_MACHINE.md`. `STATE_MACHINE.md` reste la référence de la machine cœur/TestSession V2.15 ; les deux machines sont orthogonales et le code les relie via le Profile Tool Host.

## Audit initial — écarts constatés

- `Action: TOOL` utilisait encore `AutoState.EXECUTING`, le même état que les commandes shell génériques ; la machine Relay ne distinguait donc pas explicitement une opération de profil.
- `UnityProfileState` était une simple enum : aucune transition n'était validée.
- `health_check()` assignait directement `READY`, `ERROR` ou `USER_ACTION_REQUIRED` sans passer par `CHECKING`.
- `unity.recompile` assignait directement `COMPILING` puis l'état final, sans machine de transitions.
- plusieurs états Unity prévus (`EDITOR_STARTING`, `IMPORTING`, `PLAY_MODE`, `TESTING`, `BUILDING`, `RUNTIME_TESTING`) n'étaient pas raccordés à un scheduler ; ils étaient documentaires seulement.
- `ToolDescriptor.allowed_states` existait mais n'était pas appliqué par `ProfileManager.execute_tool()`.
- un outil Unity pouvait donc être demandé alors que le profil était `UNINITIALIZED`, `USER_ACTION_REQUIRED` ou `ERROR` sans garde générique.
- la compilation était déjà transactionnelle, mais le contrat ne modélisait pas explicitement reconnect/domain reload, import ou quiescence.
- la documentation cœur ne couvrait pas encore la machine Unity ajoutée avec le système de profils.

## Référence Unity CLI vérifiée

La conception tient compte des comportements actuels du CLI expérimental :

- `unity status` distingue `starting` et `ready` et produit des sorties structurées ;
- `unity status`, `unity editors running` et `unity command` retentent après recompilation avant de conclure trop vite que l'Editor est inaccessible ;
- Pipeline expose notamment des opérations de recompilation et tests ;
- les jobs longs disposent de `job status/wait/cancel` ;
- `unity test` distingue échec réel des tests et absence de verdict/infrastructure ;
- `unity shell --protocol ndjson` existe pour l'automatisation mais reste un transport futur ;
- l'installation de Pipeline impose d'attendre la recompilation du projet avant usage.

## Machine Unity retenue

États internes normalisés :

```text
UNINITIALIZED
CHECKING
READY                 # projet + CLI utilisables, Editor pas nécessairement connecté
EDITOR_STARTING
EDITOR_READY           # Editor connecté et quiescent
IMPORTING
COMPILING
RELOADING              # domain/assembly reload ou reconnexion Pipeline attendue
PLAYMODE_ENTERING
PLAY_MODE
PLAYMODE_EXITING
TESTING
BUILDING
RUNTIME_STARTING
RUNTIME_TESTING
USER_ACTION_REQUIRED
DEGRADED
ERROR
```

Les états transitoires sont `BUSY` côté LLM. `READY` et `EDITOR_READY` sont exposés comme `ProfileState.READY`.

## Invariants

- une seule opération métier mutable Unity à la fois ;
- une transition non autorisée échoue fermée ;
- une opération ne peut démarrer que depuis un état explicitement autorisé ;
- `UNINITIALIZED` déclenche un health check paresseux avant le premier outil normal ;
- `USER_ACTION_REQUIRED` n'est jamais contourné automatiquement ;
- une compile error est un verdict métier récupérable, pas une panne d'infrastructure ;
- après une recompilation réussie ou une erreur C# exploitable, l'état retourne à `EDITOR_READY` ;
- timeout / CLI inaccessible / verdict inutilisable → `ERROR` ;
- la récupération depuis `ERROR` ou `USER_ACTION_REQUIRED` passe explicitement par `unity.health` / `CHECKING` ;
- aucun polling ou `sleep` arbitraire n'est exposé au LLM ;
- TargetSession reste orthogonale à Unity Domain State : elle sert au Player/runtime et aux fallbacks GUI ;
- une fermeture/cleanup technique ne doit pas provoquer un tour LLM supplémentaire.

## Cycle métier cible pour créer un jeu

```text
CHECK / DISCOVER
  → inspect projet / packages / scènes / assets / VCS
  → modifier code / assets / scènes
  → IMPORTING ou COMPILING si nécessaire
  → RELOADING si nécessaire
  → EDITOR_READY
  → tests ciblés
  → tests plus larges
  → BUILDING
  → RUNTIME_STARTING
  → RUNTIME_TESTING
  → observation / interaction / preuves
  → retour EDITOR_READY ou READY
  → correction
  → répétition
```

Le modèle prévoit : ouverture/fermeture Editor, discovery, scènes/GameObjects/components/assets/packages/settings, imports, compilation/reload, Play Mode, EditMode/PlayMode tests, Build Profiles, Player runtime, interaction gameplay, captures, VCS/.meta/affected, package setup, licence/auth et récupération après crash.

## Corrections réalisées

1. `AutoState.PROFILE_TOOL_RUNNING` sépare maintenant les outils métier des commandes shell `EXECUTING` ;
2. `UnityStateMachine` valide une table explicite de transitions et refuse les transitions concurrentes incohérentes ;
3. `UnityProfile` utilise la machine pour health/compilation/récupération au lieu d'assignations directes ;
4. `ProfileManager` applique maintenant réellement `ToolDescriptor.allowed_states` après une préparation sûre du profil ;
5. `AgentProfile.prepare_tool()` permet un health check paresseux avant le premier outil ;
6. `unity.health` fournit un chemin de récupération explicite depuis `ERROR`, `USER_ACTION_REQUIRED`, `DEGRADED` ou `UNINITIALIZED` ;
7. le prompt Unity explique seulement le contrat utile : pas de polling, pas de concurrence, `unity.health` pour récupérer une panne ;
8. `UNITY_STATE_MACHINE.md` documente la machine composite et distingue clairement états déjà câblés et providers futurs ;
9. des tests couvrent transitions Relay, transitions Unity, états interdits, lazy health, CLI absent, compile error récupérable, timeout vers ERROR et récupération par health.

## Hors périmètre restant

Les états sont réservés et testés mais les providers réels suivants restent à implémenter :

- ouverture/attente Editor (`EDITOR_STARTING`) ;
- observation native import/quiescence ;
- `RELOADING` explicitement observé entre compile et reconnexion ;
- Play Mode ;
- tests Unity ;
- build ;
- lancement Player orchestré par UnityProfile ;
- Pipeline commands/jobs complets ;
- shell NDJSON persistant ;
- capture native Game/Scene View ;
- validation physique Unity locale.

Le code ne prétend pas que ces providers existent déjà : le document les marque comme cible future.

## Critères d'acceptation

- transitions Relay et Unity actuellement utilisées par le code explicites et testées ;
- `Action: TOOL` n'utilise plus l'état shell générique `EXECUTING` ;
- un outil ne s'exécute jamais dans un état profil non autorisé ;
- premier outil Unity capable d'auto-effectuer le health check depuis `UNINITIALIZED` ;
- Unity CLI absent → `USER_ACTION_REQUIRED` et outil bloqué ;
- `unity.recompile` suit `READY|EDITOR_READY -> COMPILING -> EDITOR_READY|ERROR` ;
- compile error C# → état récupérable `EDITOR_READY` ;
- timeout/infra → `ERROR`, puis récupération uniquement via `unity.health`/`CHECKING` ;
- toutes les machines actives conservent un chemin fail-safe ;
- machine complète compréhensible dans `UNITY_STATE_MACHINE.md`, avec présent/futur séparés ;
- CI Ubuntu/Windows Python 3.11/3.12 verte.

## Validation

- Une première CI de développement a échoué uniquement parce qu'un nouveau faux `ToolDescriptor` de test omettait son champ obligatoire `description`. Le test a été corrigé ; il ne s'agissait pas d'une panne runtime.
- La matrice suivante au commit `674f486` est verte sur Ubuntu/Windows × Python 3.11/3.12.
- Le test de récupération explicite `ERROR -> unity.health -> CHECKING -> READY` a ensuite été ajouté ; sa matrice doit être verte avant fusion.
- Aucune validation physique Unity n'est revendiquée ici : GitHub CI n'embarque pas l'Editor ni Unity CLI de la machine utilisateur.
