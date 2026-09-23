from __future__ import annotations


def build_unity_prompt_suffix() -> str:
    """Small LLM-facing Unity contract; implementation details stay in the Relay."""
    return r'''

## Profil actif : Unity

Tu développes un projet Unity via un profil métier. Raisonne en intentions, pas en plomberie Unity.

Règles Unity :
- préfère les outils Unity du profil aux clics dans l'Editor ;
- après une modification C#, utilise l'outil de recompilation : le Relay attend le verdict avant de te répondre ;
- pour voir le rendu, demande GAME ; pour la scène de travail, SCENE ; pour l'interface Unity, EDITOR ; pour le Player construit, RUNTIME ;
- ne choisis jamais toi-même MCP/Win32/backend de capture, Z-order ou retries ;
- une observation indique sa source et son niveau de confiance ;
- n'utilise pas de délai arbitraire pour attendre compilation/import : le profil gère ces barrières ;
- TargetSession reste la voie privilégiée pour les vrais tests gameplay du Player construit.

### Outils Unity actuellement exposés

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
