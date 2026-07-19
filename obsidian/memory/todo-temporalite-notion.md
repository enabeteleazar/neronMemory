# TODO — Memory : temporalité & Knowledge Provider Notion

Version : 1.0
Créé : 19 juillet 2026, en clôture de la session d'audit/construction du
Memory/Knowledge Provider.

Deux chantiers proposés, non commencés — capturés ici pour qu'une future
session (ou Néron lui-même, via `/knowledge/query`) les retrouve sans
dépendre de la mémoire humaine.

---

# 1. Temporalité des souvenirs

## État actuel (vérifié dans le code, 19 juillet 2026)

- `KnowledgeFact` (oblivia/schemas.py) a déjà les champs temporels :
  `created_at`, `valid_from`, `valid_to`, `is_current`, `retracted`,
  `retracted_at`, `retraction_reason`.
- `SQLiteMemoryAdapter.add_fact()` ferme déjà le fait précédent
  ("valid_to" + "is_current=False") pour une **liste figée** de
  prédicats : `lives_at`, `works_at`, `favorite_color`, `name`,
  `operating_system`, `smartphone`, `spouse`, `vehicle`. Rien en dehors
  de cette liste n'est fermé automatiquement.
- `oblivia/timeline.py` est un stub vide :
  `def chronological(values): return list(values)`.
- `SemanticQueryEngine` a des branches câblées en dur pour l'historique
  ("où habitais-je avant", "où ai-je travaillé avant") — spécifiques à
  `lives_at`/`works_at`, aucun mécanisme générique.

## Bug observé en conséquence (non corrigé, hors scope de ce soir)

Un prédicat auto-généré (ex. `animal_prefere`, créé par le repli
générique de `FactExtractor` ajouté ce soir) peut se dupliquer :
mémoriser deux fois "mon animal préféré est le renard" crée deux lignes
`is_current=True` distinctes plutôt que de mettre à jour une seule
entrée ou de fermer la précédente.

## Travail à faire

1. Étendre la fermeture automatique de fait courant aux prédicats
   auto-générés (génériques), pas seulement à la liste figée actuelle.
2. Distinguer prédicats **singuliers** (une seule valeur valide à la
   fois — nom, domicile, voiture) des prédicats **cumulatifs**
   (plusieurs valeurs valides simultanément — `likes`, `has_child`).
   Attention : généraliser la fermeture à *tous* les prédicats sans
   cette distinction casserait `likes`/`has_child`.
3. Implémenter `oblivia/timeline.py` pour de vrai — un tri chronologique
   réutilisable génériquement par `SemanticQueryEngine`, au lieu des
   branches "avant" câblées en dur seulement pour deux prédicats.
4. Endpoint `/memory/timeline?subject=X&predicate=Y` pour inspecter
   l'historique complet d'un fait (pas seulement sa valeur courante).
5. Optionnel, plus tard : surfacer la temporalité dans les réponses de
   recall par défaut (ex. "Tu habites à Paris depuis le 12 mars").

---

# 2. Knowledge Provider Notion

## Où ça s'intègre

Directement dans l'architecture construite ce soir
(server/memory/protocols.py) : un second `KnowledgeProvider`, à côté
d'`ObsidianKnowledgeProvider`, satisfaisant le même contrat
(`query()`, `list_documents()`, `status()`).

## Décision de conception à trancher avant de coder

**Option A — vrai client MCP.** NeronOS parle le protocole MCP,
se connecte au serveur MCP officiel de Notion. Investissement plus
lourd (ajouter un client MCP générique à NeronOS), mais réutilisable
pour n'importe quel futur serveur MCP, pas seulement Notion.

**Option B — appel direct à l'API REST Notion.** Un
`NotionKnowledgeProvider` qui appelle l'API Notion en HTTP, comme
`ObsidianKnowledgeProvider` lit le système de fichiers. Plus simple,
plus rapide à livrer, mais spécifique à Notion — pas de bénéfice pour
un futur autre service.

Non tranché — dépend de si l'objectif est "accéder à Notion" (Option B
suffit) ou "pouvoir brancher n'importe quel serveur MCP à terme"
(Option A justifiée).

## Points d'attention

- Authentification : token d'intégration Notion dans `secrets.env`,
  jamais en dur dans le code (cohérent avec le reste de NeronOS).
- Pas de synchronisation périodique de tout l'espace Notion en local —
  `ObsidianKnowledgeProvider` interroge à la demande sans cache ;
  Notion a des limites de débit API, un appel par requête utilisateur
  est probablement le bon calibre.
- Réutiliser le même séparateur Memory/Knowledge déjà en place :
  Notion reste un Knowledge Provider, jamais un souvenir personnel.
