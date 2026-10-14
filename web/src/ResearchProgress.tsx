import { useEffect, useState } from "react";
import { LoaderCircle } from "lucide-react";
import { stageLabel } from "./researchStream";
import type { ResearchResult } from "./api";

export function ResearchProgress({
  stage,
  startedAt,
  count,
  activity = [],
}: {
  stage: string;
  startedAt: number;
  count: number;
  activity?: ResearchResult["activity"];
}) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const elapsed = Math.max(0, Math.floor((now - startedAt) / 1000));
  return (
    <div className="research-progress">
      <div role="status" aria-live="polite">
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
      {activity.length > 0 && (
        <ol className="activity-timeline" role="log" aria-label="Live agent activity" aria-live="polite" aria-relevant="additions">
          {Array.from(new Map(activity.map((item) => [item.id, item])).values())
            .sort((a, b) => (Date.parse(a.created_at) || 0) - (Date.parse(b.created_at) || 0))
            .map((item) => (
              <li key={item.id}>
                <span>{item.message}</span>
                {Number.isFinite(Date.parse(item.created_at)) && (
                  <time dateTime={item.created_at}>
                    {new Date(item.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                  </time>
                )}
              </li>
            ))}
        </ol>
      )}
    </div>
  );
}
