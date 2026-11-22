import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, api, post } from "./client";
import type { AuditEvent, Me, Role, SystemStatus, User } from "./types";

export const keys = {
  me: ["me"] as const,
  status: ["system-status"] as const,
  latestEvents: ["events", "latest"] as const,
  users: ["users"] as const,
};

/** Current user, or null when not logged in (401 is an expected state, not an error). */
export function useMe() {
  return useQuery({
    queryKey: keys.me,
    queryFn: async (): Promise<Me | null> => {
      try {
        return await api<Me>("/api/auth/me");
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: 60_000,
  });
}

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { username: string; password: string }) => post<Me>("/api/auth/login", body),
    onSuccess: (me) => qc.setQueryData(keys.me, me),
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => post<undefined>("/api/auth/logout"),
    onSettled: () => {
      qc.clear();
      qc.setQueryData(keys.me, null);
    },
  });
}

export function useSystemStatus() {
  return useQuery({
    queryKey: keys.status,
    queryFn: () => api<SystemStatus>("/api/system/status"),
    refetchInterval: 5_000,
  });
}

export function useLatestEvents(limit = 50) {
  return useQuery({
    queryKey: [...keys.latestEvents, limit],
    queryFn: () => api<AuditEvent[]>(`/api/events/latest?limit=${limit}`),
    staleTime: Infinity, // the live stream keeps the list current after the first load
  });
}

export function useUsers(enabled: boolean) {
  return useQuery({ queryKey: keys.users, queryFn: () => api<User[]>("/api/users"), enabled });
}

export function useCreateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { username: string; password: string; role: Role }) =>
      post<User>("/api/users", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.users }),
  });
}
