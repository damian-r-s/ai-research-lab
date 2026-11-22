import type { AuditEvent } from "../api/types";

export function formatBytes(bytes: number): string {
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 100 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

export function formatDuration(seconds: number): string {
  const s = Math.floor(seconds);
  const d = Math.floor(s / 86_400);
  const h = Math.floor((s % 86_400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m`;
  return `${s}s`;
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleString("en-GB", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

const str = (v: unknown) => (typeof v === "string" ? v : "");

/** One-line English description of an audit event; unknown types fall back to the type name. */
export function describeEvent(e: AuditEvent): string {
  const p = e.payload;
  switch (e.type) {
    case "auth.login.succeeded":
      return `${str(p.username)} logged in`;
    case "auth.login.failed": {
      const reason = { invalid_credentials: "wrong credentials", inactive: "account disabled", throttled: "too many attempts" }[str(p.reason)];
      return `Failed login for ${str(p.username)} (${reason ?? str(p.reason)})`;
    }
    case "auth.session.revoked":
      return "User logged out";
    case "auth.user.created":
      return `User ${str(p.username)} created with role ${str(p.role)}`;
    case "system.process.started":
      return `${str(p.component)} started (v${str(p.version)} on ${str(p.hostname)})`;
    case "system.process.stopped":
      return `${str(p.component)} stopped`;
    default:
      return e.type;
  }
}
