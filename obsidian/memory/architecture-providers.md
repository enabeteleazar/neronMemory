# Architecture Memory / Knowledge Providers

Version : 1.0
Créé : 19 juillet 2026

# Principe

La mémoire (ce que Néron retient de l'utilisateur) et la connaissance (la
documentation consultable) sont deux choses distinctes, jamais mélangées :

```
Memory API
    │
    ├── Memory Providers   (Oblivia aujourd'hui)
    │       └── Storage Providers   (SQLite aujourd'hui)
    │
    └── Knowledge Providers   (Obsidian aujourd'hui)
```

Un document Obsidian consulté n'est **jamais** traité comme un souvenir
personnel — même si Néron le lit pour répondre à une question.

# Contrats formels

Trois `Protocol` Python (typage structurel, `server/memory/protocols.py`) :

- **StorageProvider** — persistance brute (SQLite aujourd'hui). Aucune
  logique métier.
- **MemoryProvider** — moteur mémoire (Oblivia). Dédoublonnage, extraction
  de faits, historique.
- **KnowledgeProvider** — base de connaissances consultable (Obsidian).
  Recherche par mots-clés, liste de documents.

Chaque implémentation concrète est vérifiée à la construction via
`isinstance(obj, Protocol)` — une classe qui casse le contrat plante au
démarrage du service, pas silencieusement en production.

# Endpoints

Côté `memory` (port 8040) :
- `/memory/remember`, `/memory/recall`, `/memory/forget`, `/memory/search`
  — souvenirs personnels.
- `/knowledge/query`, `/knowledge/documents`, `/knowledge/health` —
  documentation consultable. Volontairement séparés de `/memory/*`.

Côté `core` (port 8010), l'orchestrateur détecte l'intention et route
vers le bon provider via le Provider Registry (A2A interne) :
- Un fait personnel ("je m'appelle...") ou une question sur
  l'utilisateur → `memory_provider`.
- Une demande explicite de consultation de documentation ("cherche dans
  la documentation...", "que dit la doc sur...") → `knowledge_provider`.

# Étendre le vault

Ce vault Obsidian (`server/memory/obsidian/`) est directement consultable
par Néron via `/knowledge/query`. Ajouter un fichier `.md` ici le rend
immédiatement cherchable — aucun redémarrage de service requis, la
lecture du vault se fait à chaque requête.
