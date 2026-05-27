#!/bin/bash
# ============================================
# Configuration initiale GCP
# Exécuter UNE SEULE FOIS pour créer les ressources
# ============================================

set -e

# Variables — modifier selon votre configuration
PROJECT_ID="solarius-microservices"
REGION="europe-west1"
REPO_NAME="solarius"
SA_NAME="github-deployer"

echo "=== 1. Création du projet GCP ==="
# Si le projet n'existe pas encore :
# gcloud projects create $PROJECT_ID --name="Solarius Microservices"
gcloud config set project $PROJECT_ID

echo "\n=== 2. Activation des APIs ==="
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com

echo "\n=== 3. Création de l'Artifact Registry ==="
gcloud artifacts repositories create $REPO_NAME \
  --repository-format=docker \
  --location=$REGION \
  --description="Images Docker Solarius Microservices"

echo "\n=== 4. Création du Service Account pour CI/CD ==="
gcloud iam service-accounts create $SA_NAME \
  --display-name="GitHub Actions Deployer"

# Donner les permissions nécessaires
SA_EMAIL="$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/run.admin"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/artifactregistry.writer"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/iam.serviceAccountUser"

echo "\n=== 5. Génération de la clé JSON ==="
gcloud iam service-accounts keys create ./gcp-sa-key.json \
  --iam-account=$SA_EMAIL

echo "\n=== 6. Stockage des API Keys dans Secret Manager ==="
echo -n "CHANGEZ-MOI-geoeconomix" | gcloud secrets create api-key-geoeconomix --data-file=-
echo -n "CHANGEZ-MOI-geomatrix" | gcloud secrets create api-key-geomatrix --data-file=-
echo -n "CHANGEZ-MOI-terra" | gcloud secrets create api-key-terraexploration --data-file=-

echo "\n============================================"
echo "✅ Configuration GCP terminée !"
echo ""
echo "Prochaines étapes :"
echo "1. Copiez le contenu de gcp-sa-key.json"
echo "2. Allez sur GitHub → Settings → Secrets → Actions"
echo "3. Créez le secret GCP_SA_KEY avec le contenu de la clé"
echo "4. Créez les secrets API_KEY_GEOECONOMIX, API_KEY_GEOMATRIX, API_KEY_TERRAEXPLORATION"
echo "5. Supprimez gcp-sa-key.json de votre machine locale"
echo "============================================"
