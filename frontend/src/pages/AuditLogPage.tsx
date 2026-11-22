import { useLatestEvents } from "../api/hooks";
import { Card, ErrorNotice, Loading, Pill } from "../components/ui";
import { useAuditStream } from "../hooks/useAuditStream";
import { describeEvent, formatTime } from "../lib/format";

const STATE_LABEL = { connecting: "Connecting", live: "Live", reconnecting: "Reconnecting" } as const;

export function AuditLogPage() {
  const latest = useLatestEvents(50);
  const { events, state } = useAuditStream(latest.data);

  if (latest.isPending) return <Loading label="Loading audit log…" />;
  if (latest.error) return <ErrorNotice error={latest.error} />;

  return (
    <Card
      title="Audit log"
      action={<Pill tone={state === "live" ? "good" : "warn"}>{STATE_LABEL[state]}</Pill>}
    >
      {events.length === 0 ? (
        <p className="text-sm text-slate-500">No events recorded yet.</p>
      ) : (
        <ol aria-label="Audit events" className="divide-y divide-slate-100 dark:divide-slate-800">
          {events.map((e) => (
            <li key={e.seq} className="flex flex-col gap-0.5 py-2 sm:flex-row sm:items-baseline sm:gap-4">
              <span className="w-12 shrink-0 text-xs tabular-nums text-slate-400">#{e.seq}</span>
              <time className="w-44 shrink-0 text-xs tabular-nums text-slate-500" dateTime={e.occurred_at}>
                {formatTime(e.occurred_at)}
              </time>
              <span className="text-sm">{describeEvent(e)}</span>
              <span className="text-xs text-slate-400 sm:ml-auto">{e.type}</span>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}
