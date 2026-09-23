# Mission — UnityProfile core : compilation, vision et séparation des profils

**Branche :** `feat/unity-profile-core-v2`  
**Base :** `main` @ `274a761`

## Objectif

Commencer l'implémentation réelle des CDC Unity sans réintroduire de logique métier dans `app.py`.

Cette mission couvre quatre briques :

1. **Séparation propre des profils** : renforcer le contrat `AgentProfile` avec des outils typés et un routage par profil.
2. **UnityProfile minimal fonctionnel** : détection de projet, health check CLI, état métier et prompt spécialisé.
3. **Compilation Unity** : fournir un coordinateur dédié qui lance `unity recompile` en sortie structurée et transforme le résultat en diagnostic typé.
4. **Retour visuel Unity** : introduire le contrat `VisualIntent` / `EvidenceBundle` / `VisualConfidence`, un `UnityVisualRouter` indépendant des backends et un premier fallback réel de capture Editor sous Windows.

## Principes

- Le cœur Relay reste générique.
- Aucune logique Unity nouvelle ne doit être ajoutée directement à `clipboard_agent/app.py`.
- Les modules Unity résident sous `clipboard_agent/profiles/unity/`.
- Les interactions réelles avec Unity CLI sont encapsulées derrière une interface testable.
- La CI standard ne requiert pas Unity installé : les providers sont testés avec fakes.
- Les commandes machine-readable utilisent JSON/NDJSON lorsqu'ils sont disponibles.
- La compilation doit être interprétée comme une opération métier, pas comme une simple commande shell opaque.
- La vision est exprimée par intention (`GAME`, `SCENE`, `EDITOR`, `RUNTIME`) ; le LLM ne choisit pas le backend.
- `profiled_app.py` conserve uniquement l'UI de profils et la réconciliation TestSession ; le pont protocole/runtime TOOL est isolé dans `profile_tool_host.py`.

## Livrables réalisés

- modèles génériques `ToolDescriptor`, `ToolRequest`, `ToolResult` ;
- extension non cassante de `AgentProfile` et routage par `ProfileManager` ;
- `UnityProfile` enregistré dans le registre par défaut ;
- détection de projet Unity et health check CLI ;
- `UnityCliRunner` injectable en sortie JSON non interactive ;
- `UnityCompileCoordinator` avec `SUCCESS`, `COMPILE_ERROR`, `INFRA_ERROR`, `TIMEOUT` ;
- extension stricte `Action: TOOL`, séparée du gros parser historique ;
- exécution asynchrone des outils afin de ne pas bloquer Tk ;
- `ProfileToolHostMixin` dédié au pont Relay/Tk : parsing TOOL, anti-duplication, runtime asynchrone, résultat et jointure image ;
- résultat canonique `#RelayResult / Kind: TOOL` ;
- modèles visuels Unity et `UnityVisualRouter` ;
- premier provider concret `UnityEditorWindowVisualProvider` : capture de la fenêtre Editor correspondant au projet, PNG artifact, confiance `MEDIUM`, sans prétendre être une preuve native ;
- jointure automatique du premier artifact image au retour Agent Auto ;
- prompt Unity isolé dans `profiles/unity/prompt.py`, limité aux intentions et outils réellement exposés ;
- tests unitaires dédiés compilation, routage, protocole TOOL, séparation structurelle et vision.

## Hors périmètre restant de cette première brique

- installation automatique de Unity CLI/Pipeline ;
- contrôle réel d'un Editor via Pipeline ;
- capture native Game View/Scene View réelle ;
- build Player et gameplay E2E ;
- shell NDJSON persistant ;
- validation physique de `unity recompile` sur la machine Unity de l'utilisateur.

Ces éléments viennent après validation de ce socle.

## Critères d'acceptation

- `GenericProfile` reste fonctionnel sans changement de comportement.
- `UnityProfile.detect_project()` reconnaît un vrai squelette Unity et évite les faux positifs évidents.
- Le health check distingue projet invalide, CLI absent et CLI disponible.
- Le coordinateur de compilation transforme correctement succès, compile error, timeout/infra error en résultat typé.
- Le Relay exécute `Action: TOOL` hors thread Tk et ne rend la main au LLM qu'après le verdict métier.
- `profiled_app.py` n'embarque pas le parser ni le runtime TOOL ; ce pont est un composant générique séparé.
- Le routeur visuel choisit le meilleur provider selon l'intention et expose provenance / confiance / fallback.
- `EDITOR` dispose d'un vrai fallback Windows produisant un artifact PNG ; le provider refuse de deviner un autre projet Unity.
- Aucun backend visuel concret n'est codé dans le contrat LLM-facing.
- Tests verts Ubuntu/Windows Python 3.11/3.12.

## Validation à date

- Les briques profile/tools, Unity compile et visual router sont couvertes par fakes en CI.
- La séparation `ProfileToolHostMixin` / `ProfiledClipboardAgentApp` est couverte par un test structurel et un test de chargement d'artifact image.
- Le fallback Editor Windows est couvert par un faux desktop et vérifie sélection du bon projet + écriture PNG.
- La syntaxe Unity CLI utilise les options globales machine-readable avant la commande (`unity --format json --non-interactive ...`).
- La validation physique Unity reste explicitement à faire : la CI GitHub n'a pas Unity installé et ne doit pas simuler cette preuve.
