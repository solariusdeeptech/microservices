# mediaforge-process (v2 — GCS)

Microservice de traitement média (FFmpeg) déchargé sur **Google Cloud Run**, conçu
pour absorber les jobs lourds vidéo / audio / GIF de **MediaForge** et supprimer
la limite de timeout de 600 s de l'API FFmpeg actuelle.

Architecture (verrouillée, spec v2) :
- **Entrées** : lues par simple `GET` HTTP(S) sur des **URLs publiques** fournies
  par MediaForge. Le service ne parle **pas** à S3/AWS.
- **Exécution** : la commande FFmpeg est fournie **telle quelle** par MediaForge
  (placeholders `{{in_1}}` / `{{out_1}}`) — approche « zéro divergence ».
- **Sorties** : écrites dans un **bucket Google Cloud Storage** dédié, servies via
  **URL publique** `https://storage.googleapis.com/<bucket>/outputs/…`.
- **Rétention** : **effacement automatique à 24 h** via une règle *lifecycle* GCS
  (aucun cron, aucun code de nettoyage).
- **Notification** : **polling** (Model A, prioritaire) + **callback signé HMAC**
  (Model B, bonus).

---

## Contrat d'API

Toutes les routes (sauf `/health`) exigent l'en-tête :

```
Authorization: Bearer <GCP_PROCESSING_API_KEY>
```

### `GET /health` — (sans auth)
```json
{ "status": "ok", "service": "mediaforge-process", "ts": "2026-…" }
```

### `POST /process` → `202 { "requestId": "<uuid>" }`
Soumet un job. Le traitement se fait **en arrière-plan** ; la réponse est immédiate.

```json
{
  "jobId": "clx123…",
  "operation": "CONVERT",
  "inputUrls": ["https://<url-publique-du-fichier-entree>"],
  "outputName": "converted.mp4",
  "outputFormat": "mp4",
  "params": { "targetFormat": "mp4" },
  "ffmpegCommand": "-i {{in_1}} -c:v libx264 -crf 23 -c:a aac -b:a 128k {{out_1}}",
  "callbackUrl": "https://mediaforges.com/api/jobs/callback"
}
```

- `{{in_N}}` est mis en correspondance **positionnelle** avec `inputUrls[N-1]`.
- Idempotence : deux appels avec le même `jobId` renvoient le **même** `requestId`
  (sauf si le job précédent a échoué → nouveau job).

### `POST /status` → `200`
```json
{ "requestId": "<uuid>" }
```
```json
{ "status": "PROCESSING" }
{ "status": "SUCCESS", "outputFiles": { "out_1": "https://storage.googleapis.com/<bucket>/outputs/….mp4" } }
{ "status": "FAILED",  "error": "message lisible" }
```
- `outputFiles` : clé = placeholder de sortie **sans accolades** (`out_1`…),
  valeur = URL publique GCS du fichier produit.
- `requestId` inconnu → `404 { "error": "NOT_FOUND" }`.

---

## Variables d'environnement (v2)

| Variable | Requis | Description |
|---|---|---|
| `GCP_PROCESSING_API_KEY` | ✅ | Clé Bearer entrante (générée par le dev). Alias acceptés : `MEDIAFORGE_API_KEY`, `API_KEY`. `GCP_PROCESSING_API_KEYS` (CSV) pour la rotation. |
| `GCS_BUCKET_MEDIAFORGE` | ✅ | Bucket GCS des sorties (public-read + lifecycle 24 h). Fallback : `GCS_BUCKET`. |
| `GCS_OUTPUT_PREFIX` | ⬜ | Préfixe des sorties (défaut `outputs/`). |
| `PUBLIC_OUTPUT` | ⬜ | `true` (défaut) → rend l'objet lisible publiquement + renvoie une URL publique. |
| `GCP_PROJECT_ID` | ⬜ | Projet GCP (défaut `microservices-497617`). |
| `GOOGLE_APPLICATION_CREDENTIALS` | ⬜ | Compte de service (sinon Workload Identity du runtime Cloud Run). |
| `CALLBACK_SECRET` | ⬜ | Secret HMAC partagé pour signer les callbacks (Model B). Absent → callbacks ignorés. |
| `MAX_INPUTS` | ⬜ | Nombre max d'entrées (défaut `10`). |
| `FFMPEG_TIMEOUT_MS` | ⬜ | Timeout dur d'un run FFmpeg (défaut `3300000` = 55 min). |
| `CALLBACK_MAX_RETRIES` | ⬜ | Tentatives de callback (défaut `6`). |
| `WORK_DIR` | ⬜ | Répertoire temporaire (défaut `/tmp/mediaforge`). |
| `PORT` | ⬜ | Port HTTP (défaut `8080`, injecté par Cloud Run). |

> ❌ **Aucune variable `AWS_*`** — le microservice ne parle jamais à S3.

---

## Organisation dans le bucket GCS

```
outputs/<jobId>/<fichier>        # fichiers de sortie (URL publique)
jobs/<requestId>.json            # état du job (store de statut)
jobs/by-jobid/<jobId>.json       # index d'idempotence jobId -> requestId
```
La règle *lifecycle* (Delete, age = 1 day) s'applique à `outputs/` et `jobs/`.

---

## Rétention 24 h — règle lifecycle GCS (à configurer une fois)

Aucun code applicatif. Sur le bucket `GCS_BUCKET_MEDIAFORGE` :

```bash
cat > /tmp/lifecycle.json <<'JSON'
{ "rule": [ { "action": {"type": "Delete"}, "condition": {"age": 1} } ] }
JSON
gsutil lifecycle set /tmp/lifecycle.json gs://<GCS_BUCKET_MEDIAFORGE>
```

## Développement local

```bash
npm install
GCP_PROCESSING_API_KEY=dev-key GCS_BUCKET_MEDIAFORGE=<bucket> \
GOOGLE_APPLICATION_CREDENTIALS=/path/sa.json node src/server.js
```

## Build & déploiement

Build + déploiement automatiques via GitHub Actions
(`.github/workflows/build-deploy.yml`) → Cloud Build → Cloud Run.
Cloud Run : CPU 4, mémoire 4 Gi, timeout 3600 s, concurrency 1, min 0 / max 5.
Voir `INTEGRATION.md` pour la liste des secrets GitHub à créer.
