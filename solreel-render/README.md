# solreel-render

Microservice de rendu vidéo animée pour **SolReel** (Solarius DeepTech).

Pipeline : **Playwright (Chromium)** enregistre la session → **FFmpeg** découpe les fenêtres de capture, concatène, transcode en **MP4 H.264 / yuv420p** (+ audio optionnel) et génère une vignette → upload public sur **Google Cloud Storage**.

## Contrat d'API

Authentification : en-tête `Authorization: Bearer <SOLREEL_API_KEY>`.

### `POST /render` → `202`
Corps = scénario complet :
```json
{
  "projectId": "...",
  "targetUrl": "https://...",
  "dimensions": { "width": 1920, "height": 1080 },
  "format": "mp4",
  "totalDurationSec": 60,
  "hasAudio": false,
  "audioUrl": null,
  "scenes": [
    {
      "sceneId": "s1", "sceneIndex": 0, "title": "Intro",
      "url": "https://...", "narration": "...", "durationSec": 8,
      "actions": [
        { "type": "navigate", "url": "https://..." },
        { "type": "capture_start" },
        { "type": "scroll", "y": 800, "durationMs": 1200 },
        { "type": "hover", "selector": "#cta" },
        { "type": "click", "selector": "#next" },
        { "type": "type", "selector": "#search", "text": "or" },
        { "type": "wait", "durationMs": 1000 },
        { "type": "capture_end" }
      ]
    }
  ]
}
```
Réponse : `{ "jobId": "...", "status": "QUEUED" }`

### `GET /render/:jobId` → `200`
```json
{
  "jobId": "...",
  "status": "QUEUED | RENDERING | PROCESSING | COMPLETED | FAILED",
  "videoUrl": "https://i.ytimg.com/vi/4lmGHfN9tAk/maxresdefault.jpg",
  "duration": 59.8,
  "fileSize": 12345678,
  "thumbnailUrl": "https://placehold.co/1200x600/e2e8f0/1e293b?text=thumbnail_image_of_a_rendered_video_from_solreel_s",
  "error": null
}
```

### `GET /health` → `200` (sans auth)

## Limites (contrat)
- ≤ 300 s, ≤ 8 scènes, ≤ 1920×1920
- Types d'actions : `navigate`, `wait`, `scroll`, `click`, `hover`, `type`, `capture_start`, `capture_end`

## Décisions de production
- **Job store persistant sur GCS** (`jobs/{jobId}/status.json`) → le `GET` fonctionne quelle que soit l'instance qui répond.
- **Cloud Run `--min-instances=1 --no-cpu-throttling`** → le rendu en arrière-plan (après le 202) n'est jamais coupé.
- **Auth GCS via le service account d'exécution** (ADC / Workload Identity) — aucun fichier de clé.
- **Sortie MP4 H.264 + yuv420p + faststart** → lisible dans le lecteur intégré du navigateur.
- Mémoire **8 Gi / 4 CPU**, `concurrency: 1`.

## Variables d'environnement
| Variable | Rôle |
|---|---|
| `SOLREEL_API_KEY` | Clé Bearer acceptée (obligatoire en prod) |
| `SOLREEL_API_KEYS` | Clés supplémentaires (rotation), séparées par des virgules |
| `GCS_BUCKET_SOLREEL` | Bucket public des rendus (défaut `solreel-renders`) |
| `GCP_PROJECT_ID` / `GCP_REGION` | Projet / région GCP |
| `PORT` | Port HTTP (défaut 8080) |

## Bucket GCS
Le bucket `solreel-renders` doit être en **lecture publique** :
```bash
gsutil mb -l europe-west1 -b on gs://solreel-renders
gsutil iam ch allUsers:objectViewer gs://solreel-renders
# (optionnel) purge auto des vidéos > 30 jours
printf '{"rule":[{"action":{"type":"Delete"},"condition":{"age":30}}]}' > /tmp/lc.json
gsutil lifecycle set /tmp/lc.json gs://solreel-renders
```
Le service account d'exécution de Cloud Run doit avoir `roles/storage.objectAdmin` sur ce bucket.
