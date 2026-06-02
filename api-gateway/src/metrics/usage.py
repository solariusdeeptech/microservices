"""
Usage metrics — tracks API consumption per platform.
In production, this would persist to Firestore or BigQuery.
For now, in-memory with periodic logging.
"""
import time
from collections import defaultdict
from dataclasses import dataclass, field
from loguru import logger


@dataclass
class PlatformMetrics:
    total_requests: int = 0
    sync_requests: int = 0
    async_requests: int = 0
    total_points_processed: int = 0
    total_blocks_processed: int = 0
    total_compute_ms: int = 0
    errors: int = 0
    endpoints: dict = field(default_factory=lambda: defaultdict(int))
    last_request_at: float = 0.0


# {platform_name: PlatformMetrics}
_metrics: dict[str, PlatformMetrics] = defaultdict(PlatformMetrics)
_start_time = time.time()


def record_request(
    platform: str,
    endpoint: str,
    mode: str = "sync",
    n_points: int = 0,
    n_blocks: int = 0,
    compute_ms: int = 0,
    error: bool = False,
):
    """Record a single API request."""
    m = _metrics[platform]
    m.total_requests += 1
    m.endpoints[endpoint] += 1
    m.last_request_at = time.time()
    m.total_points_processed += n_points
    m.total_blocks_processed += n_blocks
    m.total_compute_ms += compute_ms

    if mode == "sync":
        m.sync_requests += 1
    else:
        m.async_requests += 1

    if error:
        m.errors += 1


def get_metrics(platform: str | None = None) -> dict:
    """Return metrics for a single platform or all platforms."""
    uptime = int(time.time() - _start_time)

    if platform:
        m = _metrics.get(platform)
        if not m:
            return {"platform": platform, "total_requests": 0}
        return {
            "platform": platform,
            "total_requests": m.total_requests,
            "sync_requests": m.sync_requests,
            "async_requests": m.async_requests,
            "total_points_processed": m.total_points_processed,
            "total_blocks_processed": m.total_blocks_processed,
            "total_compute_ms": m.total_compute_ms,
            "avg_compute_ms": round(m.total_compute_ms / max(1, m.total_requests)),
            "errors": m.errors,
            "error_rate": round(m.errors / max(1, m.total_requests) * 100, 1),
            "top_endpoints": dict(sorted(m.endpoints.items(), key=lambda x: -x[1])[:10]),
            "uptime_seconds": uptime,
        }

    # All platforms
    return {
        "platforms": {
            p: {
                "total_requests": m.total_requests,
                "sync_requests": m.sync_requests,
                "async_requests": m.async_requests,
                "errors": m.errors,
                "total_points": m.total_points_processed,
                "total_blocks": m.total_blocks_processed,
            }
            for p, m in _metrics.items()
        },
        "uptime_seconds": uptime,
        "total_requests": sum(m.total_requests for m in _metrics.values()),
    }
