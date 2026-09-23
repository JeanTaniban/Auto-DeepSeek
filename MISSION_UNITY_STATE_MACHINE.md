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

Le LLM ne doit voir qu'un état métier simple (`READY`, `BUSY`, `ACTION_REQUIRED`, `ERROR`) et les résultats utiles. Les transitions techniques restent internes.

## Audit initial — écarts constatés

- `Action: TOOL` utilise encore `AutoState.EXECUTING`, le même état que les commandes shell génériques ; la machine Relay ne distingue donc pas explicitement une opération de profil.
- `UnityProfileState` est actuellement une simple enum : aucune transition n'est validée.
- `health_check()` assigne directement `READY`, `ERROR` ou `USER_ACTION_REQUIRED` sans passer par `CHECKING`.
- `unity.recompile` assigne directement `COMPILING` puis l'état final, sans machine de transitions.
- plusieurs états Unity prévus (`EDITOR_STARTING`, `IMPORTING`, `PLAY_MODE`, `TESTING`, `BUILDING`, `RUNTIME_TESTING`) ne sont pas encore raccordés à un scheduler ; ils sont documentaires seulement.
- `ToolDescriptor.allowed_states` existe mais n'est pas appliqué par `ProfileManager.execute_tool()`.
- un outil Unity peut donc être demandé alors que le profil est `UNINITIALIZED`, `USER_ACTION_REQUIRED` ou `ERROR` ; aujourd'hui le runtime ne l'interdit pas génériquement.
- la compilation est déjà transactionnelle, mais le contrat ne modélise pas encore explicitement reconnect/domain reload, import ou quiescence.
- `STATE_MACHINE.md` ne documente pas encore `Action: TOOL` ni la machine Unity ajoutée après V2.15.

## Référence Unity CLI vérifiée

La conception doit tenir compte des comportements actuels du CLI expérimental :

- `unity status` distingue désormais `starting` et `ready` et produit des sorties structurées ;
- `unity status`, `unity editors running` et `unity command` retentent une fois après recompilation avant de conclure que l'Editor est inaccessible ;
- `unity command`/Pipeline expose notamment `recompile`, `recompile_status`, `run_tests`, `test_status` ;
- les jobs détachés sont gérés par `unity job status/wait/cancel` ;
- `unity test` distingue échec de tests (`8`) et absence de verdict/infrastructure (`6`) ;
- `unity shell --protocol ndjson` existe pour les agents mais reste un transport futur ;
- Unity Pipeline nécessite d'attendre la recompilation du projet après installation.

## Machine Unity cible

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
- `UNINITIALIZED` déclenche un health check paresseux avant le premier outil ;
- `USER_ACTION_REQUIRED` n'est jamais contourné automatiquement ;
- une compile error est un verdict métier récupérable, pas une panne d'infrastructure ;
- après une recompilation réussie ou échouée, l'état retourne à `EDITOR_READY` si l'Editor a fourni un verdict exploitable ;
- timeout / CLI inaccessible / protocole invalide → `ERROR` ou `DEGRADED` selon récupération possible ;
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

Les futures opérations devront couvrir :

- ouverture/fermeture Editor ;
- discovery de capabilities ;
- inspection scènes/GameObjects/components/assets/packages/settings ;
- édition scènes/prefabs/components/assets ;
- import/refresh ;
- compilation/reload ;
- Play Mode enter/exit ;
- EditMode/PlayMode tests ;
- Build Profiles / builds ;
- lancement Player ;
- interaction gameplay ;
- captures Game/Scene/Editor/Runtime ;
- VCS/.meta/affected tests ;
- package install/upgrade ;
- diagnostics/licence/auth ;
- cleanup et récupération après crash.

## Corrections à implémenter dans cette mission

1. ajouter un état Relay explicite `PROFILE_TOOL_RUNNING` ;
2. introduire `UnityStateMachine` avec table de transitions et erreur fail-safe ;
3. faire utiliser cette machine à `UnityProfile` au lieu d'assignations directes ;
4. appliquer `ToolDescriptor.allowed_states` dans `ProfileManager` ;
5. ajouter une préparation paresseuse du profil avant le premier outil pour éviter un faux blocage au démarrage ;
6. documenter la machine composite complète dans `STATE_MACHINE.md` et un document Unity dédié ;
7. ajouter des tests de transitions, d'états interdits et de conformité du chemin `TOOL`.

## Hors périmètre de cette mission

- implémenter tous les futurs providers Unity (tests/build/playmode/packages) ;
- shell NDJSON persistant ;
- Pipeline réel sur la machine utilisateur ;
- capture native Game/Scene View ;
- validation physique Unity locale.

La machine doit néanmoins prévoir ces états et rendre leur future intégration évidente.

## Critères d'acceptation

- toutes les transitions Relay et Unity utilisées par le code sont explicites et testées ;
- `Action: TOOL` n'utilise plus l'état shell générique `EXECUTING` ;
- un outil ne s'exécute jamais dans un état profil non autorisé ;
- le premier outil Unity peut auto-effectuer le health check si le profil est encore `UNINITIALIZED` ;
- une absence de Unity CLI place le profil en `USER_ACTION_REQUIRED` et bloque proprement l'outil ;
- `unity.recompile` suit une transition `READY|EDITOR_READY -> COMPILING -> EDITOR_READY|ERROR` valide ;
- toutes les machines actives conservent un chemin fail-safe ;
- `STATE_MACHINE.md` décrit le fonctionnement réellement implémenté et distingue clairement présent/futur ;
- CI Ubuntu/Windows Python 3.11/3.12 verte.
