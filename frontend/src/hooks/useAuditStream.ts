import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { keys } from "../api/hooks";
import type { AuditEvent } from "../api/types";

export type StreamState = "connecting" | "live" | "reconnecting";

const MAX_EVENTS = 200;

/**
 * Live audit events via Server-Sent Events, layered on top of an initial newest-first list.
 * The stream starts after the newest initial event; the browser reconnects by itself and
 * resumes with Last-Event-ID, so nothing is missed or duplicated.
 */
export function useAuditStream(initial: AuditEvent[] | undefined) {
  const qc = useQueryClient();
  const [live, setLive] = useState<AuditEvent[]>([]);
  const [state, setState] = useState<StreamState>("connecting");
  const ready = initial !== undefined;
  const afterSeq = initial?.[0]?.seq ?? 0;

  useEffect(() => {
    if (!ready) return;
    const source = new EventSource(`/api/events/stream?after=${afterSeq}`);
    source.onopen = () => setState("live");
    source.onerror = () => setState("reconnecting");
    source.addEventListener("audit", (message: MessageEvent<string>) => {
      const event = JSON.parse(message.data) as AuditEvent;
      setLive((current) => [event, ...current].slice(0, MAX_EVENTS));
    });
    source.addEventListener("session_ended", () => {
      source.close();
      qc.setQueryData(keys.me, null); // the route guard redirects to the login page
    });
    return () => source.close();
  }, [ready, afterSeq, qc]);

  const events = useMemo(() => {
    const seen = new Set<number>();
    return [...live, ...(initial ?? [])]
      .filter((e) => (seen.has(e.seq) ? false : (seen.add(e.seq), true)))
      .slice(0, MAX_EVENTS);
  }, [live, initial]);

  return { events, state };
}
