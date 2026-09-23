# Machine d'état Unity — Auto-DeepSeek

**Statut :** contrat runtime et guide d'intégration  
**Profil :** `unity`  
**Principe :** le LLM pilote le métier ; Auto-DeepSeek pilote les transitions techniques Unity.

---

## 1. Modèle mental pour l'agent

L'agent ne doit pas administrer Unity comme une suite de commandes bas niveau.

Il exprime une intention :

```text
modifier le code
recompiler
observer le jeu
lancer les tests
construire le Player
jouer / cliquer / tirer
inspecter une scène
```

Le Relay doit :

1. vérifier que l'opération est autorisée dans l'état courant ;
2. lancer l'outil approprié ;
3. attendre les effets secondaires Unity nécessaires ;
4. récupérer le verdict réel ;
5. seulement ensuite rendre la main au LLM.

Le LLM ne doit donc pas faire :

```text
compile
wait 5 s
status
wait 5 s
status
```

Le Relay doit faire cette attente lui-même avec des preuves natives.

---

# 2. Quatre machines orthogonales

Une seule machine globale serait difficile à comprendre et provoquerait une explosion combinatoire. Le fonctionnement est donc décomposé en quatre machines synchronisées.

```text
┌────────────────────────────────────────────────────┐
│ 1. Relay / Agent Auto                              │
│ conversation, copie, envoi, attente LLM            │
└───────────────────────┬────────────────────────────┘
                        │ Action: TOOL
┌───────────────────────▼────────────────────────────┐
│ 2. Profile Tool Host                               │
│ une opération métier de profil à la fois           │
└───────────────────────┬────────────────────────────┘
                        │ profil Unity
┌───────────────────────▼────────────────────────────┐
│ 3. Unity Domain State                              │
│ CLI / Editor / compile / import / tests / build    │
└───────────────────────┬────────────────────────────┘
                        │ Player réel si nécessaire
┌───────────────────────▼────────────────────────────┐
│ 4. TargetSession / Runtime                         │
│ clavier / souris / observation / fenêtre Player    │
└────────────────────────────────────────────────────┘
```

Les machines 2 à 4 sont techniques. Le LLM reçoit un résumé métier.

---

# 3. États exposés au LLM

Le LLM n'a normalement besoin que de :

```text
UNINITIALIZED
READY
BUSY
USER_ACTION_REQUIRED
DEGRADED
ERROR
```

Correspondance :

- `READY` : l'opération suivante peut être demandée ;
- `BUSY` : Auto-DeepSeek attend déjà Unity ; ne pas lancer une opération concurrente ;
- `USER_ACTION_REQUIRED` : installation, licence, authentification ou autre action humaine requise ;
- `DEGRADED` : fonctionnement partiel, fallback possible ;
- `ERROR` : échec infrastructure nécessitant diagnostic/récupération.

Les détails Unity sont fournis dans `DATA.unityState` lorsqu'ils sont utiles.

---

# 4. Machine Relay / Agent Auto

Pour les outils de profils :

```text
PROCESSING_INITIAL_REPLY / PROCESSING_REPLY
                    │
                    │ Action: TOOL
                    ▼
          PROFILE_TOOL_RUNNING
                    │
                    │ verdict métier final
                    ▼
                 SENDING
                    │
                    ▼
              WAITING_VISUAL
                    │
                    ▼
             WAITING_CLIPBOARD
                    │
                    ▼
             PROCESSING_REPLY
```

`PROFILE_TOOL_RUNNING` est distinct de `EXECUTING` :

- `EXECUTING` = commande shell générique ;
- `PROFILE_TOOL_RUNNING` = opération sémantique appartenant au profil actif.

Depuis tout état actif, intervention utilisateur ou fail-safe peut mener à `PAUSED` / `OFF` selon les règles du Relay.

---

# 5. Machine Unity interne

États du domaine Unity :

```text
UNINITIALIZED
CHECKING
READY
EDITOR_STARTING
EDITOR_READY
IMPORTING
COMPILING
RELOADING
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

## 5.1 États stables

### `UNINITIALIZED`

Le profil vient d'être créé ou réinitialisé. Aucun health check n'a encore prouvé que le projet et Unity CLI sont exploitables.

Premier outil demandé : Auto-DeepSeek effectue automatiquement un health check léger.

### `READY`

Projet Unity reconnu et Unity CLI disponible.

Cela ne garantit pas qu'un Editor soit actuellement connecté.

Opérations futures possibles :

- ouvrir Editor ;
- lancer une opération batch compatible ;
- compiler si le CLI peut joindre l'Editor ;
- diagnostics ;
- build/test batch selon politique ;
- lancer un runtime déjà construit.

### `EDITOR_READY`

Editor du bon projet connecté et quiescent.

Signification cible :

```text
pas de compilation
pas d'import bloquant
pas de transition Play Mode
pas de job exclusif en cours
Pipeline/CLI répond
```

C'est l'état nominal de travail interactif.

### `PLAY_MODE`

L'Editor est volontairement en Play Mode.

Modifications structurelles de scène/prefab doivent suivre une politique explicite ; aucune écriture risquée ne doit être lancée par défaut dans cet état.

### `USER_ACTION_REQUIRED`

Exemples :

- Unity CLI absent ;
- licence non activable automatiquement ;
- authentification interactive ;
- installation/upgrade nécessitant validation ;
- package Pipeline absent lorsque l'opération demandée en dépend.

Aucun outil métier normal ne contourne cet état.

### `DEGRADED`

Unity reste partiellement utilisable mais une capacité est indisponible ou un fallback plus faible est nécessaire.

Exemple futur : capture native indisponible mais capture Windows possible.

### `ERROR`

Échec d'infrastructure ou incohérence d'état.

La récupération normale repasse par `CHECKING`.

---

## 5.2 États transitoires

### `CHECKING`

Health check / redécouverte environnement.

### `EDITOR_STARTING`

Un Editor du projet est en démarrage. `unity status` peut indiquer `starting`; cela ne doit pas être traité comme `EDITOR_READY`.

### `IMPORTING`

AssetDatabase/import/refresh en cours.

### `COMPILING`

Compilation C# en cours.

### `RELOADING`

Domain/assembly reload ou reconnexion Pipeline attendue après compilation/import.

Une perte momentanée de connexion après recompilation est un événement attendu, pas immédiatement une panne.

### `PLAYMODE_ENTERING` / `PLAYMODE_EXITING`

Transitions explicites autour de Play Mode.

### `TESTING`

EditMode/PlayMode tests ou test job en cours.

### `BUILDING`

Build Player en cours.

### `RUNTIME_STARTING`

Player construit en cours de lancement et de détection.

### `RUNTIME_TESTING`

Player réel sous contrôle de TargetSession : clavier, souris, captures et assertions gameplay.

---

# 6. Transitions métier principales

## 6.1 Initialisation

```text
UNINITIALIZED
    ↓ health check automatique ou manuel
CHECKING
    ├── projet + CLI OK ───────────────→ READY
    ├── action humaine nécessaire ─────→ USER_ACTION_REQUIRED
    ├── fonctionnement partiel ────────→ DEGRADED
    └── incohérence / panne ───────────→ ERROR
```

`USER_ACTION_REQUIRED` et `ERROR` ne reviennent pas directement à `READY` :

```text
USER_ACTION_REQUIRED / ERROR
            ↓
         CHECKING
            ↓
     READY / ...
```

---

## 6.2 Ouverture Editor

Cible future :

```text
READY
 ↓ unity open
EDITOR_STARTING
 ↓ unity status = ready + bon project path
EDITOR_READY
```

Sur Windows, ne pas dépendre d'un simple délai. Poller l'état structuré avec timeout borné.

---

## 6.3 Modification de code C#

```text
EDITOR_READY ou READY
 ↓ écriture source
COMPILING
 ↓ unity recompile / statut compilation
[RELOADING]
 ↓ Editor reconnecté / verdict disponible
EDITOR_READY
```

Le LLM reçoit une seule réponse finale.

### Compile error

Une erreur C# est un **verdict métier récupérable** :

```text
COMPILING
 ↓ diagnostics valides
EDITOR_READY
```

Le résultat `TOOL` est `ERROR` avec `COMPILE_ERROR`, mais l'infrastructure Unity reste utilisable pour corriger le code.

### Timeout / CLI cassé

```text
COMPILING
 ↓ aucun verdict fiable
ERROR
```

---

## 6.4 Import d'asset / package / settings

Cible future :

```text
EDITOR_READY
 ↓ modification/import
IMPORTING
 ├─ pas de code affecté ───────────────→ EDITOR_READY
 └─ code/assemblies affectés ──────────→ COMPILING
                                          ↓
                                      [RELOADING]
                                          ↓
                                      EDITOR_READY
```

L'installation de Pipeline suit ce principe : après ajout du package, attendre réellement la recompilation avant de considérer Pipeline disponible.

---

## 6.5 Play Mode

```text
EDITOR_READY
 ↓ enter play mode
PLAYMODE_ENTERING
 ↓ état confirmé
PLAY_MODE
 ↓ exit play mode
PLAYMODE_EXITING
 ↓ quiescence confirmée
EDITOR_READY
```

Le profil doit tenir compte des options Unity qui peuvent désactiver certains reloads à l'entrée Play Mode ; il ne doit pas déduire un domain reload systématique.

---

## 6.6 Tests

Deux modes devront être distingués.

### Tests via Editor connecté

```text
EDITOR_READY
 ↓ run_tests / command
TESTING
 ↓ verdict
EDITOR_READY
```

### Tests batch

```text
READY
 ↓ unity test
TESTING
 ↓ verdict
READY
```

Résultats :

- tests réellement exécutés avec échec → `TEST_FAILURE`, état stable restauré ;
- compilation/licence/crash/timeout sans verdict → `INFRA_FAILURE` / `ERROR` ou récupération explicite.

Ne jamais confondre ces deux catégories.

---

## 6.7 Build

```text
READY ou EDITOR_READY
 ↓ build
BUILDING
 ├─ build terminé ─────────────→ état stable précédent
 ├─ build + lancement demandé ─→ RUNTIME_STARTING
 └─ infra failure ─────────────→ ERROR
```

Le scheduler futur devra choisir entre build connecté et batch selon les capacités/version et éviter deux Editors concurrents sur le même projet.

---

## 6.8 Runtime Player / gameplay

```text
BUILDING ou READY ou EDITOR_READY
 ↓ lancement Player
RUNTIME_STARTING
 ↓ process + HWND + readiness
RUNTIME_TESTING
 ↓ actions / observations / probes
READY ou EDITOR_READY
```

`RUNTIME_TESTING` compose la machine Unity avec la machine `TestSessionState` :

```text
Unity: RUNTIME_TESTING
TestSession: ACTIVE_BACKGROUND / ACTIVE_FOREGROUND
```

L'état exact de TestSession reste géré par la couche TargetSession ; le LLM ne ferme pas les sessions de maintenance.

---

# 7. Vision et preuves

Une observation ne doit normalement pas créer un nouvel état Unity si elle est purement read-only.

Intentions :

```text
GAME
SCENE
EDITOR
RUNTIME
```

Le routeur choisit le backend.

Exemples :

```text
EDITOR_READY + OBSERVE_SCENE → EDITOR_READY
PLAY_MODE    + OBSERVE_GAME  → PLAY_MODE
RUNTIME_TESTING + OBSERVE_RUNTIME → RUNTIME_TESTING
```

Une observation demandée pendant `COMPILING`, `IMPORTING`, `RELOADING`, `BUILDING` ou autre état exclusif doit attendre la barrière ou être refusée selon son contrat ; elle ne doit pas provoquer une course avec l'opération en cours.

---

# 8. Completion barriers

Les états disent **où se trouve Unity**. Les barrières disent **quand une opération peut être considérée terminée**.

```text
NONE
EDITOR_QUIESCENT
IMPORT_SETTLED
COMPILE_SETTLED
RELOAD_SETTLED
IMPORT_AND_COMPILE_SETTLED
PLAYMODE_ENTERED
PLAYMODE_EXITED
TEST_VERDICT
BUILD_FINISHED
RUNTIME_READY
```

Exemples :

| Opération | Effet | Barrière |
|---|---|---|
| inspecter une scène | lecture | `NONE` / `EDITOR_QUIESCENT` |
| écrire `.cs` | compilation possible | `COMPILE_SETTLED` |
| modifier `.asmdef` | compile + reload | `RELOAD_SETTLED` |
| importer package | import + compile possibles | `IMPORT_AND_COMPILE_SETTLED` |
| entrer Play Mode | changement runtime Editor | `PLAYMODE_ENTERED` |
| tests | verdict | `TEST_VERDICT` |
| build | artifact final | `BUILD_FINISHED` |
| lancer Player | fenêtre/process prêt | `RUNTIME_READY` |

Le LLM ne choisit pas la barrière ; elle appartient au `ToolDescriptor`.

---

# 9. Opérations nécessaires pendant la création complète d'un jeu

La machine doit pouvoir accueillir les familles suivantes sans ajouter de logique métier dans `app.py`.

## Projet / environnement

- détecter projet/version ;
- health/doctor/licence/auth ;
- détecter/install modules ;
- Pipeline install/upgrade ;
- capabilities discovery ;
- ouvrir/fermer Editor de façon sûre.

## Code

- lire/rechercher scripts ;
- créer/modifier/refactorer C# ;
- asmdef ;
- compilation ;
- diagnostics ;
- domain reload/reconnect.

## Scènes / objets

- créer/ouvrir/sauvegarder scène ;
- GameObjects ;
- composants ;
- parentage ;
- Transform ;
- prefabs ;
- ScriptableObjects ;
- settings.

## Assets

- importer/déplacer/supprimer ;
- préserver `.meta`/GUID ;
- materials/textures/audio ;
- Addressables si présents ;
- AssetDatabase refresh/import.

## Gameplay

- Play Mode ;
- état sémantique ;
- VisualProbe ;
- Game View ;
- interaction Player réel.

## Tests

- EditMode ;
- PlayMode ;
- tests ciblés ;
- tests affectés ;
- coverage ;
- distinction test failure / infra failure.

## Build

- targets ;
- Build Profiles ;
- desktop/mobile/WebGL selon projet ;
- build artifact ;
- logs ;
- signature avec politique secrets stricte.

## Runtime

- lancer build ;
- trouver process/HWND ;
- clavier/souris ;
- souris relative pour FPS ;
- capture ;
- logs ;
- crash detection ;
- fermeture/cleanup.

## Validation visuelle

- Scene View ;
- Game View ;
- Editor entier ;
- Player ;
- probes sémantiques ;
- confiance/fallback/provenance.

## Source control

- Git reste autorité ;
- Unity VCS helpers pour `.meta`, diff sémantique, affected ;
- pas de commit tant que compilation/tests requis ne sont pas stabilisés.

---

# 10. Règles de concurrence

Une seule opération mutable Unity est autorisée à la fois.

États exclusifs :

```text
CHECKING
EDITOR_STARTING
IMPORTING
COMPILING
RELOADING
PLAYMODE_ENTERING
PLAYMODE_EXITING
TESTING
BUILDING
RUNTIME_STARTING
RUNTIME_TESTING   # sauf actions runtime de la même session
```

Une seconde opération incompatible est refusée, pas mise en concurrence.

Les futures lectures compatibles peuvent être autorisées explicitement par descriptor ; elles ne doivent jamais être supposées compatibles par défaut.

---

# 11. Récupération et crash

## CLI/Pipeline momentanément inaccessible après compile

Si une compilation/reload vient d'être déclenchée :

```text
COMPILING
 → RELOADING
 → retry/re-discovery borné
 → EDITOR_READY
```

Ne pas conclure immédiatement `ERROR` sur la première perte de connexion attendue.

## Editor crash

```text
état Editor actif
 → ERROR
 → diagnostic logs
 → CHECKING
 → READY / EDITOR_STARTING / USER_ACTION_REQUIRED
```

## Player crash

```text
RUNTIME_TESTING
 → état TargetSession LOST
 → cleanup automatique
 → EDITOR_READY ou READY
```

Le crash est envoyé au LLM comme résultat du test/runtime, sans lui demander de fermer une session technique perdue.

---

# 12. Contrat d'état du code actuel

Implémenté maintenant :

- machine Relay avec `PROFILE_TOOL_RUNNING` ;
- `UnityStateMachine` avec transitions validées ;
- health : `UNINITIALIZED → CHECKING → READY|USER_ACTION_REQUIRED|ERROR` ;
- premier TOOL Unity : health paresseux si nécessaire ;
- manager : application de `ToolDescriptor.allowed_states` ;
- compile : `READY|EDITOR_READY → COMPILING → EDITOR_READY|ERROR` ;
- observation : lecture depuis un état profil `READY`, sans mutation de l'état Unity ;
- image artifact renvoyée par le Profile Tool Host ;
- TargetSession existante reste orthogonale.

Prévu mais pas encore implémenté comme provider réel :

- `EDITOR_STARTING` ;
- `IMPORTING` ;
- `RELOADING` explicite dans le coordinateur ;
- Play Mode ;
- tests Unity ;
- build ;
- runtime Player piloté par UnityProfile ;
- Pipeline commands/jobs ;
- captures natives Game/Scene.

Ces états sont déjà définis afin que les futures briques se branchent sans modifier le modèle global.

---

# 13. Références Unity vérifiées

- Unity CLI reference / releases : https://docs.unity.com/en-us/unity-cli/release-notes
- Unity CLI introduction : https://docs.unity.com/en-us/unity-cli/unity-cli
- Unity Pipeline package : https://docs.unity.com/en-us/unity-production-pipeline/local-tools-cli/unity-pipeline-package
- Domain reload / Play Mode : https://docs.unity.com/en-us/engine/6000.6/manual/scripting/compilation-and-code-reload/code-reloading-editor/domain-reloading

La CLI étant expérimentale, la version installée et `unity commands --format json` restent l'autorité opérationnelle.
