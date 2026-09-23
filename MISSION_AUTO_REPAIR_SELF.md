# Mission — Agent Auto / Auto repair self

## But

Rendre Agent Auto plus autonome face aux incidents locaux récupérables du Relay. Un défaut transitoire qui arrêtait auparavant la boucle doit pouvoir être renvoyé au LLM comme **erreur système structurée**, afin que l'agent ait une chance de reprendre la mission sans intervention humaine.

## Contrat utilisateur

Une option persistante `Auto repair self` est disponible dans l'interface.

- désactivée : comportement fail-safe historique ; un défaut qui appelait l'arrêt Auto continue d'arrêter Auto ;
- activée : un défaut classé récupérable déclenche une récupération contrôlée ;
- les arrêts normaux, exigences de sécurité, intervention utilisateur, incohérences de machine d'état et pertes du canal LLM restent fail-stop/fail-safe.

L'option ne doit jamais transformer une commande `BLOCKED`/`SENSITIVE`, une intervention physique utilisateur ou une incohérence interne en autorisation implicite.

## Résultat système envoyé au LLM

Le Relay doit produire un message canonique :

```text
#RelayResult
Protocol: 2
Kind: SYSTEM_ERROR
ID: system-...
Status: ERROR
Severity: RECOVERABLE
Source: RELAY
AutoRepairSelf: ACTIVE
AutoState: ...
Code: ...
RecoveryAttempt: ...
Description: ...
Instruction: ...
```

Ce résultat signifie : « erreur du logiciel hôte / Relay », et non « erreur du projet ». Le LLM peut ensuite répondre avec une directive `#Relay` normale pour réessayer, diagnostiquer ou choisir une autre stratégie.

## Machine d'état

Ajouter `RECOVERING_SYSTEM_ERROR` à la machine Agent Auto.

Chemin nominal :

```text
<état Auto actif>
      |
      | défaut récupérable
      v
RECOVERING_SYSTEM_ERROR
      |
      | message SYSTEM_ERROR prêt
      v
SENDING
      v
WAITING_VISUAL
      v
WAITING_CLIPBOARD
      v
PROCESSING_REPLY
```

`RECOVERING_SYSTEM_ERROR` doit aussi conserver les sorties `PAUSED` et `OFF` afin de maintenir la priorité utilisateur et le fail-safe.

## Classification

### Arrêts normaux

Ne sont pas des erreurs à auto-réparer :

- arrêt demandé par l'utilisateur ;
- `Action: END` ;
- `SHOW` qui prend volontairement la main ;
- changement/réinitialisation explicite de projet.

### Défauts fatals / non réparables automatiquement

Restent fail-stop :

- violation de la machine d'état Agent Auto ;
- commande de sécurité `BLOCKED` ;
- opération locale concurrente encore active ;
- perte certaine du canal permettant de joindre le LLM (workspace LLM impossible à restaurer, entrée Win32 indisponible) ;
- intervention physique utilisateur ;
- échec pendant la transmission du message d'auto-réparation lui-même ;
- répétition excessive d'erreurs système dans une fenêtre courte.

### Défauts récupérables

Exemples :

- timeout visuel ;
- presse-papiers Copier inchangé/vide ;
- réponse/protocole agent invalide ;
- directive ou outil métier invalide mais sans violation de sécurité ;
- échec transitoire de lancement d'une commande, Target App, TestSession ou outil de profil lorsque plus aucune opération locale ne reste active ;
- détection Copier ponctuellement impossible si le canal d'envoi au LLM reste exploitable.

La classification doit être centralisée et testée. Tant que les appels historiques de `_stop_auto(reason)` portent encore la nature de l'incident dans le texte, la politique peut adapter ces raisons legacy, mais cette dépendance doit rester isolée dans un module dédié.

## Anti-boucle

L'auto-réparation ne doit pas produire une boucle infinie.

- maximum : 3 récupérations système sur une fenêtre glissante de 120 s ;
- un incident pendant qu'un message `SYSTEM_ERROR` est encore en cours de transmission devient fatal ;
- dépasser le budget arrête Agent Auto avec une raison explicite.

## Prompt LLM

Lorsque l'option est activée, le prompt initial doit expliquer `Kind: SYSTEM_ERROR` en quelques lignes seulement :

- c'est une erreur du Relay, pas nécessairement du projet ;
- analyser `Code`, `AutoState` et `Description` ;
- répondre avec une directive `#Relay` normale ;
- ne pas tenter de contourner une condition de sécurité ou une demande d'intervention utilisateur.

## Persistance / UI

Ajouter `Settings.auto_repair_self: bool = False` pour préserver le comportement historique après mise à jour.

L'interface doit exposer un `Checkbutton` clairement nommé `Auto repair self`, persistant immédiatement et au prochain démarrage.

## Tests obligatoires

- persistance du nouveau booléen ;
- transition de tous les états Auto actifs récupérables vers `RECOVERING_SYSTEM_ERROR` ;
- retour `RECOVERING_SYSTEM_ERROR -> SENDING` ;
- erreur récupérable -> message `SYSTEM_ERROR` sans arrêt Auto ;
- option désactivée -> arrêt historique ;
- `BLOCKED`, incohérence d'état, workspace LLM perdu et opération concurrente -> arrêt ;
- erreur pendant une récupération -> arrêt ;
- budget anti-boucle -> arrêt après dépassement ;
- prompt additionnel uniquement lorsque le mode est activé ;
- CI Ubuntu/Windows Python 3.11/3.12.

## Non-objectifs

- masquer silencieusement les exceptions ;
- redémarrer arbitrairement des processus externes sans verdict ;
- ignorer les règles de sécurité ;
- reprendre automatiquement après mouvement physique utilisateur ;
- promettre une réparation autonome de pannes qui empêchent précisément le Relay de communiquer avec le LLM.
