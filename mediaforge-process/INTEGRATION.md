# Contrat d'intégration — MediaForge ⇆ mediaforge-process (v2, GCS)

Ce document décrit **tout ce que le côté MediaForge doit implémenter** pour
brancher le microservice GCP. Le code applicatif MediaForge n'est PAS modifié ici —
c'est une étape ultérieure séparée. Ce fichier est la référence de ce branchement.

> **Correction v2 majeure** : plus aucun identifiant AWS. Les entrées sont lues
> par URL publique ; les sorties sont écrites sur un bucket **GCS** dédié (URL
> publique + effacement lifecycle 24 h).

---

## 1. Bascule par feature-flag

Côté MediaForge :

```
PROCESSING_ENGINE=abacus   # défaut actuel (moteur FFmpeg existant, limite 600 s)
PROCESSING_ENGINE=gcp      # nouveau microservice Cloud Run
```

- `abacus` → comportement inchangé. `gcp` → route vers le microservice.
- Rollback = repasser à `abacus` + redéployer. Aucune migration de base
  (réutilisation de `MediaJob.ffmpegRequestId`).

---

## 2. Ce que MediaForge envoie à `/process`

MediaForge **construit lui-même la commande FFmpeg complète** puis l'envoie telle
quelle. Le microservice substitue les placeholders et exécute.

| Champ | Type | Notes |
|---|---|---|
| `jobId` | string | Identifiant MediaForge (= `MediaJob.id`). Idempotence. |
| `operation` | string | Une valeur de la table (§5). Informatif. |
| `inputUrls` | string[] | **URLs publiques** des entrées (`getFileUrl(path, true)`). `{{in_1}}` = `inputUrls[0]`. |
| `outputName` | string? | Nom de sortie souhaité. |
| `params` | object | Libre ; traçabilité/debug. |
| `ffmpegCommand` | string | Commande FFmpeg avec placeholders `{{in_N}}` / `{{out_N}}`. |
| `callbackUrl` | string? | Si présent + `CALLBACK_SECRET` → Model B. |

### Règles sur `ffmpegCommand`
- Pas de préfixe `ffmpeg`, pas de `-y` (gérés par le service).
- Entrées : `{{in_1}}`, `{{in_2}}`… (positionnelles). Sorties : `{{out_1}}`…
- `filter_complex` : entourer la valeur de guillemets doubles.

---

## 3. Model A — polling (prioritaire)

```
1. POST /process               -> { requestId }
2. POST /status { requestId }  (toutes les ~2 s côté MediaForge)
     -> { status: "PROCESSING" }
     -> { status: "SUCCESS", outputFiles }   // { out_1: "https://storage.googleapis.com/…" }
     -> { status: "FAILED", error }
```

---

## 4. Model B — callback signé (bonus)

Si `callbackUrl` + `CALLBACK_SECRET` sont fournis, le microservice POST le
résultat vers `callbackUrl` dès la fin :

```
POST https://mediaforges.com/api/jobs/callback
Content-Type: application/json
X-Signature: sha256=<HMAC-SHA256-hex du corps brut, clé = CALLBACK_SECRET>

{
  "jobId": "clx123…",
  "status": "SUCCESS",
  "outputFiles": { "out_1": "https://storage.googleapis.com/<bucket>/outputs/….mp4" },
  "errorMessage": null,
  "durationMs": 84213
}
```

**Vérification côté MediaForge (obligatoire, sur le corps brut) :**
```js
import crypto from 'crypto';
function verify(rawBody, headerSig, secret) {
  const expected = 'sha256=' + crypto.createHmac('sha256', secret).update(rawBody).digest('hex');
  const a = Buffer.from(headerSig || ''); const b = Buffer.from(expected);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}
```

Le callback est **idempotent** côté microservice (retries backoff jusqu'à
`CALLBACK_MAX_RETRIES`). L'endpoint MediaForge doit répondre 2xx vite et ne pas
rétrograder un job déjà `SUCCESS`. Le polling (Model A) reste le filet de sécurité.

---

## 5. Table des opérations (référence)

vidéo / audio / GIF uniquement (PDF & IA exclus — pas de goulot).

| Opération | Sortie |
|---|---|
| TRIM | même ext. |
| MERGE | mp4 |
| EXTRACT_AUDIO | mp3/… |
| CONVERT | mp4/webm/… |
| COMPRESS | même ext. |
| VIDEO_TO_GIF | gif |
| ADD_AUDIO | mp4 |
| RESIZE | même ext. |
| SPEED | même ext. |
| EXTRACT_FRAMES | zip (voir §6) |
| ADD_SUBTITLE | mp4 |
| AUDIO_CONVERT | mp3/… |
| AUDIO_TRIM | même ext. |
| AUDIO_MERGE | même ext. |
| AUDIO_COMPRESS | mp3 |
| AUDIO_NORMALIZE | même ext. |

---

## 6. Cas particulier — EXTRACT_FRAMES

Décision verrouillée : le microservice **zippe toutes les frames** et renvoie un
unique `out_1` = URL du `.zip`. MediaForge n'a qu'une seule URL à gérer.

---

## 7. Secrets GitHub à créer (déploiement)

Dépôt `solariusdeeptech/microservices` → `Settings → Secrets and variables → Actions` :

| Secret | Contenu |
|---|---|
| `GCS_BUCKET_MEDIAFORGE` | Nom du bucket GCS dédié aux sorties (public-read). |
| `GCS_OUTPUT_PREFIX` | Préfixe des sorties (ex. `outputs/`). |
| `PUBLIC_OUTPUT` | `true` (décision verrouillée). |
| `GCP_PROCESSING_API_KEY` | Clé Bearer entrante (générée, partagée avec MediaForge). |
| `CALLBACK_SECRET` | Secret HMAC partagé (généré, partagé avec MediaForge). |

Les secrets GCP (workload identity, projet, région, registre) existent déjà pour
les autres services et sont réutilisés. **Aucun secret AWS.**

---

## 8. À compléter côté MediaForge (étape d'intégration ultérieure)

Tout isolé derrière `PROCESSING_ENGINE` — aucun impact tant que la valeur reste
`abacus`. Aucune migration de base.

1. `lib/processing-engine.ts` (nouveau) : `submitJob(config, meta)` +
   `checkStatus(requestId)`, aiguillant Abacus (existant) ou GCP selon `PROCESSING_ENGINE`.
2. `app/api/jobs/route.ts` : remplacer `createFfmpegRequest(…)` par `submitJob(…)` ;
   passer `jobId`, `callbackUrl`, `ffmpegCommand` au microservice.
3. `app/api/jobs/[id]/route.ts` : remplacer `checkFfmpegStatus(…)` par `checkStatus(…)`.
4. `app/api/jobs/callback/route.ts` (nouveau) : vérifier l'HMAC `X-Signature`, puis
   mettre à jour le `MediaJob` (idempotent, ne rétrograde pas un `SUCCESS`).
5. Nettoyage : `/api/cleanup` ne supprime que les objets S3 ; les sorties GCS sont
   purgées par la lifecycle 24 h (rien à faire).

Aucune modif du frontend, des hooks (`use-media-job.ts`), ni du schéma.

Env vars MediaForge (derrière le flag) : `PROCESSING_ENGINE`, `GCP_PROCESSING_URL`,
`GCP_PROCESSING_API_KEY`, `CALLBACK_SECRET`.

---

## 9. Plan de bascule / rollback

1. Déployer le microservice, configurer la lifecycle 24 h, valider en isolé
   (`/health`, rejets 401/400, cycle complet `/process` → `/status`).
2. Renseigner les env vars MediaForge, garder `PROCESSING_ENGINE=abacus`.
3. Basculer `gcp` en préprod ; tester un job de chaque famille (vidéo lourde, audio, GIF, frames).
4. Basculer en prod. Anomalie → `PROCESSING_ENGINE=abacus` + redéploiement (retour instantané).

---

## 10. Checklist de validation en ligne (après déploiement)

- [ ] `GET /health` → 200 sans auth.
- [ ] `POST /process` sans Bearer → 401.
- [ ] `POST /process` payload invalide → 400.
- [ ] Cycle complet : job vidéo lourd (> 600 s d'encodage) → `SUCCESS` → URL GCS téléchargeable.
- [ ] Callback reçu, signature HMAC vérifiable.
- [ ] Objet GCS effacé automatiquement après 24 h (règle lifecycle).
- [ ] Aucun secret ni URL signature dans les logs.
