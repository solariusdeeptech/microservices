"""
Cloud Batch integration — submit heavy jobs to GCP Cloud Batch.
Jobs run on dedicated VMs with pre-compiled Julia or Python containers.
Results are written to GCS and retrieved via signed URLs.
"""
import json
import uuid
from datetime import datetime, timezone
from loguru import logger

try:
    from google.cloud import batch_v1
    from google.cloud import storage as gcs
    CLOUD_BATCH_AVAILABLE = True
except ImportError:
    CLOUD_BATCH_AVAILABLE = False
    logger.warning("google-cloud-batch not available — Cloud Batch jobs disabled")

from src.config import settings

# Job status constants
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"


def _generate_job_id(platform: str, endpoint: str) -> str:
    """Generate a unique job ID: {platform}-{endpoint}-{short_uuid}."""
    short = uuid.uuid4().hex[:8]
    clean_ep = endpoint.replace("/", "-").strip("-")
    return f"{platform}-{clean_ep}-{short}"


def _gcs_input_path(job_id: str) -> str:
    return f"jobs/{job_id}/input.json"


def _gcs_output_path(job_id: str) -> str:
    return f"jobs/{job_id}/output.json"


async def upload_job_input(job_id: str, payload: dict) -> str:
    """Upload job input payload to GCS. Returns gs:// path."""
    if not CLOUD_BATCH_AVAILABLE:
        raise RuntimeError("Cloud Batch not available")

    client = gcs.Client(project=settings.GCP_PROJECT_ID)
    bucket = client.bucket(settings.GCS_BUCKET)
    blob = bucket.blob(_gcs_input_path(job_id))
    blob.upload_from_string(
        json.dumps(payload, default=str),
        content_type="application/json"
    )
    gs_path = f"gs://{settings.GCS_BUCKET}/{_gcs_input_path(job_id)}"
    logger.info(f"Uploaded input for job {job_id} to {gs_path}")
    return gs_path


async def submit_batch_job(
    platform: str,
    endpoint: str,
    payload: dict,
    runtime: str = "julia",  # "julia" or "python"
    machine_type: str = "e2-highmem-4",
    max_duration: str = "3600s",
) -> dict:
    """
    Submit a heavy computation job to Cloud Batch.
    Returns {job_id, status, gcs_input, gcs_output}.
    """
    if not CLOUD_BATCH_AVAILABLE:
        return {
            "error": "CLOUD_BATCH_UNAVAILABLE",
            "message": "Cloud Batch SDK not installed. Install google-cloud-batch."
        }

    job_id = _generate_job_id(platform, endpoint)

    # 1. Upload input to GCS
    gcs_input = await upload_job_input(job_id, payload)
    gcs_output = f"gs://{settings.GCS_BUCKET}/{_gcs_output_path(job_id)}"

    # 2. Select container image
    image = settings.JULIA_IMAGE if runtime == "julia" else settings.PYTHON_HEAVY_IMAGE

    # 3. Create Cloud Batch job
    client = batch_v1.BatchServiceClient()

    # Container — reads from GCS, computes, writes result to GCS
    container = batch_v1.Runnable.Container(
        image_uri=image,
        commands=[
            "python3", "-c",
            f"from job_runner import run; run('{gcs_input}', '{gcs_output}', '{endpoint}')"
        ] if runtime == "python" else [
            "julia", "--project=/app", "-e",
            f'include("/app/src/job_runner.jl"); run_job("{gcs_input}", "{gcs_output}", "{endpoint}")'
        ],
        volumes=["/mnt/disks/work:/work"],
    )

    runnable = batch_v1.Runnable(container=container)

    task_spec = batch_v1.TaskSpec(
        runnables=[runnable],
        max_run_duration=max_duration,
        max_retry_count=1,
    )

    task_group = batch_v1.TaskGroup(
        task_count=1,
        task_spec=task_spec,
    )

    # Allocation policy — machine type + region
    instance_policy = batch_v1.AllocationPolicy.InstancePolicy(
        machine_type=machine_type,
    )
    instances = batch_v1.AllocationPolicy.InstancePolicyOrTemplate(
        policy=instance_policy,
    )
    allocation = batch_v1.AllocationPolicy(
        instances=[instances],
        location=batch_v1.AllocationPolicy.LocationPolicy(
            allowed_locations=[f"zones/{settings.GCP_REGION}-b"]
        ),
    )

    job = batch_v1.Job(
        task_groups=[task_group],
        allocation_policy=allocation,
        logs_policy=batch_v1.LogsPolicy(
            destination=batch_v1.LogsPolicy.Destination.CLOUD_LOGGING
        ),
        labels={
            "platform": platform,
            "endpoint": endpoint.replace("/", "-").strip("-"),
            "runtime": runtime,
        },
    )

    # Submit
    parent = f"projects/{settings.GCP_PROJECT_ID}/locations/{settings.GCP_REGION}"
    request = batch_v1.CreateJobRequest(
        parent=parent,
        job_id=job_id,
        job=job,
    )

    try:
        result = client.create_job(request=request)
        logger.info(f"Submitted Cloud Batch job: {job_id} ({runtime}, {machine_type})")
        return {
            "job_id": job_id,
            "status": STATUS_PENDING,
            "runtime": runtime,
            "machine_type": machine_type,
            "gcs_input": gcs_input,
            "gcs_output": gcs_output,
            "batch_job_name": result.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.exception(f"Failed to submit Cloud Batch job: {e}")
        return {
            "job_id": job_id,
            "status": STATUS_FAILED,
            "error": str(e),
        }


async def get_job_status(job_id: str) -> dict:
    """Poll Cloud Batch for job status."""
    if not CLOUD_BATCH_AVAILABLE:
        return {"job_id": job_id, "status": "unknown", "error": "Cloud Batch SDK not available"}

    client = batch_v1.BatchServiceClient()
    job_name = f"projects/{settings.GCP_PROJECT_ID}/locations/{settings.GCP_REGION}/jobs/{job_id}"

    try:
        job = client.get_job(name=job_name)
        state = job.status.state.name  # QUEUED, SCHEDULED, RUNNING, SUCCEEDED, FAILED

        status_map = {
            "QUEUED": STATUS_PENDING,
            "SCHEDULED": STATUS_PENDING,
            "RUNNING": STATUS_RUNNING,
            "SUCCEEDED": STATUS_SUCCEEDED,
            "FAILED": STATUS_FAILED,
            "DELETION_IN_PROGRESS": STATUS_FAILED,
        }

        return {
            "job_id": job_id,
            "status": status_map.get(state, "unknown"),
            "batch_state": state,
            "gcs_output": f"gs://{settings.GCS_BUCKET}/{_gcs_output_path(job_id)}",
        }
    except Exception as e:
        logger.error(f"Failed to get status for {job_id}: {e}")
        return {"job_id": job_id, "status": "unknown", "error": str(e)}


async def get_job_result(job_id: str) -> dict | None:
    """Download job result from GCS. Returns parsed JSON or None."""
    if not CLOUD_BATCH_AVAILABLE:
        return None

    try:
        client = gcs.Client(project=settings.GCP_PROJECT_ID)
        bucket = client.bucket(settings.GCS_BUCKET)
        blob = bucket.blob(_gcs_output_path(job_id))

        if not blob.exists():
            return None

        content = blob.download_as_text()
        return json.loads(content)
    except Exception as e:
        logger.error(f"Failed to retrieve result for {job_id}: {e}")
        return None
