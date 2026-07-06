# Oblivia — Memory Foundation

Oblivia est l’unique source de vérité mémoire de NéronOS.

## Flux

```text
Core → Provider Registry → A2A → Oblivia
Goal → Provider Registry → A2A → Oblivia
```

Le Core détecte et route les intentions. Il ne stocke, n’interprète et ne
reformule aucune connaissance. Il accède à Oblivia exclusivement via le
provider enregistré et A2A.

L’API Python publique est `memory.oblivia`. Les adapters SQLite, Obsidian et
les composants sémantiques sont des détails d’implémentation et ne doivent
pas être importés par le Core.

## Modèle

Oblivia conserve :

- les faits sémantiques sous forme `subject / predicate / object` ;
- les épisodes et traces runtime sous forme de `MemoryRecord` ;
- les documents durables dans Obsidian ;
- les index de recherche normalisés dans SQLite.

Le comportement des relations vient exclusivement de `ontology.py` :

- `immutable` conserve la première valeur et audite les conflits ;
- `replace` clôt la valeur courante et conserve son historique ;
- `accumulate` ajoute des valeurs sans doublon ;
- `preference` permet rétractation et réactivation du même tuple ;
- `event` est déclaré mais ne possède encore aucun prédicat.

Les faits partagent un cycle de vie générique (`valid_from`, `valid_to`,
`is_current`, rétractation et conflit). Aucun oubli ne supprime physiquement
une connaissance. La table historique `lives_at_facts` est conservée comme
source de migration compatible ; les nouvelles écritures vont dans
`knowledge_facts`.

Les décisions produit explicites sont :

- `name` est `replace`, avec historique visible ;
- `spouse` est `replace`, avec historique visible ;
- `likes` est `preference`, avec rétractation et réactivation.

## Reasoner déterministe

`reasoner.py` agrège exclusivement les faits structurés liés à `user`. Il
répond aux synthèses personnelles, alias familiaux, historiques d’emploi et
préférences actives/anciennes. Les faits rétractés ou conflictuels sont exclus
des réponses normales. Les `MemoryRecord` système/projet ne sont jamais
injectés dans une synthèse utilisateur.

Il fournit aussi des vues déterministes sans LLM pour l’audit personnel
(prédicats, catégories, faits obsolètes, rétractations et conflits), les
relations familiales avec réponses prudentes sur le foyer/dépendance, et le
dernier fait personnel appris. Ces vues ne consultent jamais les
`MemoryRecord` projet/système.

Pour les historiques `lives_at` et `works_at`, la projection utilisateur est
d’abord triée par bornes temporelles, puis rendue unique par valeur normalisée
en conservant sa première apparition. Cette projection ne modifie jamais les
faits d’audit.

## Découverte de prédicats

`predicate_discovery.py` classe les déclarations claires de possession,
d’usage et d’achat. Il réutilise les prédicats proches de l’ontologie et
conserve les concepts nouveaux clairs comme candidats ; les concepts ambigus
requièrent une confirmation. Les catégories `devices`, `possessions` et
`purchases` couvrent notamment `owns_device`, `owns_object`, `purchased` et
`uses_device`.

`owns_device` est `accumulate`, avec remplacement ontologique par
`device_slot` : changer de téléphone clôt le téléphone précédent sans
désactiver l’ordinateur ou la tablette. `purchased` reste `accumulate` dans
cette phase afin de fournir un historique idempotent sans activer le
lifecycle `event`.

## Extensions prévues

- extracteur linguistique enrichi ;
- typage épisodique et procédural avancé ;
- graphe de connaissances ;
- embeddings et recherche vectorielle.
