import { useEffect, useState } from "react";
import { LoaderCircle } from "lucide-react";
import { stageLabel } from "./researchStream";

export function ResearchProgress({
  stage,
  startedAt,
  count,
}: {
  stage: string;
  startedAt: number;
  count: number;
}) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const elapsed = Math.max(0, Math.floor((now - startedAt) / 1000));
  return (
    <div className="research-progress" role="status" aria-live="polite">
      <div>
        <LoaderCircle size={16} className="spin" />
        <strong>{stageLabel(stage)}</strong>
      </div>
      <p>
        <span aria-live="off">
          {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}{" "}
          elapsed
        </span>
        {count > 0 && <span> · {count} sources retrieved</span>}
      </p>
      <p className="muted">
        Recent searches took 1–3 minutes. Timing varies by source and evidence
        checks.
      </p>
    </div>
  );
}
