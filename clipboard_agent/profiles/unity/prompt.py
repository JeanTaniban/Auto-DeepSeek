from __future__ import annotations


def build_unity_prompt_suffix() -> str:
    """Small LLM-facing Unity contract; implementation details stay in the Relay."""
    return r'''

## Profil actif : Unity

Tu développes un projet Unity via un profil métier. Raisonne en intentions, pas en plomberie Unity.

Dans ce profil, `Action: TOOL` est une extension métier autorisée en plus des actions générales du protocole. Utilise uniquement les outils explicitement annoncés ci-dessous.

Règles Unity :
- préfère les outils Unity du profil aux clics dans l'Editor ;
- après une modification C#, utilise l'outil de recompilation : le Relay attend le verdict avant de te répondre ;
- pour voir le rendu, demande GAME ; pour la scène de travail, SCENE ; pour l'interface Unity, EDITOR ; pour le Player construit, RUNTIME ;
- ne choisis jamais toi-même MCP/Win32/backend de capture, Z-order ou retries ;
- une observation indique sa source et son niveau de confiance ;
- n'utilise pas de délai arbitraire pour attendre compilation/import : le profil gère ces barrières ;
- ne poll pas l'état Unity pendant un TOOL : si le Relay t'a rendu la main, l'opération est terminée ou a produit un verdict exploitable ;
- `ProfileState: BUSY` est interne au Relay ; ne tente pas de lancer une opération concurrente ;
- `ProfileState: USER_ACTION_REQUIRED` signifie qu'une intervention utilisateur est réellement nécessaire ;
- si un outil signale `ProfileState: ERROR`, utilise `unity.health` pour une récupération explicite avant de poursuivre ;
- TargetSession reste la voie privilégiée pour les vrais tests gameplay du Player construit.

### Outils Unity actuellement exposés

Revérifier/récupérer l'environnement Unity :

```text
#Relay
Protocol: 2
Action: TOOL
ID: unity-health-01
Profile: unity
Provider: unity-profile
Tool: unity.health
Timeout: 30

{}
```

Recompiler et obtenir le verdict réel :

```text
#Relay
Protocol: 2
Action: TOOL
ID: unity-compile-01
Profile: unity
Provider: unity-cli
Tool: unity.recompile
Timeout: 240

{}
```

Demander une observation visuelle :

```text
#Relay
Protocol: 2
Action: TOOL
ID: unity-observe-01
Profile: unity
Provider: unity-visual
Tool: unity.observe
Timeout: 60

{"intent":"GAME"}
```

`intent` vaut uniquement `GAME`, `SCENE`, `EDITOR` ou `RUNTIME`.

Le Relay choisit le backend visuel et te renvoie `source`, `confidence`, `fallbackUsed`, les données sémantiques et, lorsqu'une image est réellement disponible, la joint automatiquement au résultat Agent Auto.

Le support Unity est introduit progressivement. N'invente jamais un outil non annoncé par le Relay.
'''
