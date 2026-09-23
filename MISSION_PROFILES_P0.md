# Mission P0 — Framework de profils sans régression

**Référence :** `CDC_PROFILES_UNITY.md`, phase P0  
**Base :** V2.14 / `f20fe17`  
**Branche :** `feature/profiles-p0`

## Objectif

Introduire le socle générique de profils sans ajouter de logique Unity et sans modifier le comportement fonctionnel du Relay actuel.

## Périmètre

- Créer `clipboard_agent/profiles/` avec modèles, interface de profil, registre, manager et `GenericProfile`.
- Faire de `GenericProfile` l’encapsulation du comportement actuel pour la construction du prompt initial.
- Ajouter `profile_id` aux réglages persistants avec `generic` comme valeur par défaut rétrocompatible.
- Ajouter un sélecteur de profil à l’UI ; P0 n’enregistre que `Développement général`.
- Empêcher le changement de profil pendant une commande, une TargetSession/TestSession ou Agent Auto.
- Ajouter les tests du registre, du manager, du profil générique et de la persistance.
- Conserver intégralement le protocole V2.14 et les actions existantes ; `Action: TOOL` appartient à P1 et reste hors périmètre.

## Invariants

- Aucun code Unity dans le cœur ni dans P0.
- Aucun changement du texte du prompt générique hors composition via `GenericProfile`.
- Aucun changement de sécurité, de timings, de machine d’état, de TargetSession ou du parser Relay.
- Un profil inconnu stocké dans un ancien/futur réglage doit retomber proprement sur `generic` au démarrage.
- Le profil actif est explicite et persistant.

## Tests requis

1. Registre : ajout, ordre, recherche, doublon ID et doublon de nom affiché refusés.
2. Manager : profil actif, fallback vers `generic`, sélection valide/invalide.
3. GenericProfile : état READY, health PASS, détection fallback et prompt strictement identique au `build_initial_prompt` V2.14.
4. Settings : `profile_id=generic` par défaut et round-trip d’une valeur de profil.
5. UI : logique de sélection isolée autant que possible ; aucune régression des tests existants.
6. `compileall` + `pytest` sur Ubuntu/Windows Python 3.11/3.12.

## Gate de fin P0

P0 n’est intégrable que si le diff ne contient aucune logique Unity, si la suite V2.14 reste verte et si le profil générique est le seul profil enregistré par défaut.

## Validation P0

- `clipboard_agent/profiles/` introduit les modèles de détection/health/état, `AgentProfile`, `ProfileRegistry`, `ProfileManager` et `GenericProfile`.
- Le registre livré par défaut contient uniquement `generic` / `Développement général`.
- `GenericProfile.build_initial_prompt()` délègue au builder V2.14 existant ; un test vérifie l’égalité exacte du prompt produit.
- `Settings.profile_id` vaut `generic` par défaut ; les anciens fichiers de réglages sans ce champ restent compatibles et les valeurs futures sont persistées sans perte.
- Le point d’entrée normal `main.py` lance une couche `ProfiledClipboardAgentApp` qui conserve `ClipboardAgentApp` V2.14 intact et ajoute le sélecteur de profil.
- Le changement de profil est refusé pendant Agent Auto, une commande, une Target App ou toute TestSession non `CLOSED`.
- Un ID de profil persistant mais indisponible retombe explicitement sur `generic` au démarrage.
- Aucun `Action: TOOL`, aucun provider Unity et aucune logique Unity n’ont été ajoutés en P0.
- Une première CI a échoué à cause d’un faux objet de test qui n’exposait pas `_ensure_profile_manager`; le runtime compilait. Le harness a été corrigé, ainsi qu’un avertissement de collecte pytest.
- Après correction, la matrice GitHub Actions Ubuntu/Windows × Python 3.11/3.12 est entièrement verte avant la passe documentaire finale.
