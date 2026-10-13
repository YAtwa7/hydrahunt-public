"""Job collectors — one module per source family.

Every collector returns list[RawJob] and never raises: failures are
logged and recorded in the scans table so the dashboard can show
source health.
"""

from app.collectors.base import RawJob

__all__ = ["RawJob"]
