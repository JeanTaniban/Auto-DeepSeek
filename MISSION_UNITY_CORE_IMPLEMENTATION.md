# Mission — UnityProfile core : compilation, vision et séparation des profils

**Branche :** `feat/unity-profile-core-v2`  
**Base :** `main` @ `274a761`

## Objectif

Commencer l'implémentation réelle des CDC Unity sans réintroduire de logique métier dans `app.py`.

Cette mission couvre quatre briques :

1. **Séparation propre des profils** : renforcer le contrat `AgentProfile` avec des outils typés et un routage par profil.
2. **UnityProfile minimal fonctionnel** : détection de projet, health check CLI, état métier et prompt spécialisé.
3. **Compilation Unity** : fournir un coordinateur dédié qui lance `unity recompile` en sortie structurée et transforme le résultat en diagnostic typé.
4. **Retour visuel Unity** : introduire le contrat `VisualIntent` / `EvidenceBundle` / `VisualConfidence` et un `UnityVisualRouter` indépendant des backends concrets.

## Principes

- Le cœur Relay reste générique.
- Aucune logique Unity nouvelle ne doit être ajoutée directement à `clipboard_agent/app.py`.
- Les modules Unity résident sous `clipboard_agent/profiles/unity/`.
- Les interactions réelles avec Unity CLI sont encapsulées derrière une interface testable.
- La CI standard ne requiert pas Unity installé : les providers sont testés avec fakes.
- Les commandes machine-readable utilisent JSON/NDJSON lorsqu'ils sont disponibles.
- La compilation doit être interprétée comme une opération métier, pas comme une simple commande shell opaque.
- La vision est exprimée par intention (`GAME`, `SCENE`, `EDITOR`, `RUNTIME`) ; le LLM ne choisit pas le backend.

## Livrables

- modèles d'outils de profil génériques ;
- extension non cassante de `AgentProfile` ;
- `UnityProfile` enregistré dans le registre par défaut ;
- `UnityCliRunner` injectable ;
- `UnityCompileCoordinator` ;
- modèles visuels Unity et `UnityVisualRouter` ;
- tests unitaires dédiés ;
- documentation minimale des capacités réellement implémentées.

## Hors périmètre de cette première brique

- installation automatique de Unity CLI/Pipeline ;
- contrôle réel d'un Editor via Pipeline ;
- capture native Game View/Scene View réelle ;
- `Action: TOOL` complète dans le protocole Relay ;
- build Player et gameplay E2E ;
- shell NDJSON persistant.

Ces éléments viendront après validation de ce socle.

## Critères d'acceptation

- `GenericProfile` reste fonctionnel sans changement de comportement.
- `UnityProfile.detect_project()` reconnaît un vrai squelette Unity et évite les faux positifs évidents.
- Le health check distingue projet invalide, CLI absent et CLI disponible.
- Le coordinateur de compilation transforme correctement succès, compile error, timeout/infra error en résultat typé.
- Le routeur visuel choisit le meilleur provider selon l'intention et expose la provenance / confiance / fallback.
- Aucun backend visuel concret n'est codé dans le LLM-facing contract.
- Tests verts Ubuntu/Windows Python 3.11/3.12.
