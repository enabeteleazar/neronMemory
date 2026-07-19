# Dépannage — incidents connus

Version : 1.0
Dernière mise à jour : 19 juillet 2026

Journal des pannes réellement rencontrées sur Homebox et leur résolution.
Objectif : reconnaître le symptôme plus vite la prochaine fois.

# "Erreur du provider LLM : All connection attempts failed"

**Cause** : le service appelant (souvent `core`) vise la mauvaise URL —
soit un mauvais host/port, soit une variable d'environnement
(`NERON_LLM_URL`, `NERON_CORE_URL`, `NERON_MEMORY_URL`) absente ou fausse.

**Diagnostic** :
```bash
ss -tlnp | grep <port>
curl -s http://<host>:<port>/health
```

# 404 sur un appel provider → provider (ex. `/memory/query`)

**Cause** : le client (`core/providers/...`) code en dur un chemin qui ne
correspond pas aux routes réelles du service cible. Vérifier les deux
côtés à la main :
```bash
grep -n "@app\.\(get\|post\)" server/<service>/app.py
grep -n "base_url\|self\.base_url" server/core/providers/<service>/*.py
```

# 403 "Invalid or missing API key" entre deux services internes

**Cause** : le client HTTP interne n'envoie pas le header
`Authorization: Bearer $NERON_API_KEY`. Rappel : ce n'est jamais
automatique, chaque provider doit l'ajouter explicitement à ses requêtes.

# `SyntaxError: invalid decimal literal` au démarrage d'un service

**Cause vécue** : du texte de terminal (ex. sortie de `ollama list`)
copié-collé par erreur dans un fichier `.py` au lieu du code attendu.
Toujours valider la syntaxe avant de redémarrer un service :
```bash
/etc/neronOS/venv/bin/python -c "import ast; ast.parse(open('<fichier>').read())"
```

# `ImportError: cannot import name 'X' from partially initialized module` (cycle d'import)

**Cause** : deux modules s'importent mutuellement, directement ou via un
package intermédiaire dont le `__init__.py` importe quelque chose de
lourd de façon "eager". Rencontré deux fois en construisant l'architecture
Memory/Knowledge Provider (juillet 2026).

**Solutions, par ordre de préférence** :
1. Extraire la classe/fonction partagée dans un module racine sans
   dépendance (ex. `dumpable.py`), que les deux côtés du cycle importent.
2. Si le cycle porte uniquement sur des annotations de type (pas de code
   exécuté à l'exécution), passer l'import sous
   `if TYPE_CHECKING:` — fonctionne grâce à
   `from __future__ import annotations` (PEP 563), qui garde les
   annotations sous forme de chaînes non évaluées.

**Piège en testant** : l'ordre d'import change le résultat. Un cycle peut
sembler résolu en testant `import A; import B`, puis réapparaître dans
l'ordre réel utilisé par l'application (`import B; import A`). Toujours
tester dans les deux ordres, ou reproduire l'ordre exact du point d'entrée
réel (`app.py`).

# Systemd refuse de redémarrer : "Start request repeated too quickly"

**Cause** : le disjoncteur anti-tempête (`StartLimitBurst=5`) s'est
déclenché après 5 échecs rapprochés.

```bash
sudo systemctl reset-failed neron-<service>
sudo systemctl restart neron-<service>
```

# Un modèle Ollama répond mais latence anormalement élevée (30-70s)

**Causes possibles, dans l'ordre à vérifier** :
1. **Chargement à froid** — le modèle n'était pas en mémoire (RAM limitée
   sur Homebox : 7.2 Gi, souvent 3.6 Gi seulement disponible). Réponse :
   `llm.keep_alive` dans `neron.yaml` (10m par défaut). Se vérifie en
   relançant la même requête juste après : la 2e doit être nettement
   plus rapide.
2. **Mode "thinking" du modèle** (famille Qwen3) — le modèle rédige un
   raisonnement interne complet avant de répondre, même pour une question
   triviale. Pas un problème de chargement — un choix de modèle à
   reconsidérer si la réactivité prime (`llama3.2:3b` n'a pas ce
   comportement).
3. **Modèle inexistant/payant** — un modèle `:cloud` sur Ollama peut
   nécessiter un abonnement (`ollama.com/upgrade`). Se vérifie avec un
   appel direct : `curl http://127.0.0.1:11434/api/generate -d
   '{"model":"...", "prompt":"test", "stream":false}'`.
