# Cahier des charges — Architecture de profils et profil Unity v1

**Projet :** Auto-DeepSeek / Clipboard Agent Relay  
**Statut :** spécification d’architecture — à implémenter  
**Base actuelle :** V2.14, commit de référence `a93cc74`  
**Date :** 2026-09-22

---

## 1. Objet du document

Ce document définit l’architecture à mettre en place pour transformer Auto-DeepSeek d’un agent de développement généraliste en un **orchestrateur extensible par profils métier**, sans casser le fonctionnement actuel.

Le premier profil spécialisé à implémenter est **Unity**.

Le terme **profil** désigne ici un ensemble cohérent de :

- détection de projet ;
- capacités métier ;
- outils natifs ;
- règles de sécurité ;
- machine d’état ;
- instructions LLM ;
- validations ;
- workflows de build/test/runtime ;
- diagnostics ;
- configuration.

Un profil ne doit **pas** être réduit à un prompt différent.

L’objectif est de permettre ensuite l’ajout propre de profils tels que :

- KiCad ;
- Blender ;
- FreeCAD ;
- Godot ;
- Unreal Engine ;
- PlatformIO / Arduino ;
- autres environnements disposant d’un CLI, d’une API, d’un IPC ou d’un système de scripting.

---

# 2. Principes structurants

## 2.1 Le cœur Auto-DeepSeek reste générique

Le cœur existant conserve la responsabilité de :

- Agent Auto ;
- protocole `#Relay` ;
- sécurité et classification des actions ;
- presse-papiers ;
- navigateur LLM ;
- gestion des fenêtres ;
- intervention utilisateur ;
- `TargetSession` ;
- exécution de processus ;
- journalisation ;
- persistance des réglages ;
- Git général ;
- résultats structurés ;
- fail-safe.

Aucune logique Unity, KiCad ou Blender ne doit être ajoutée directement dans `app.py`, `protocol.py` ou les couches Win32, sauf les points d’intégration génériques nécessaires au système de profils.

## 2.2 Un profil utilise en priorité les interfaces natives

Ordre de préférence général :

1. API structurée native du logiciel ;
2. CLI natif avec sortie machine-readable ;
3. IPC / serveur local officiel ;
4. scripting officiel ;
5. commande métier ajoutée par un petit plugin contrôlé ;
6. MCP si utile ;
7. automatisation GUI ;
8. reconnaissance visuelle / coordonnées comme dernier recours.

Le profil Unity doit respecter cet ordre.

## 2.3 La GUI n’est plus l’interface principale d’un profil métier

`TargetSession` reste essentielle pour :

- tester un programme construit ;
- tester un gameplay réel ;
- observer un rendu ;
- interagir avec une fenêtre pour laquelle aucune API sémantique n’existe ;
- servir de fallback diagnostique.

Elle ne doit pas devenir le mécanisme principal de création d’une scène Unity ou de modification d’un composant.

## 2.4 Le profil ne contourne jamais la sécurité du cœur

Un profil peut réduire les ambiguïtés d’une action, mais il ne peut pas :

- désactiver la protection intervention utilisateur ;
- contourner les validations sensibles ;
- exécuter arbitrairement hors du projet ;
- masquer une erreur native ;
- forcer une transition de machine d’état interdite ;
- inventer un succès absent du résultat natif.

---

# 3. Architecture cible

```text
                       LLM Web
                         │
                         ▼
                   Protocol #Relay
                         │
                         ▼
                ┌───────────────────┐
                │ Auto-DeepSeek Core│
                │                   │
                │ Agent Auto        │
                │ Security          │
                │ Window/Clipboard  │
                │ Execution         │
                │ TargetSession     │
                │ Git               │
                └─────────┬─────────┘
                          │
                          ▼
                 ┌─────────────────┐
                 │ ProfileManager  │
                 └────────┬────────┘
                          │
          ┌───────────────┼────────────────┐
          │               │                │
          ▼               ▼                ▼
   GenericProfile     UnityProfile     FutureProfile
                          │
             ┌────────────┼──────────────┐
             │            │              │
             ▼            ▼              ▼
       Unity CLI      Pipeline       Runtime Player
       / shell        / Editor        TargetSession
```

---

# 4. Framework générique de profils

## 4.1 Arborescence souhaitée

La cible d’architecture est de l’ordre de :

```text
clipboard_agent/
    profiles/
        __init__.py
        base.py
        registry.py
        manager.py
        models.py
        generic.py

        unity/
            __init__.py
            profile.py
            discovery.py
            health.py
            cli.py
            pipeline.py
            tools.py
            state.py
            policy.py
            results.py
            runtime.py

tests/
    profiles/
        test_profile_registry.py
        test_generic_profile.py
        unity/
            test_unity_detection.py
            test_unity_health.py
            test_unity_cli.py
            test_unity_pipeline.py
            test_unity_policy.py
            test_unity_state.py
            test_unity_runtime.py
```

L’implémentation exacte pourra différer, mais les responsabilités doivent rester séparées.

## 4.2 Contrat `AgentProfile`

Une interface de profil doit exposer au minimum des opérations conceptuelles équivalentes à :

```python
class AgentProfile:
    id: str
    display_name: str
    version: str

    def detect_project(context) -> DetectionResult: ...
    def health_check(context, deep=False) -> HealthReport: ...
    def discover_capabilities(context) -> CapabilityRegistry: ...
    def build_prompt_fragment(context) -> str: ...
    def validate_tool_request(request, context) -> ValidationResult: ...
    def execute_tool(request, context, callbacks) -> ToolResult: ...
    def cancel_active_tool(context) -> None: ...
    def current_state(context) -> ProfileState: ...
    def recommended_next(context) -> tuple[str, ...]: ...
    def shutdown(context) -> None: ...
```

Cette signature est indicative ; l’exigence porte sur les responsabilités, pas sur les noms exacts.

## 4.3 Modèles communs

Le framework doit disposer de modèles typés pour :

### `DetectionResult`

- `matched` ;
- `confidence` ;
- `reasons` ;
- `project_type` ;
- `detected_version` ;
- `warnings`.

### `HealthReport`

Chaque check comporte :

- identifiant ;
- statut : `PASS | WARN | FAIL | USER_ACTION_REQUIRED` ;
- message ;
- détail machine-readable ;
- remédiation proposée ;
- booléen indiquant si la remédiation peut être exécutée automatiquement ;
- niveau de risque.

### `ToolDescriptor`

- identifiant stable ;
- provider ;
- description courte ;
- schéma des arguments ;
- schéma du résultat ;
- timeout par défaut ;
- risque ;
- états depuis lesquels l’outil est autorisé ;
- nature : lecture / modification / test / build / runtime / installation ;
- disponibilité détectée à l’exécution.

### `ToolRequest`

- ID Relay ;
- profil ;
- provider ;
- tool ;
- arguments structurés ;
- timeout ;
- métadonnées de contexte.

### `ToolResult`

- ID ;
- profil ;
- provider ;
- tool ;
- statut ;
- état profil avant/après ;
- exit code natif si applicable ;
- stdout/stderr bornés ;
- données structurées ;
- warnings ;
- erreurs ;
- artifacts ;
- `RecommendedNext`.

## 4.4 `ProfileRegistry`

Le registre doit :

- enregistrer les profils disponibles ;
- refuser les doublons d’ID ;
- permettre de lister leurs métadonnées sans les initialiser ;
- permettre une détection projet ;
- ne pas importer de dépendances lourdes au démarrage ;
- isoler un profil défaillant du profil générique.

## 4.5 `ProfileManager`

Le manager doit :

- mémoriser le profil actif ;
- interdire un changement de profil pendant une action ou session incompatible ;
- exécuter le lifecycle du profil ;
- exposer état, health, capacités et recommandations au cœur ;
- router les actions `TOOL` ;
- garantir qu’un outil n’est exécuté que par le profil actif ;
- restaurer proprement `GenericProfile` en cas de fermeture du projet.

---

# 5. Profil générique

## 5.1 But

Le premier travail d’intégration consiste à encapsuler le comportement V2.14 actuel dans `GenericProfile`.

## 5.2 Exigence de non-régression

Avant toute logique Unity, `GenericProfile` doit produire le même comportement fonctionnel que V2.14 :

- `EXECUTION` ;
- `OPEN_TEST_SESSION` ;
- `TEST_ACTIONS` ;
- `CLOSE_TEST_SESSION` ;
- `TEMP_TEST` ;
- `SHOW` ;
- `END` ;
- sécurité ;
- timings ;
- presse-papiers ;
- workspaces ;
- TargetSession ;
- protocole strict une directive par réponse.

Le refactoring vers les profils n’est validé que si la suite de tests existante reste verte sans assouplissement.

---

# 6. Sélection du profil

## 6.1 UI

Ajouter un sélecteur visible :

```text
Profil :
[ Développement général ▼ ]

Développement général
Unity
...
```

L’interface doit également afficher :

- état du profil ;
- projet détecté ;
- version détectée ;
- health global ;
- bouton `Vérifier le profil` ;
- bouton `Voir capacités` ;
- bouton de setup lorsque nécessaire.

## 6.2 Sélection manuelle par défaut

Pour la première version :

- le profil est choisi manuellement par l’utilisateur ;
- Auto-DeepSeek peut suggérer un profil détecté ;
- il ne change jamais silencieusement de profil.

Exemple :

```text
Projet Unity détecté (Unity 6000.x).
Profil actuel : Développement général.
[ Passer au profil Unity ]
```

## 6.3 Détection automatique indicative

Le détecteur Unity recherchera notamment :

- `ProjectSettings/ProjectVersion.txt` ;
- `Assets/` ;
- `Packages/` ;
- `Packages/manifest.json`.

La présence de fichiers partiels ne suffit pas à modifier le profil actif.

---

# 7. Persistance des profils

## 7.1 Réglages utilisateur

Les réglages globaux doivent mémoriser :

- dernier profil utilisé ;
- options de profil ;
- chemin Unity CLI détecté si nécessaire ;
- préférences d’utilisation du transport persistant ;
- autorisations optionnelles.

Les secrets ne doivent pas être stockés dans le JSON de réglages Auto-DeepSeek.

## 7.2 Configuration projet

Une configuration projet propre à Auto-DeepSeek peut être ajoutée ultérieurement, par exemple :

```text
.autodeepseek/
    profile.json
```

Elle doit être :

- optionnelle ;
- versionnable ;
- sans secret ;
- lisible par l’humain ;
- indépendante de la configuration utilisateur locale.

Le choix exact du fichier est reporté à l’implémentation.

---

# 8. Extension du protocole Relay

## 8.1 Nouvelle action générique

Ajouter une action canonique générique :

```text
#Relay
Protocol: 2
Action: TOOL
ID: unity-tool-01
Profile: unity
Provider: unity
Tool: status

{
  "project_path": "."
}
```

Le payload doit être JSON lorsque des arguments structurés sont nécessaires.

## 8.2 Règles

- une seule directive `#Relay` par message reste obligatoire ;
- `Profile`, `Provider` et `Tool` doivent correspondre au registre courant ;
- aucun outil non découvert/non autorisé ne peut être appelé ;
- le schéma des arguments doit être validé avant exécution ;
- le LLM ne fournit jamais un executable path arbitraire à un provider ;
- le profil actif doit correspondre à `Profile` ;
- le cœur conserve l’ID anti-doublon ;
- les règles de session existantes restent prioritaires.

## 8.3 Résultat

Format cible :

```text
#RelayResult
Protocol: 2
Kind: TOOL
ID: unity-tool-01
Status: SUCCESS
Profile: unity
Provider: unity
Tool: status
ProfileState: READY_EDIT
RecommendedNext: TOOL,EXECUTION

DATA:
{...}
```

Les blobs volumineux ne sont pas injectés intégralement dans le chat. Ils sont résumés, bornés ou transmis comme artifact lorsque l’infrastructure le permet.

---

# 9. Composition du prompt par profil

Le prompt initial devient :

```text
CoreInstructions
    +
SelectedProfileInstructions
    +
DetectedCapabilitiesSummary
    +
ProjectHealthSummary
```

## 9.1 Le cœur reste propriétaire des règles absolues

Les règles suivantes ne peuvent pas être redéfinies par un profil :

- une seule directive copiable ;
- sécurité ;
- intervention utilisateur ;
- ID unique ;
- respect de `RecommendedNext` ;
- non-invention de résultats ;
- comportement fail-safe.

## 9.2 Le profil ajoute uniquement

- règles métier ;
- outils disponibles ;
- ordre de préférence des outils ;
- anti-patterns ;
- workflow de validation ;
- état métier ;
- conventions de fichiers.

## 9.3 Prompt dynamique et borné

Ne pas injecter des manifestes complets de plusieurs milliers d’entrées.

Le profil fournit :

- un résumé des capacités essentielles ;
- les outils pertinents dans l’état courant ;
- un moyen de demander le catalogue détaillé.

---

# 10. UnityProfile — objectifs

UnityProfile doit permettre à l’agent de développer et valider un projet Unity avec le moins possible d’automatisation GUI.

Il doit couvrir progressivement :

- diagnostic environnement ;
- détection/version du projet ;
- ouverture/fermeture Editor ;
- connexion à Unity Pipeline ;
- découverte des commandes ;
- interrogation/modification de l’Editor ;
- compilation ;
- tests EditMode et PlayMode ;
- build ;
- lancement d’un Player ;
- test gameplay via TargetSession ;
- diagnostics Git/Unity ;
- collecte de logs ;
- retour structuré au LLM.

---

# 11. Hypothèses et contraintes Unity

## 11.1 Unity CLI

Au moment de ce CDC, Unity CLI est officiel mais encore marqué **expérimental** par Unity.

Conséquence impérative :

> Auto-DeepSeek ne doit pas coder en dur l’intégralité de la surface Unity CLI comme si elle était stable.

Le profil doit découvrir la version et les capacités réellement installées.

## 11.2 Unity Pipeline

Le package Unity Pipeline permet de contrôler un Editor local via une API locale et les commandes Unity CLI.

Prérequis officiel actuel :

- Unity Editor 6.0 ou supérieur ;
- Unity CLI ;
- package Pipeline installé dans le projet.

Les anciennes versions de Unity restent hors périmètre du profil Unity v1, sauf dégradation explicite vers les fonctions génériques.

## 11.3 Aucun serveur Unity propriétaire en v1

Ne pas développer un serveur TCP/HTTP Auto-DeepSeek parallèle à Unity Pipeline.

Si des primitives manquent, préférer :

1. commande Pipeline native ;
2. `unity eval` pour exploration ponctuelle ;
3. petite commande C# enregistrée dans un package Auto-DeepSeek ;
4. GUI seulement en dernier recours.

---

# 12. Providers Unity

## 12.1 `UnityCliProvider`

Responsabilités :

- trouver `unity` ;
- récupérer la version ;
- exécuter en mode non interactif ;
- demander `--format json` ou `--format ndjson` lorsque disponible ;
- séparer stdout et stderr ;
- mapper les exit codes ;
- supporter cancellation et timeout ;
- redacter les données sensibles ;
- découvrir la surface avec `unity commands --format json`.

Le provider ne doit pas parser les sorties humaines lorsque l’équivalent machine-readable existe.

## 12.2 `UnityProjectProvider`

Responsabilités :

- détection du projet ;
- `unity projects info` si disponible ;
- version Editor attendue ;
- modules requis ;
- configuration `ProjectSettings/UnityCliConfig.json` ;
- incohérences projet/Editor ;
- répertoires générés.

## 12.3 `UnityPipelineProvider`

Responsabilités :

- vérifier l’installation Pipeline ;
- détecter les Editors connectés ;
- sélectionner l’Editor correspondant exactement au projet ;
- exécuter `unity status` ;
- découvrir les outils via `unity list --format json` ;
- exécuter `unity command` ;
- gérer les jobs détachés via `unity job` lorsqu’ils sont disponibles.

Un Editor d’un autre projet ne doit jamais être choisi par défaut.

## 12.4 `UnityEvalProvider`

`unity eval` est un outil avancé.

Politique v1 :

- lecture/inspection simple : autorisable ;
- expression modifiant l’Editor : `SENSITIVE` par défaut ;
- code multi-ligne ou effets non déterminables : refus ou validation humaine ;
- ne pas utiliser `eval` comme substitut permanent d’un outil métier récurrent.

Toute opération `eval` répétée doit être candidate à devenir une commande typée.

## 12.5 `UnityTestProvider`

Responsabilités :

- lancer EditMode ;
- lancer PlayMode ;
- filtre de tests ;
- timeout ;
- rapports structurés ;
- coverage lorsque demandé ;
- tests affectés si supportés ;
- distinguer échec des tests et échec d’infrastructure.

Mapping minimum actuel des codes Unity CLI :

- `0` : succès ;
- `6` : opération/test sans verdict valide / infrastructure ;
- `7` : service inaccessible et potentiellement retryable ;
- `8` : tests réellement exécutés avec au moins un échec ;
- `130` : interruption utilisateur ;
- `143` : terminaison externe.

Le profil ne doit jamais traiter `8` comme une panne d’infrastructure.

## 12.6 `UnityBuildProvider`

Responsabilités :

- lister targets ;
- lister Build Profiles ;
- lancer un build ;
- obtenir le chemin de sortie réel ;
- collecter logs/erreurs ;
- ne pas considérer le build réussi uniquement parce que le processus Editor a existé ;
- supporter `unity build run` lorsque pertinent.

Les secrets de signature ne doivent pas être envoyés en clair dans le prompt ou les logs.

## 12.7 `UnityVcsProvider`

Le profil peut utiliser les commandes `unity vcs` disponibles pour obtenir une vision sémantique Unity du dépôt.

Le Git actuel d’Auto-DeepSeek reste l’autorité pour :

- commits ;
- branches ;
- push ;
- historique général.

Le provider Unity VCS sert notamment pour :

- diagnostics .meta ;
- diffs sémantiques scènes/prefabs si disponibles ;
- assets affectés ;
- diagnostics repository Unity.

Il ne doit pas remplacer automatiquement Git par Unity Version Control.

## 12.8 `UnityRuntimeProvider`

Une fois le Player construit :

1. récupérer le chemin exact du build ;
2. lancer le Player ;
3. identifier son HWND/processus ;
4. ouvrir une TestSession persistante ;
5. effectuer les tests gameplay ;
6. capturer les observations ;
7. fermer ou rendre la main.

Le RuntimeProvider réutilise les mécanismes robustes déjà présents :

- HWND appartenant au processus ;
- layout clavier de la Target ;
- Unicode ;
- capture noire + fallback ;
- intervention utilisateur ;
- Z-order ;
- retries ;
- logs.

---

# 13. Transport Unity CLI

## 13.1 Transport de référence

Le provider doit proposer une abstraction de transport :

```text
UnityCliTransport
    ├── OneShotTransport
    └── ShellNdjsonTransport
```

## 13.2 `OneShotTransport`

Chaque commande lance un processus `unity` distinct.

Avantages :

- simple ;
- traçable ;
- robuste ;
- isolation forte ;
- facile à tester.

Il constitue le fallback obligatoire.

## 13.3 `ShellNdjsonTransport`

Si la version découverte expose `unity shell --protocol ndjson`, le profil peut conserver un processus chaud pour réduire les coûts de startup et exécuter plusieurs commandes.

Exigences :

- framing NDJSON strict ;
- ID de requête local ;
- une réponse terminale par requête ;
- timeout ;
- stderr séparé ;
- détection du crash du shell ;
- redémarrage contrôlé ;
- aucun replay automatique d’une commande de modification après crash ;
- fallback OneShot.

En v1, le transport persistant peut rester désactivé par défaut jusqu’à validation physique suffisante.

---

# 14. Découverte dynamique des capacités Unity

Au démarrage du profil :

```text
unity --version
        ↓
unity commands --format json
        ↓
project detection
        ↓
unity projects info ...
        ↓
unity status --format json
        ↓
unity pipeline list
        ↓
unity list --format json
        ↓
CapabilityRegistry
```

Le profil doit conserver :

- version CLI ;
- version Editor ;
- version Pipeline ;
- manifest des commandes ;
- outils Pipeline disponibles ;
- hash/version du catalogue ;
- timestamp de découverte.

Si Unity CLI change de syntaxe :

- le profil doit échouer explicitement ;
- il ne doit pas essayer des commandes historiques au hasard ;
- il peut dégrader certaines fonctions ;
- il doit proposer une remédiation.

---

# 15. Skill Unity et documentation dynamique

Lorsque la version installée le permet, le profil peut consulter le skill Unity CLI correspondant à la version courante.

Cependant :

- le texte externe ne doit jamais remplacer les règles de sécurité Auto-DeepSeek ;
- il ne doit pas être injecté intégralement sans borne ;
- il doit être considéré comme documentation/version-matched ;
- le profil doit préférer les schémas machine-readable à des instructions prose lorsqu’ils existent.

---

# 16. Health check Unity

## 16.1 Health rapide

Doit vérifier rapidement :

- Unity CLI trouvé ;
- version CLI ;
- projet Unity valide ;
- Editor demandé installé ou identifiable ;
- licence/auth état connu ;
- Editor connecté si nécessaire ;
- Pipeline détecté ;
- outils essentiels découverts.

## 16.2 Health profond

Peut inclure :

- `unity doctor --ci` si disponible ;
- espace disque ;
- modules de build requis ;
- diagnostics Pipeline ;
- diagnostics VCS ;
- test de commande non destructive ;
- test compilation/statut.

## 16.3 Installation/remédiation

Actions telles que :

- installer Unity CLI ;
- installer un Editor ;
- ajouter un module ;
- installer/mettre à jour Pipeline ;
- activer/modifier une licence ;
- modifier l’authentification ;

ne doivent pas être silencieuses.

Elles doivent être classées selon leur impact et demander validation utilisateur lorsque nécessaire.

---

# 17. Machine d’état Unity

La machine d’état profil est orthogonale à `AutoState`.

États normalisés souhaités :

```text
UNINITIALIZED
    ↓
CHECKING
    ↓
READY
    ↕
EDITOR_STARTING
EDITOR_READY
COMPILING
IMPORTING
PLAY_MODE
TESTING
BUILDING
RUNTIME_TESTING
USER_ACTION_REQUIRED
DEGRADED
ERROR
```

Un état concret peut être simplifié si Unity CLI ne permet pas de le distinguer de façon fiable.

Principe :

> ne jamais inventer un état plus précis que ce que les sources natives permettent d’observer.

## 17.1 Exemples de transitions

```text
READY
  -> EDITOR_STARTING
  -> EDITOR_READY

EDITOR_READY
  -> COMPILING
  -> EDITOR_READY

EDITOR_READY
  -> TESTING
  -> EDITOR_READY

EDITOR_READY
  -> BUILDING
  -> READY

READY
  -> RUNTIME_TESTING
  -> READY
```

Les opérations incompatibles sont refusées pendant `BUILDING`, `TESTING`, `COMPILING` ou `RUNTIME_TESTING`, sauf outils explicitement read-only compatibles.

---

# 18. Readiness Unity

Le profil doit bannir les délais arbitraires comme mécanisme principal.

Ordre de preuve de readiness :

1. état Unity CLI/Pipeline structuré ;
2. fin de job native ;
3. état Editor renvoyé par une commande ;
4. absence de compilation/import actif confirmée ;
5. TargetSession readiness visuelle uniquement pour le Player/runtime.

Un `sleep` peut servir de settle local très court, jamais de preuve fonctionnelle.

---

# 19. Workflow de développement Unity autonome

Workflow nominal :

```text
Inspect project
    ↓
Health Unity
    ↓
Discover capabilities
    ↓
Inspect scene/assets/code
    ↓
Modify source / issue Editor command
    ↓
Wait native readiness
    ↓
Collect compile diagnostics
    ↓
Fix if necessary
    ↓
Run affected/targeted tests
    ↓
Run broader EditMode/PlayMode tests
    ↓
Build Player
    ↓
Launch Player
    ↓
TargetSession gameplay test
    ↓
Collect logs + screenshots
    ↓
Fix
    ↓
Repeat
    ↓
Final full validation
    ↓
Git commit/push
```

---

# 20. Règles de modification d’un projet Unity

## 20.1 Répertoires

Le profil doit connaître la différence entre source et généré.

Ne pas versionner ou modifier comme source métier :

- `Library/` ;
- `Temp/` ;
- `Logs/` ;
- `obj/` ;
- répertoires de build générés configurés.

## 20.2 Assets et `.meta`

Toute opération sur `Assets/` doit respecter les GUID Unity.

Préférence :

1. outil Unity/Pipeline/AssetDatabase ;
2. opération fichier qui déplace systématiquement asset + `.meta` ;
3. refus si la conservation des références n’est pas garantie.

## 20.3 Scènes et Prefabs

Par défaut :

- préférer les commandes Editor/Pipeline ;
- éviter l’édition YAML brute ;
- ne jamais réécrire massivement une scène/prefab comme texte sans nécessité documentée ;
- vérifier sauvegarde et compilation après modification.

## 20.4 ProjectSettings

Les changements doivent être :

- ciblés ;
- diffables ;
- validés ;
- compatibles avec la version Editor du projet.

---

# 21. `ProjectSettings/UnityCliConfig.json`

Lorsque la version CLI installée le supporte, le profil peut utiliser le fichier natif Unity pour rendre les builds/tests reproductibles.

Exigences :

- fichier versionnable ;
- pas de secret ;
- modification seulement avec diff explicite ;
- valeurs cohérentes avec le projet ;
- ne pas imposer une target sans demande du projet.

Le profil doit pouvoir expliquer la valeur résolue d’un réglage lorsque Unity expose un mécanisme de résolution.

---

# 22. Outils Unity minimaux requis pour le MVP

Le MVP Unity n’est validé que s’il peut fournir, via outils natifs ou commandes typées :

## Inspection

- état Editor ;
- version projet/Editor ;
- scènes connues/active ;
- GameObjects principaux ;
- erreurs de compilation ;
- logs utiles ;
- capacités installées.

## Édition

Au minimum :

- ouvrir/créer/sauvegarder une scène ;
- créer/supprimer/renommer un GameObject ;
- parentage ;
- transform ;
- ajouter/configurer un composant courant ;
- créer/instancier un prefab ou fournir une alternative native propre.

Les noms exacts des commandes ne doivent pas être codés en dur si la découverte native fournit le schéma.

## Validation

- compilation ;
- EditMode tests ;
- PlayMode tests ;
- build desktop ;
- lancement du Player ;
- test TargetSession.

Si une primitive essentielle n’est pas disponible nativement, elle devient candidate à une commande C# Auto-DeepSeek.

---

# 23. Package Unity Auto-DeepSeek optionnel

Créer un package Unity propriétaire uniquement si nécessaire.

Objectif :

- compléter les lacunes de Unity Pipeline ;
- exposer des commandes typées ;
- ne pas créer un deuxième serveur.

Exemple :

```text
Packages/
    com.autodeepseek.unity/
        Editor/
            Commands/
                SceneCommands.cs
                GameObjectCommands.cs
                DiagnosticsCommands.cs
```

Les commandes doivent :

- utiliser l’API Unity officielle ;
- valider leurs arguments ;
- être idempotentes lorsque possible ;
- retourner un résultat structuré ;
- supporter Undo lorsque pertinent ;
- sauvegarder explicitement lorsque l’opération l’exige ;
- ne pas cacher les exceptions.

---

# 24. MCP Unity

Le CLI Unity dispose d’une intégration MCP.

Politique pour UnityProfile v1 :

- **non requis pour le chemin principal** ;
- optionnel ;
- activable comme provider complémentaire ;
- utile si certaines capacités sont disponibles uniquement par ce canal ;
- ne doit pas être nécessaire à l’autonomie basique.

Le chemin préféré reste Unity CLI + Pipeline, car Auto-DeepSeek sait déjà orchestrer des processus locaux et exploiter des sorties structurées.

---

# 25. Sécurité Unity

## 25.1 Catégories

### LOW

- status ;
- list ;
- inspect ;
- logs bornés ;
- project info ;
- read-only VCS.

### MODIFY

- création/modification de scène ;
- modification GameObject/component ;
- fichiers source dans le projet ;
- création de tests ;
- configuration non sensible.

### SENSITIVE

- `eval` à effet non borné ;
- installation de package ;
- installation/upgrade Pipeline ;
- upgrade de projet Unity ;
- changement Editor majeur ;
- opérations VCS destructives ;
- build/signing avec credentials ;
- cloud linking ;
- actions licence/auth.

### BLOCKED ou validation forte

- exécution de code arbitraire hors projet ;
- suppression massive ;
- écriture dans installation Unity ;
- extraction/exposition de secrets ;
- contournement de licence ;
- commande non découverte avec effets inconnus.

## 25.2 Auth et licence

Auto-DeepSeek ne doit jamais :

- enregistrer un token Unity dans ses logs ;
- recopier un secret dans le chat ;
- modifier une licence silencieusement ;
- effectuer un login interactif sans prévenir l’utilisateur.

---

# 26. Gestion des erreurs Unity CLI

Le provider doit distinguer :

- erreur de syntaxe ;
- configuration manquante ;
- auth/licence ;
- service temporairement inaccessible ;
- échec opérationnel ;
- test échoué ;
- compilation échouée ;
- Editor crash ;
- timeout ;
- interruption utilisateur.

Un `stderr` non vide ne signifie pas systématiquement échec si le code natif et le résultat structuré indiquent le contraire.

Inversement, absence de texte d’erreur ne signifie pas succès.

---

# 27. Retry

Retry automatique uniquement sur erreurs explicitement transitoires.

Exemples possibles :

- service temporairement inaccessible ;
- Editor encore `starting` ;
- connexion Pipeline en cours d’établissement.

Ne jamais retry automatiquement :

- tests échoués ;
- compile error déterministe ;
- argument invalide ;
- action refusée ;
- modification partiellement exécutée dont l’idempotence n’est pas prouvée.

---

# 28. Logs et artifacts

Chaque opération Unity doit pouvoir produire :

- commande logique ;
- provider ;
- durée ;
- exit code ;
- stdout/stderr bornés ;
- données JSON ;
- log Editor pertinent ;
- rapport tests ;
- rapport coverage ;
- path build ;
- screenshot/runtime observation.

Les fichiers volumineux restent sur disque et sont référencés, pas injectés intégralement dans le chat.

---

# 29. Tests du framework de profils

## 29.1 Tests unitaires

- registre ;
- sélection ;
- détection ;
- changement de profil ;
- sérialisation settings ;
- lifecycle ;
- routage TOOL ;
- validation schema ;
- erreurs provider ;
- cancellation ;
- recommandations.

## 29.2 Tests de non-régression GenericProfile

Toute la suite V2.14 actuelle doit rester verte.

## 29.3 Simulation provider

Les tests CI standards ne doivent pas nécessiter Unity installé.

Créer des fakes de :

- Unity CLI ;
- Pipeline ;
- shell NDJSON ;
- status ;
- build ;
- test ;
- Editor jobs.

---

# 30. Tests UnityProfile

## Scénario U01 — Détection

Projet Unity valide → profil détecté avec version.

## U02 — Faux positif

Dossier avec seulement `Assets/` → pas de sélection automatique.

## U03 — CLI absent

Health explicite `FAIL`, remédiation proposée, aucun crash cœur.

## U04 — Version expérimentale inconnue

Capabilities découvertes dynamiquement ; fonctions absentes marquées indisponibles.

## U05 — Pipeline absent

Le profil reste utilisable pour commandes CLI non-Editor ; commandes Editor refusées avec remédiation.

## U06 — Plusieurs Editors

Sélection stricte par project path ; aucune commande envoyée à un autre projet.

## U07 — Compilation

Modification C# → compilation → erreurs structurées ou état ready.

## U08 — Test réussi

EditMode/PlayMode → statut success.

## U09 — Test échoué

Exit code de test → `TEST_FAILURE`, pas `INFRA_FAILURE`.

## U10 — Infrastructure test cassée

Compile/licence/crash/timeout → pas de faux « tests échoués ».

## U11 — Build

Build desktop → chemin artifact confirmé.

## U12 — Runtime

Player lancé → HWND associé → TargetSession → entrée clavier → Observe → close.

## U13 — Intervention utilisateur

Mouvement physique → annulation de l’action UI/runtime selon les garanties V2.14.

## U14 — Shell NDJSON crash

Aucune commande de modification n’est rejouée ; fallback sûr ou erreur explicite.

## U15 — `.meta`

Déplacement asset → GUID conservé ou opération refusée.

## U16 — Profile isolation

Un outil Unity ne peut pas s’exécuter lorsque GenericProfile est actif.

---

# 31. Tests d’intégration réels

Prévoir ultérieurement un runner Windows dédié avec Unity installé.

Matrice minimale physique :

- Unity 6 LTS / version supportée ;
- Unity CLI supportée ;
- Pipeline installée ;
- projet fixture Auto-DeepSeek Unity.

Tests :

1. health ;
2. Editor open ;
3. Pipeline connect ;
4. commande read-only ;
5. création scène fixture ;
6. compilation ;
7. EditMode ;
8. PlayMode ;
9. build Windows ;
10. Player TargetSession ;
11. cleanup.

Ces tests ne doivent pas devenir obligatoires pour chaque commit générique si leur coût est trop élevé ; ils peuvent être nightly/manual/release-gated.

---

# 32. UI UnityProfile

Panneau suggéré :

```text
Profil              Unity
Projet              C:\...\MyGame
Unity Editor         6000.x
Unity CLI            1.0.0-beta.x
Pipeline             Installed / Connected
Editor State         READY
Licence              OK
Health               7 PASS / 1 WARN

[ Vérifier ] [ Capacités ] [ Ouvrir Editor ] [ Setup Pipeline ]
```

Le panneau ne doit pas surcharger l’UI principale.

Les détails avancés restent dans une vue dédiée.

---

# 33. UX de setup Unity

Première activation :

1. détecter projet ;
2. vérifier Unity CLI ;
3. vérifier Editor ;
4. vérifier auth/licence ;
5. vérifier Pipeline ;
6. afficher les problèmes ;
7. proposer les remédiations une par une ;
8. demander confirmation pour toute installation/modification externe ;
9. refaire health ;
10. autoriser Agent Auto uniquement si le minimum requis est sain.

---

# 34. Interaction avec Agent Auto

Agent Auto ne doit pas exécuter un outil Unity s’il manque un prérequis critique.

Exemple :

```text
ProfileState: USER_ACTION_REQUIRED
RecommendedNext: PROFILE_HEALTH
Reason: Unity Pipeline absent
```

Le LLM ne doit pas compenser par des clics GUI arbitraires.

---

# 35. Git et commits

Le workflow projet reste :

```text
Understand
→ Plan
→ Code
→ Test
→ Save
→ Integrate
→ Validate
→ Deliver
```

UnityProfile ajoute des validations, mais ne remplace pas cette discipline.

Avant commit Unity :

- compilation propre ;
- tests ciblés ;
- validation `.meta` ;
- pas de fichiers générés indésirables ;
- diff raisonnable ;
- build/test plus large aux étapes cohérentes.

---

# 36. Phasage d’implémentation

## Phase P0 — Refactoring profils sans changement fonctionnel

- créer modèles/base/registry/manager ;
- créer `GenericProfile` ;
- sélecteur UI ;
- persistance ;
- déplacer progressivement les décisions profile-aware ;
- aucune logique Unity.

**Gate :** toute la suite V2.14 verte.

## Phase P1 — Protocole TOOL

- modèles Tool ;
- parser strict ;
- result `Kind: TOOL` ;
- schema validation ;
- routing ProfileManager ;
- outils fake de test.

**Gate :** aucune régression V2 ; TOOL inaccessible si non supporté.

## Phase U1 — Discovery et Health Unity

- détection projet ;
- Unity CLI version ;
- manifest commands ;
- project info ;
- status ;
- Pipeline status ;
- health UI.

**Gate :** diagnostic fiable sans modifier le projet.

## Phase U2 — Unity CLI provider

- OneShot JSON/NDJSON ;
- exit codes ;
- timeouts ;
- cancellation ;
- redaction ;
- capabilities.

## Phase U3 — Pipeline / Editor

- discovery tools ;
- command ;
- jobs ;
- eval read-only ;
- state normalization ;
- sélection stricte project path.

## Phase U4 — Tests

- EditMode ;
- PlayMode ;
- filtres ;
- rapports ;
- distinction test failure / infra failure.

## Phase U5 — Build

- targets/profiles ;
- config projet ;
- build Windows ;
- artifact ;
- logs.

## Phase U6 — Runtime

- lancement Player ;
- TargetSession ;
- test gameplay ;
- observations ;
- cleanup.

## Phase U7 — Shell NDJSON

- session persistante ;
- restart/fallback ;
- soak tests ;
- activation par défaut seulement après validation physique.

## Phase U8 — Commandes Auto-DeepSeek Unity optionnelles

Seulement si des lacunes natives restent démontrées.

---

# 37. Hors périmètre Unity v1

- génération 3D avancée type Blender ;
- création autonome d’assets artistiques complets ;
- publication stores ;
- signing mobile automatique avec secrets ;
- cloud deployment ;
- changement automatique de licence ;
- mise à niveau majeure du projet sans validation ;
- multi-agent concurrent sur le même Editor ;
- support complet des anciennes versions Unity pré-6 ;
- édition brute généralisée des YAML scene/prefab ;
- dépendance obligatoire à MCP ;
- automatisation GUI de l’Editor comme chemin nominal.

---

# 38. Critères de validation du framework profils

Le framework est accepté si :

- GenericProfile reproduit V2.14 ;
- un profil est sélectionnable sans redémarrer l’application hors action active ;
- les capacités sont isolées par profil ;
- TOOL est validé et routé de façon déterministe ;
- un profil défaillant ne casse pas le cœur ;
- health et state sont visibles ;
- les instructions profil sont composées sans remplacer les règles absolues ;
- la suite CI Ubuntu/Windows Python 3.11/3.12 reste verte.

---

# 39. Critères de validation UnityProfile v1

UnityProfile v1 est accepté si un agent peut, sur un projet fixture réel :

1. détecter le projet Unity ;
2. identifier la version Editor ;
3. diagnostiquer Unity CLI et Pipeline ;
4. connecter le bon Editor ;
5. découvrir les commandes réellement disponibles ;
6. inspecter le projet sans GUI ;
7. modifier une scène simple par API/commande native ;
8. provoquer et détecter une compilation ;
9. récupérer une compile error réelle ;
10. corriger puis obtenir compilation propre ;
11. lancer EditMode ;
12. lancer PlayMode ;
13. distinguer test failure et infrastructure failure ;
14. construire un Player Windows ;
15. lancer ce Player ;
16. l’ouvrir en TargetSession ;
17. effectuer clavier/souris/Observe ;
18. fermer proprement ;
19. produire un état final et des résultats structurés ;
20. laisser le dépôt sans fichiers générés accidentels.

---

# 40. Définition de « développement Unity complètement autonome »

Pour ce projet, « complètement autonome » signifie :

- l’agent peut accomplir la boucle technique complète sans intervention humaine nominale ;
- les interventions humaines restent possibles et prioritaires ;
- certaines actions sensibles nécessitent volontairement confirmation ;
- l’autonomie ne signifie pas contournement des licences, authentifications ou validations ;
- l’agent s’appuie sur l’état réel du logiciel, pas sur des délais devinés.

La boucle cible est :

```text
analyse
→ modification
→ compilation
→ tests
→ build
→ exécution
→ gameplay/observation
→ diagnostic
→ correction
→ validation
→ commit
```

---

# 41. Décisions d’architecture à ne pas remettre en cause sans justification

1. **Les profils sont des adaptateurs métier, pas des prompts.**
2. **GenericProfile est le comportement actuel encapsulé.**
3. **Le cœur ne contient pas de logique Unity spécifique.**
4. **Unity CLI/Pipeline est le chemin nominal Unity.**
5. **Les capacités Unity sont découvertes dynamiquement.**
6. **TargetSession reste le mécanisme de runtime/GUI fallback.**
7. **MCP est optionnel, pas une dépendance fondamentale.**
8. **Pas de serveur Unity parallèle en v1.**
9. **Pas d’édition YAML scene/prefab par défaut.**
10. **Aucun install/upgrade/auth/licence silencieux.**
11. **Les résultats natifs structurés priment sur l’interprétation visuelle.**
12. **Aucune régression du profil générique n’est acceptable.**

---

# 42. Risques principaux

## Unity CLI expérimental

**Risque :** breaking changes.

**Réponse :**
- discovery ;
- versioning ;
- manifest machine-readable ;
- providers isolés ;
- tests contractuels ;
- fallback explicite.

## Pipeline indisponible

**Risque :** impossibilité de contrôler l’Editor sémantiquement.

**Réponse :**
- health ;
- setup guidé ;
- fonctions CLI batch encore disponibles ;
- ne pas basculer silencieusement vers GUI.

## État Editor complexe

**Risque :** action pendant compile/import/build.

**Réponse :**
- state machine profil ;
- jobs natifs ;
- status ;
- readiness sémantique.

## Projet corrompu par asset operations

**Risque :** GUID/`.meta` cassés.

**Réponse :**
- API Unity d’abord ;
- règles asset ;
- validation VCS ;
- commits petits.

## Prompt trop gros

**Risque :** baisse de fiabilité LLM.

**Réponse :**
- capacités résumées ;
- catalogue à la demande ;
- discovery côté Relay ;
- ne pas coller l’intégralité de la documentation Unity.

---

# 43. Références officielles de conception

Références consultées lors de la rédaction de ce CDC :

- Unity CLI — introduction et statut expérimental :  
  https://docs.unity.com/en-us/unity-cli

- Unity CLI — référence commandes, formats structurés et exit codes :  
  https://docs.unity.com/ja-jp/unity-cli/unity-cli-reference

- Unity CLI — release notes, notamment `commands --format json`, `shell --protocol ndjson`, tests, builds, jobs et skill :  
  https://docs.unity.com/en-us/unity-cli/release-notes

- Unity Pipeline package — contrôle local de l’Editor via API et commandes :  
  https://docs.unity.com/en-us/unity-production-pipeline/local-tools-cli/unity-pipeline-package

- Comparaison Unity CLI / Pipeline :  
  https://docs.unity.com/zh-cn/unity-production-pipeline/local-tools-cli/unity-cli-pipeline-package

- Unity Production Pipeline / local tools :  
  https://docs.unity.com/en-us/unity-production-pipeline/local-tools-cli

Ces URLs sont informatives. L’implémentation doit continuer à découvrir les capacités de la version Unity CLI réellement installée et ne pas considérer une documentation beta comme un contrat immuable.

---

# 44. Definition of Done de la mission d’implémentation future

La future mission « Profils + Unity » pourra être déclarée terminée uniquement lorsque :

- le framework de profils est intégré ;
- GenericProfile est sans régression ;
- UnityProfile respecte les gates P0 → U6 au minimum ;
- les tests unitaires sont complets ;
- la matrice CI standard est verte ;
- un test Windows physique Unity réel est documenté ;
- compilation/tests/build/runtime ont été exécutés réellement ;
- aucune étape critique n’est simulée dans le rapport final ;
- les limites restantes sont explicites ;
- README, CDC global et machine d’état sont synchronisés avec l’implémentation.
