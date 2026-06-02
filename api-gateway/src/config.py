"""API Gateway configuration — loaded from environment variables."""
import os
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # --- Service URLs (Cloud Run backends) ---
    PYTHON_GEOSTAT_URL: str = os.getenv("PYTHON_GEOSTAT_URL", "http://python-geostat:8080")
    PYTHON_VIZ_URL: str = os.getenv("PYTHON_VIZ_URL", "http://python-viz:8081")

    # --- Cloud Batch (heavy jobs) ---
    GCP_PROJECT_ID: str = os.getenv("GCP_PROJECT_ID", "microservices-497617")
    GCP_REGION: str = os.getenv("GCP_REGION", "europe-west1")
    GCS_BUCKET: str = os.getenv("GCS_BUCKET", "solarius-jobs")
    JULIA_IMAGE: str = os.getenv("JULIA_IMAGE", "europe-west1-docker.pkg.dev/microservices-497617/solarius/julia-geostat:latest")
    PYTHON_HEAVY_IMAGE: str = os.getenv("PYTHON_HEAVY_IMAGE", "europe-west1-docker.pkg.dev/microservices-497617/solarius/python-geostat:latest")

    # --- API Keys (multi-tenant) ---
    API_KEY_GEOECONOMIX: str = os.getenv("API_KEY_GEOECONOMIX", "")
    API_KEY_GEOMATRIX: str = os.getenv("API_KEY_GEOMATRIX", "")
    API_KEY_TERRAEXPLORATION: str = os.getenv("API_KEY_TERRAEXPLORATION", "")

    # --- Routing thresholds ---
    MAX_POINTS_CLOUD_RUN: int = int(os.getenv("MAX_POINTS_CLOUD_RUN", "50000"))
    MAX_BLOCKS_CLOUD_RUN: int = int(os.getenv("MAX_BLOCKS_CLOUD_RUN", "100000"))
    MAX_REALIZATIONS_CLOUD_RUN: int = int(os.getenv("MAX_REALIZATIONS_CLOUD_RUN", "50"))

    # --- Gateway ---
    PORT: int = int(os.getenv("PORT", "8080"))
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    REQUEST_TIMEOUT: float = float(os.getenv("REQUEST_TIMEOUT", "300"))

    class Config:
        env_file = ".env"
        extra = "ignore"

    def get_api_keys(self) -> dict[str, str]:
        """Return mapping {key_value: platform_name}."""
        keys = {}
        if self.API_KEY_GEOECONOMIX:
            keys[self.API_KEY_GEOECONOMIX] = "geoeconomix"
        if self.API_KEY_GEOMATRIX:
            keys[self.API_KEY_GEOMATRIX] = "geomatrix"
        if self.API_KEY_TERRAEXPLORATION:
            keys[self.API_KEY_TERRAEXPLORATION] = "terraexploration"
        return keys


settings = Settings()
