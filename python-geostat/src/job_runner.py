"""
Cloud Batch Job Runner — Python (heavy workloads).
Reads input from GCS, runs computation, writes output to GCS.
Designed to run inside Cloud Batch containers.
"""
import json
import sys
import time
import numpy as np
from google.cloud import storage


def download_from_gcs(gs_path: str) -> dict:
    """Download and parse JSON from GCS."""
    # Parse gs://bucket/path
    parts = gs_path.replace("gs://", "").split("/", 1)
    bucket_name, blob_path = parts[0], parts[1]
    
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    content = blob.download_as_text()
    return json.loads(content)


def upload_to_gcs(gs_path: str, data: dict):
    """Upload JSON result to GCS."""
    parts = gs_path.replace("gs://", "").split("/", 1)
    bucket_name, blob_path = parts[0], parts[1]
    
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    blob.upload_from_string(
        json.dumps(data, default=str),
        content_type="application/json"
    )


def compute_montecarlo(input_data: dict) -> dict:
    """Heavy Monte Carlo simulation."""
    from src.routes.montecarlo import run_montecarlo_core
    return run_montecarlo_core(input_data)


def compute_pit_optimization(input_data: dict) -> dict:
    """Heavy pit optimization."""
    from src.routes.pit_optimize import run_pit_optimization_core
    return run_pit_optimization_core(input_data)


def compute_kriging(input_data: dict) -> dict:
    """Heavy kriging on large datasets."""
    from src.routes.kriging import run_kriging_core
    return run_kriging_core(input_data)


def compute_sgs(input_data: dict) -> dict:
    """Heavy SGS with many realizations."""
    from src.routes.sgs import run_sgs_core
    return run_sgs_core(input_data)


def run(gcs_input: str, gcs_output: str, endpoint: str):
    """Main entry point for Cloud Batch."""
    print(f"Python Cloud Batch Job Runner")
    print(f"Input: {gcs_input}")
    print(f"Output: {gcs_output}")
    print(f"Endpoint: {endpoint}")
    
    # Download input
    input_data = download_from_gcs(gcs_input)
    print(f"Input loaded: {len(input_data)} fields")
    
    t0 = time.time()
    
    # Dispatch
    dispatch = {
        "montecarlo": compute_montecarlo,
        "pit-optimize": compute_pit_optimization,
        "kriging": compute_kriging,
        "sgs": compute_sgs,
    }
    
    handler = None
    for key, func in dispatch.items():
        if key in endpoint:
            handler = func
            break
    
    if handler:
        result = handler(input_data)
    else:
        result = {"error": f"Unknown endpoint: {endpoint}"}
    
    elapsed = round(time.time() - t0, 2)
    result["_processing_time_seconds"] = elapsed
    result["_runtime"] = "python"
    result["_python_version"] = sys.version
    result["_compute_source"] = "python_cloud_batch"
    
    print(f"Computation complete in {elapsed}s")
    
    # Upload result
    upload_to_gcs(gcs_output, result)
    print(f"Result uploaded to {gcs_output}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python job_runner.py <gcs_input> <gcs_output> <endpoint>")
        sys.exit(1)
    run(sys.argv[1], sys.argv[2], sys.argv[3])
