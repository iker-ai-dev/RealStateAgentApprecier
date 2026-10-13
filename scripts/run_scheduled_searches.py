"""Wake the public service, then drain a bounded batch of due subscriptions.

Only health reads are retried. An ambiguous paid-run response is never retried.
Provider keys and workspace contents stay on the backend.
"""
import json
import os
import time
import urllib.error
import urllib.request


def main():
    base = os.environ.get("APP_URL", "https://retailscope-ai-demo.onrender.com").rstrip("/")
    secret = os.environ.get("RETAILSCOPE_SCHEDULER_SECRET", "")
    if not base.startswith("https://") or len(secret) < 32:
        raise SystemExit("Scheduler endpoint or credential is not configured.")
    for attempt in range(12):
        try:
            with urllib.request.urlopen(base + "/healthz", timeout=30) as response:
                if json.load(response).get("status") == "ok":
                    break
        except (OSError, ValueError):
            pass
        if attempt == 11:
            raise SystemExit("Render did not become ready. No searches dispatched.")
        time.sleep(10)
    failed = 0
    retry_needed = False
    retry_rounds = 0
    completed = 0
    attempts = 0
    while completed < 20:
        attempts += 1
        if attempts > 84:
            raise SystemExit("Admission retry budget exhausted; inspect scheduler configuration.")
        request = urllib.request.Request(base + "/internal/schedules/tick", data=b"{}",
            headers={"Authorization": "Bearer " + secret, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                result = json.load(response)
        except (OSError, ValueError):
            raise SystemExit("Scheduler request failed; inspect backend telemetry. No automatic retry.") from None
        if result.get("deferred"):
            retry_needed = True
            print("A due search could not start; continuing other work before retrying admission.")
            continue
        if not result.get("ran"):
            if retry_needed:
                if retry_rounds >= 3:
                    raise SystemExit("Search admission remains unavailable. No paid retry was attempted.")
                retry_rounds += 1
                retry_needed = False
                time.sleep(30)
                continue
            print("No more due searches.")
            break
        status = result.get("status", "unknown")
        print("Scheduled search finished:", status)
        completed += 1
        failed += status not in {"completed"}
    else:
        print("Twenty-search batch finished; any later enrollments remain queued for the next dispatch.")
    if failed:
        raise SystemExit(f"{failed} scheduled searches need attention.")


if __name__ == "__main__":
    main()
