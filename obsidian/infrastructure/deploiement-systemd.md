# Déploiement systemd — procédure

Version : 1.0
Dernière vérification : 19 juillet 2026

# Ajouter un nouveau service NéronOS

Patron établi lors de l'audit premortem (juillet 2026), à suivre pour
tout nouveau service Python/FastAPI sur Homebox.

## 1. Unité systemd

```ini
[Unit]
Description=NéronOS <Nom> API
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User=neron
Group=neron-dev
WorkingDirectory=/etc/neronOS/server
EnvironmentFile=-/etc/neronOS/secrets.env
Environment=NERON_ROOT=/etc/neronOS
Environment=NERON_CONFIG=/etc/neronOS/neron.yaml
Environment=NERON_SERVICE_HOST=127.0.1.X
Environment=NERON_SERVICE_PORT=XXXX
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=/etc/neronOS:/etc/neronOS/server
ExecStart=/etc/neronOS/venv/bin/python -m uvicorn <module>.app:app --host 127.0.1.X --port XXXX
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Points non négociables :
- `StartLimitIntervalSec=300` + `StartLimitBurst=5` — anti-tempête de
  redémarrages. Sans ça, un service qui plante en boucle peut saturer les
  logs et masquer la vraie cause.
- `Environment=NERON_SERVICE_HOST`/`PORT` explicites — ne jamais compter
  uniquement sur le défaut codé dans le Python (cf. topologie-homebox.md).
- `PYTHONPATH` avec les deux racines (`/etc/neronOS` ET
  `/etc/neronOS/server`) — les imports `core.xxx`/`llm.xxx`/`memory.xxx`
  en ont besoin.

## 2. Vérification avant activation

**Toujours tester l'import réel avant de toucher systemd** — un cycle
d'import ou une erreur de syntaxe fait planter le service en boucle
(`Start request repeated too quickly` après 5 échecs).

```bash
/etc/neronOS/venv/bin/python -c "
import sys
sys.path.insert(0, '/etc/neronOS')
sys.path.insert(0, '/etc/neronOS/server')
import <module>.app
print('IMPORT COMPLET OK')
"
```

## 3. Activation

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now neron-<nom>
sleep 5
systemctl status neron-<nom> --no-pager -l
curl -s http://127.0.1.X:XXXX/health
```

Si le disjoncteur anti-tempête s'est déclenché (5 échecs) :

```bash
sudo systemctl reset-failed neron-<nom>
sudo systemctl restart neron-<nom>
```
