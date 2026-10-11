"""Session-owned, bounded cached-source scans. No network or Streamlit state access."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

from app.observability import start_agent_transaction, start_span

GROUPS = ("Listing platforms", "Broker documents", "Broker websites")
MAX_RECORDS = 100


def source_group(row):
    url = str(row.get("url") or "")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
    except ValueError:
        return GROUPS[2]
    if host in ("crexi.com", "loopnet.com") or host.endswith((".crexi.com", ".loopnet.com")):
        return GROUPS[0]
    if parsed.path.lower().endswith(".pdf"):
        return GROUPS[1]
    return GROUPS[2]


def scan_source(rows):
    # Observations remain unchanged; no snippet parsing or financial inference.
    properties = [row for _, row in rows if row.get("lead_kind") not in ("listing_directory", "market_report")]
    return {"rows": rows, "observed": len(properties),
            "resources": len(rows) - len(properties),
            "sources": sorted({str(row.get("source_name") or "Unknown") for _, row in rows}),
            "vintages": sorted({str(row.get("retrieved_at") or "Unknown") for _, row in rows}),
            "unknown_availability": sum(row.get("active_status") in (None, "unknown") for row in properties)}


def summarize(groups, screen, filters):
    with start_agent_transaction("retailscope_ai.discovery"):
        return _summarize(groups, screen, filters)


def _summarize(groups, screen, filters):
    groups = [future.result() for future in groups]
    indexed = sorted((item for group in groups for item in group["rows"]), key=lambda item: item[0])
    rows = [row for _, row in indexed]
    with start_span("discovery.screen", "Screen cached public listing leads"):
        leads = screen(rows, **filters)
    return {"observed": sum(group["observed"] for group in groups),
            "resources": sum(group["resources"] for group in groups),
            "leads": leads,
            "vintages": sorted({str(row.get("retrieved_at") or "Unknown") for row in rows}),
            "unknown_availability": sum(group["unknown_availability"] for group in groups)}


@dataclass
class DiscoveryJob:
    executor: ThreadPoolExecutor
    workers: dict
    screen: object
    filters: dict
    started_at: str
    summary: object = None
    result: dict | None = None
    error: str | None = None
    finished_at: str | None = None
    closed: bool = False

    @property
    def active(self):
        return not self.closed

    def poll(self):
        """Only the UI caller advances dependencies and owns lifecycle/state."""
        if self.closed:
            return self
        if self.summary is not None and self.summary.done():
            try:
                self.result = self.summary.result()
            except Exception:
                failed_source = any(f.cancelled() or f.done() and f.exception() for f in self.workers.values())
                self.error = ("A cached-source worker failed. No new scan results were published." if failed_source else
                              "Cached-source screening failed. Saved evidence remains available for review.")
        if self.result is not None or self.error is not None:
            self.executor.shutdown(wait=False, cancel_futures=True)
            self.closed = True
            self.finished_at = datetime.now(timezone.utc).isoformat()
        return self

    def cancel(self):
        """Discard obsolete results; running bounded local tasks finish without publication."""
        for future in (*self.workers.values(), self.summary):
            if future is not None:
                future.cancel()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.closed = True

    def statuses(self):
        states = {}
        for name, future in self.workers.items():
            states[name] = ("Error" if future.cancelled() or future.done() and future.exception() else
                            "Complete" if future.done() else "Running" if future.running() else "Queued")
        states["Screen & summarize"] = ("Error" if self.error else "Complete" if self.result is not None else
                                         "Running" if self.summary is not None and self.summary.running() else "Waiting for sources")
        return states


def _scan_source_traced(rows):
    with start_agent_transaction("retailscope_ai.discovery_source"):
        with start_span("discovery.scan", "Scan cached public listing sources"):
            return scan_source(rows)


def start_scan(records, screen, filters=None):
    if not isinstance(records, list) or len(records) > MAX_RECORDS or any(not isinstance(row, dict) for row in records):
        raise ValueError("Expected at most 100 saved public-source records.")
    groups = {name: [] for name in GROUPS}
    for index, row in enumerate(deepcopy(records)):
        groups[source_group(row)].append((index, row))
    executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="cached-discovery")
    try:
        workers = {name: executor.submit(_scan_source_traced, rows) for name, rows in groups.items()}
        job = DiscoveryJob(executor, workers, screen, deepcopy(filters or {}),
                           datetime.now(timezone.utc).isoformat())
        job.summary = executor.submit(summarize, list(workers.values()), screen, job.filters)
        # Close submissions immediately: threads exit after these four bounded tasks,
        # including when a browser disconnects and no UI poll ever arrives.
        executor.shutdown(wait=False)
        return job
    except Exception:
        executor.shutdown(wait=False, cancel_futures=True)
        raise
