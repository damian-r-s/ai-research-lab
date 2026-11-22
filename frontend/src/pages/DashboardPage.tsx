import { Link } from "react-router";

import { useSystemStatus } from "../api/hooks";
import type { SystemStatus } from "../api/types";
import { Card, ErrorNotice, Loading, Meter, Pill, Row } from "../components/ui";
import { formatBytes, formatDuration, formatTime } from "../lib/format";

function Resources({ r }: { r: NonNullable<SystemStatus["resources"]> }) {
  return (
    <>
      <Card title="CPU">
        <Meter label="Usage" percent={r.cpu.percent} detail={`${r.cpu.percent.toFixed(0)}%`} />
        <dl className="mt-3">
          <Row label="Cores">{r.cpu.logical_cores}</Row>
          <Row label="Load (1 / 5 / 15 min)">
            {[r.cpu.load_avg_1m, r.cpu.load_avg_5m, r.cpu.load_avg_15m].map((l) => l.toFixed(2)).join(" / ")}
          </Row>
        </dl>
      </Card>
      <Card title="Memory">
        <Meter
          label="RAM"
          percent={r.memory.percent}
          detail={`${formatBytes(r.memory.used_bytes)} / ${formatBytes(r.memory.total_bytes)}`}
        />
        <dl className="mt-3">
          <Row label="Available">{formatBytes(r.memory.available_bytes)}</Row>
          <Row label="Swap used">
            {formatBytes(r.memory.swap_used_bytes)} / {formatBytes(r.memory.swap_total_bytes)}
          </Row>
        </dl>
      </Card>
      <Card title="Storage">
        <div className="space-y-3">
          {r.disks.map((d) => (
            <Meter
              key={d.mountpoint}
              label={d.mountpoint}
              percent={d.percent}
              detail={`${formatBytes(d.used_bytes)} / ${formatBytes(d.total_bytes)}`}
            />
          ))}
        </div>
      </Card>
      <Card title="GPU">
        {r.gpu.status === "ok" ? (
          <div className="space-y-4">
            {r.gpu.devices.map((g) => (
              <div key={g.index} className="space-y-2">
                <p className="text-sm font-medium">{g.name}</p>
                <Meter
                  label="VRAM"
                  percent={(g.memory_used_bytes / g.memory_total_bytes) * 100}
                  detail={`${formatBytes(g.memory_used_bytes)} / ${formatBytes(g.memory_total_bytes)}`}
                />
                <dl>
                  <Row label="Utilisation">{g.utilization_percent === null ? "n/a" : `${g.utilization_percent}%`}</Row>
                  <Row label="Temperature">{g.temperature_c === null ? "n/a" : `${g.temperature_c} °C`}</Row>
                  <Row label="Power">{g.power_w === null ? "n/a" : `${g.power_w.toFixed(1)} W`}</Row>
                </dl>
              </div>
            ))}
            <p className="text-xs text-slate-500">Driver {r.gpu.driver_version}</p>
          </div>
        ) : (
          <div className="space-y-1 text-sm">
            <p>No GPU in use</p>
            <p className="text-slate-500 dark:text-slate-400">{r.gpu.reason}</p>
          </div>
        )}
      </Card>
    </>
  );
}

export function DashboardPage() {
  const { data, error, isPending } = useSystemStatus();
  if (isPending) return <Loading label="Loading system status…" />;
  if (error) return <ErrorNotice error={error} />;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">Lab status</h1>
        <Pill tone={data.status === "ok" ? "good" : "warn"}>{data.status === "ok" ? "Operational" : "Degraded"}</Pill>
        <span className="text-sm text-slate-500 dark:text-slate-400">
          v{data.version} · {data.env} · up {formatDuration(data.uptime_s)}
          {data.resources && ` · ${data.resources.hostname}`}
        </span>
      </div>

      {data.warnings.length > 0 && (
        <ul aria-label="Warnings" className="space-y-1 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
          {data.warnings.map((w) => (
            <li key={w}>⚠ {w}</li>
          ))}
        </ul>
      )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {data.resources ? <Resources r={data.resources} /> : <Loading label="Waiting for the first resource sample…" />}
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Card title="Database">
          <dl>
            <Row label="Connection">
              <Pill tone={data.database.reachable ? "good" : "bad"}>{data.database.reachable ? "Reachable" : "Unreachable"}</Pill>
            </Row>
            <Row label="Schema">
              <Pill tone={data.database.migrations_current ? "good" : "bad"}>
                {data.database.migrations_current ? "Up to date" : "Migration needed"}
              </Pill>
            </Row>
            <Row label="Revision">{data.database.current_revisions.join(", ") || "none"}</Row>
          </dl>
        </Card>
        <Card title="Audit log" action={<Link className="text-sm underline" to="/audit">Open</Link>}>
          <dl>
            <Row label="Recorded events">{data.audit.total_events.toLocaleString("en-GB")}</Row>
            <Row label="Live updates">
              <Pill tone={data.audit.live_updates === "connected" ? "good" : "warn"}>
                {data.audit.live_updates === "connected" ? "Connected" : "Reconnecting"}
              </Pill>
            </Row>
          </dl>
        </Card>
      </div>

      <p className="text-xs text-slate-500">
        Readings sampled {data.resources ? formatTime(data.resources.sampled_at) : "—"}, refreshed every 5 seconds.
        Research statistics will appear here once research workflows exist (Phase 2 onwards).
      </p>
    </div>
  );
}
