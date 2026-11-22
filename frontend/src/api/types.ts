// Mirrors the backend response models (src/api/*.py). Keep in sync when the API changes.

export type Role = "viewer" | "researcher" | "admin";

export interface Me {
  username: string;
  role: Role;
}

export interface AuditEvent {
  seq: number;
  id: string;
  type: string;
  type_version: number;
  occurred_at: string;
  recorded_at: string;
  actor_type: "user" | "agent" | "system";
  actor_id: string | null;
  aggregate_type: string | null;
  aggregate_id: string | null;
  correlation_id: string | null;
  causation_id: string | null;
  payload: Record<string, unknown>;
  hash: string;
}

export interface GpuDevice {
  index: number;
  name: string;
  memory_total_bytes: number;
  memory_used_bytes: number;
  utilization_percent: number | null;
  temperature_c: number | null;
  power_w: number | null;
}

export interface SystemStatus {
  status: "ok" | "degraded";
  version: string;
  env: string;
  started_at: string;
  uptime_s: number;
  resources: {
    sampled_at: string;
    hostname: string;
    cpu: {
      percent: number;
      logical_cores: number;
      physical_cores: number | null;
      load_avg_1m: number;
      load_avg_5m: number;
      load_avg_15m: number;
    };
    memory: {
      total_bytes: number;
      used_bytes: number;
      available_bytes: number;
      percent: number;
      swap_total_bytes: number;
      swap_used_bytes: number;
    };
    disks: {
      path: string;
      mountpoint: string;
      total_bytes: number;
      used_bytes: number;
      free_bytes: number;
      percent: number;
    }[];
    gpu: {
      status: "ok" | "unavailable";
      reason: string | null;
      driver_version: string | null;
      devices: GpuDevice[];
    };
  } | null;
  database: { reachable: boolean; migrations_current: boolean; current_revisions: string[] };
  audit: { total_events: number; last_seq: number | null; live_updates: "connected" | "reconnecting" };
  warnings: string[];
}

export interface User {
  id: string;
  username: string;
  role: Role;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
}
