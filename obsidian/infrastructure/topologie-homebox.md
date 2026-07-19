# Topologie réseau — Homebox

Version : 1.0
Dernière vérification : 19 juillet 2026

# Vue d'ensemble

NéronOS tourne sur Homebox, un serveur unique, avec chaque service lié à
une adresse loopback dédiée sous 127.0.1.x. C'est `neron.server.yaml` qui
fait autorité sur cette topologie — ce document en est le résumé lisible,
pas la source de vérité.

# Services et ports

| Service | Host | Port | Rôle |
|---|---|---|---|
| core | 127.0.1.1 | 8010 | Orchestrateur, point d'entrée `/input/text` |
| llm | 127.0.1.2 | 8765 | Génération de texte via Ollama |
| goal | 127.0.1.3 | 8030 | Planification, exécution d'agents |
| memory | 127.0.1.4 | 8040 | Mémoire (Oblivia) + Connaissances (Obsidian) |
| voice | 127.0.1.5 | 8045 / 8082 | STT + UI vocale |
| homeassistant | 127.0.1.6 | 8123 | Sidecar registry Home Assistant |
| watchdog | 127.0.1.6 | 8003 | Supervision |
| searxng | 127.0.1.6 | 8080 | Recherche web locale |

Chaque service s'enregistre auprès du registry central (`core`, port 8010,
route `/registry/services`) au démarrage, avec heartbeat périodique.
Vérifier l'état de tout le cluster :

```bash
curl -s -H "Authorization: Bearer $NERON_API_KEY" http://127.0.1.1:8010/registry/services
```

# Pièges connus (retour d'expérience du 19 juillet 2026)

- **`RegistryClient` lit `NERON_SERVICE_HOST`/`NERON_SERVICE_PORT` en
  priorité sur ce que le code Python passe en dur.** Un service peut
  s'annoncer avec la bonne adresse uniquement parce que systemd définit
  ces variables — pas parce que le code est correct par lui-même. Toujours
  vérifier les deux (code ET unité systemd) lors d'un nouveau service.
- **Les providers HTTP internes (core → llm, core → memory) doivent
  explicitement transmettre `NERON_API_KEY` en header `Authorization`.**
  Ce n'est pas automatique — un provider qui ne le fait pas échoue en 403
  dès que le service cible active son authentification.
- **`ss -tlnp` capturé juste après une correction d'unité systemd peut
  refléter un ancien processus, pas la config actuelle** — `enable --now`
  ne redémarre pas un service déjà actif. Toujours confirmer avec un
  `systemctl restart` explicite avant de tirer une conclusion d'un `ss`.
