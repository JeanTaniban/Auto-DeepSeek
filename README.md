# Auto-DeepSeek / Clipboard Agent Relay — V2.13

Application Python locale qui transforme un chat LLM Web utilisé manuellement en **agent de développement semi-autonome**. Le navigateur n’est pas interrogé par API/DOM : le Relay utilise le presse-papiers, des interactions Windows contrôlées, une surveillance visuelle et des fenêtres explicitement liées.

## Fonctionnement

Le flux principal reste simple : le LLM produit une directive copiable, le Relay l’exécute localement, puis renvoie automatiquement le résultat au chat en mode Agent Auto.

Le protocole canonique V2 utilise **un seul marqueur top-level** : `#Relay`, puis un champ `Action:` explicite.

Actions principales :

- `EXECUTION` : commande shell atomique + stdout/stderr ;
- `OPEN_TEST_SESSION` : lance l’application développée et la garde ouverte entre plusieurs tours LLM ;
- `TEST_ACTIONS` : clics/clavier/observations sur cette même application ;
- `CLOSE_TEST_SESSION` : ferme la session et renvoie les logs finaux ;
- `TEMP_TEST` : test UI court launch→actions→close ;
- `SHOW` : démonstration visible à l’utilisateur et arrêt Auto ;
- `END` : fin de mission.

Les marqueurs V1 restent acceptés par le parser pour les conversations déjà démarrées, mais ne sont plus le format recommandé.

## Installation / lancement Windows

```bat
run_windows.bat
```

Le script installe les dépendances nécessaires si besoin. Python 3.11+ est recommandé.

Dépendances runtime :

- `opencv-python-headless`
- `psutil`
- `tkinter` (fourni par l’installation Python Windows standard)

Lancement direct :

```powershell
python main.py
```

## Démarrage d’une mission

1. Sélectionner le dossier du projet.
2. Renseigner **Topic / Destination Goal**.
3. Cliquer **Copier le prompt initial**, le coller puis l’envoyer au LLM.
4. Configurer Agent Auto une fois si nécessaire.
5. Placer la fenêtre LLM juste derrière le Relay dans l’ordre de superposition, puis cliquer **Démarrer Agent Auto**.

Agent Auto commence par observer/copier **la réponse déjà affichée**. Il n’envoie pas le prompt initial une seconde fois.

## Liaison déterministe de la fenêtre LLM

Au démarrage Auto :

1. le Relay prend un snapshot du Z-order Windows ;
2. sa propre fenêtre est `RELAY_WINDOW` ;
3. la première fenêtre utilisateur exploitable sous le Relay devient `LLM_WINDOW` ;
4. les points configurés **Prompt** et **Envoyer** doivent appartenir à ce même HWND ;
5. le HWND reste lié pour la session Auto.

Cela évite de choisir arbitrairement un autre Chrome/Edge lorsqu’il y a plusieurs navigateurs ouverts.

Le Relay conserve la disposition choisie par l’utilisateur : il ne redimensionne pas agressivement le navigateur, car les positions Prompt/Envoyer sont des pixels physiques. En workspace LLM, le navigateur reçoit le focus et le Relay reste visible sans recevoir le clavier.

## Réglages Auto persistants

Les réglages sont sauvegardés dans :

```text
~/.clipboard_agent_relay/settings.json
```

Le panneau est redimensionnable et scrollable. Il contient notamment :

- point Prompt ;
- point Envoyer ;
- rectangle visuel « Réponse agent » ;
- image de référence du bouton Copier ;
- seuil de matching ;
- délais UI ;
- stabilité/timeout de réponse ;
- timings Target App/TestSession : détection fenêtre, délai entre actions, fermeture, restauration, stabilité readiness, poll readiness, settle activation.

Les captures de setup sont persistées immédiatement.

## Détection du bouton Copier

Le template configuré est utilisé **tel quel, à échelle 1.00**. Après stabilité de la réponse, le Relay capture l’écran virtuel Windows entier en pixels natifs et cherche le motif exact avec OpenCV.

Le bouton **Tester la détection Copier** :

- masque temporairement le Relay ;
- capture l’écran virtuel entier ;
- trouve le meilleur candidat ;
- déplace la souris sur son centre **sans cliquer** ;
- affiche confiance, coordonnées locales/globales et position réellement atteinte ;
- écrit les diagnostics dans `~/.clipboard_agent_relay/diagnostics/`.

## Protocole V2 canonique

### Principe

Chaque directive utilise la même enveloppe :

```text
#Relay
Protocol: 2
Action: <ACTION>
ID: <id-unique>
...
```

Les métadonnées viennent avant une ligne vide ; la commande ou les actions viennent après. Le logiciel traduit cette enveloppe vers ses modèles internes typés.

### Choix de l’action

| Besoin | Action |
|---|---|
| terminal / fichiers / build / tests | `EXECUTION` |
| ouvrir une UI persistante | `OPEN_TEST_SESSION` |
| agir sur la UI persistante | `TEST_ACTIONS` |
| fermer la UI persistante | `CLOSE_TEST_SESSION` |
| test UI one-shot | `TEMP_TEST` |
| donner le programme à l’utilisateur | `SHOW` |
| terminer la mission | `END` |

### Commande terminal

```text
#Relay
Protocol: 2
Action: EXECUTION
ID: inspect-1
Shell: powershell
CWD: .
Timeout: 120

git status --short
```

### Ouvrir une TestSession persistante

```text
#Relay
Protocol: 2
Action: OPEN_TEST_SESSION
ID: gui-1
Shell: powershell
CWD: .
Timeout: 120
Launch: python app.py
Ready: auto

#Observe startup
```

Readiness :
- `auto` : contenu rendu puis UI stable ou rendu dynamique actif ;
- `content` : plusieurs frames non noires sans exigence de stabilité ;
- `checkpoint:<nom>` : synchronisation logique puis surface rendue ; recommandé lorsque le code peut être instrumenté ;
- `window` : existence de la fenêtre uniquement ;
- `delay:<ms>` : mécanisme explicite exceptionnel, pas une méthode de détection de readiness.

Instrumentation :

```python
print("[[CAR_CHECKPOINT:main-window-ready]]", flush=True)
print("[[CAR_SCREENSHOT:menu-open]]", flush=True)
```

### Agir sur la TestSession

```text
#Relay
Protocol: 2
Action: TEST_ACTIONS
ID: ui-actions-1

#Click 300;240
#TypeInput "test"
#Key ENTER
#Observe after-input
```

Actions de payload : `#Click`, `#TypeInput`, `#Key`, `#Wait`, `#Observe`.

`#Wait` est réservé aux délais qui font partie du comportement testé. Il ne doit pas servir à deviner le temps de démarrage ou de rendu.

`#Observe` retente une capture transitoirement quasi noire. Une capture qui reste inexploitable est signalée par `OBSERVATION_WARNINGS`.

### Fermer la TestSession

```text
#Relay
Protocol: 2
Action: CLOSE_TEST_SESSION
ID: gui-close-1
```

### Test temporaire

```text
#Relay
Protocol: 2
Action: TEMP_TEST
ID: quick-ui
Shell: powershell
CWD: .
Timeout: 120
Launch: python app.py

#Observe initial
```

### Résultats

Tous les résultats canoniques commencent par :

```text
#RelayResult
Protocol: 2
Kind: ...
LegacyMarker: ...
ID: ...
Status: ...
```

`LegacyMarker` sert uniquement à maintenir la compatibilité avec les prompts V1 déjà en cours.

Les résultats de TestSession ajoutent `SessionState` et `RecommendedNext`. L’agent n’a donc plus à déduire implicitement si la session est encore active ni quel type d’action utiliser ensuite.

## Workspaces Target App

Une TestSession possède une fenêtre cible appartenant obligatoirement au processus lancé ou à l’un de ses descendants.

Lors d’une action :

```text
LLM_WORKSPACE
 → TARGET_WORKSPACE
 → actions / captures
 → restauration + vérification LLM_WORKSPACE
 → envoi #TestSessionResult
```

La Target App n’est pas minimisée par principe : elle est placée au premier plan pendant l’interaction puis repassée derrière le LLM par restauration du Z-order/focus. Cela évite de casser les moteurs GUI qui suspendent leur rendu lorsqu’ils sont minimisés.

Les contrôles de workspace sont **non destructifs** : si le bon HWND est déjà au premier plan, ils ne font rien. Un changement de focus ne réapplique pas la géométrie mémorisée et n’utilise `SW_RESTORE` que si la fenêtre est réellement minimisée. Avant une action navigateur, la géométrie du HWND LLM et la propriété des points Prompt/Envoyer sont vérifiées ; en cas de dérive, Auto s’arrête au lieu de déplacer la fenêtre juste avant le clic ou la détection.

## Intervention utilisateur

Tout mouvement physique de souris pendant Agent Auto est prioritaire :

- timers annulés ;
- actions Target restantes annulées ;
- Auto passe à `PAUSED` ;
- aucun retour tardif vers le navigateur ;
- la main reste à l’utilisateur.

Il n’existe pas de reprise implicite depuis `PAUSED`.

## Nouveau projet

Le bouton **Nouveau projet** réinitialise projet, Goal, historique et état de session. Une TestSession active doit être fermée auparavant afin d’éviter de mélanger deux projets.

## Tests

```bash
./test.sh
```

ou :

```bash
python -m pytest
```

La machine d’état complète est dans [`STATE_MACHINE.md`](STATE_MACHINE.md).

## Build Windows

```bat
build_windows_exe.bat
```

## Scripts Bash

```bash
./setup.sh
./run_venv.sh
./test.sh
```

## Limites

- les interactions bureau/TestSession sont Windows-only ;
- la validation CI ne remplace pas un test sur un bureau Windows interactif pour `SendInput`, hooks souris, Z-order et capture GDI ;
- ce n’est pas une sandbox OS : les commandes terminal restent puissantes ;
- les opérations sensibles sont donc soumises à confirmation/blocage.
