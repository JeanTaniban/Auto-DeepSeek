# Mission — CDC vision Unity

## Objectif

Définir une architecture visuelle Unity simple pour le LLM : l’agent exprime une intention d’observation ou d’interaction, tandis que UnityProfile choisit le backend, attend les barrières de stabilité, produit les artifacts et indique la fiabilité de la preuve.

## Livrable

- `CDC_UNITY_VISUAL_AUTONOMY.md`

## Principes retenus

- modification par API, validation par vision ;
- capture native Game/Scene avant capture de bureau ;
- Player construit + TargetSession pour le gameplay réel ;
- VisualProbe uniquement pour les points de preuve précis ;
- EvidenceBundle corrélant image, état, faits sémantiques et logs ;
- VisualConfidence explicite ;
- aucun backend de capture exposé au raisonnement normal du LLM ;
- fallbacks et retries gérés côté Relay ;
- prompt Unity court et orienté intention.

## Validation documentaire

Le CDC comporte architecture, modèle de données, politique de fallback, contrat LLM, tests unitaires, tests physiques Windows, phasage VIS0→VIS6 et Definition of Done.
