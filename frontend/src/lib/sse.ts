// Server-Sent Events over fetch.
//
// Why not the browser's EventSource? It only does GET and can't send an Authorization
// header. Our chat stream is a POST with a bearer token, so we use
// @microsoft/fetch-event-source, which parses the SSE format from a fetch() body.
//
// Safety detail: that library RETRIES by default (on network errors and when the tab is
// hidden). Retrying POST /api/chat/stream would run the same turn twice, so here every
// error is fatal (onerror rethrows) and openWhenHidden keeps the stream open in background.

import { EventStreamContentType, fetchEventSource } from "@microsoft/fetch-event-source";

import { ApiError, toApiError } from "./api";
import { API_URL } from "./config";
import { type ChatEvent, TERMINAL_EVENTS } from "./types";

export interface StreamOptions {
  path: string;
  token: string;
  method?: "GET" | "POST";
  body?: unknown;
  signal?: AbortSignal;
  onEvent: (event: ChatEvent) => void;
}

/** Resolves when the server ends the stream after a terminal event; rejects otherwise. */
export async function streamEvents(opts: StreamOptions): Promise<void> {
  let sawTerminal = false;
  await fetchEventSource(`${API_URL}${opts.path}`, {
    method: opts.method ?? "POST",
    headers: {
      Authorization: `Bearer ${opts.token}`,
      Accept: EventStreamContentType,
      ...(opts.body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    signal: opts.signal,
    openWhenHidden: true,

    async onopen(res) {
      const type = res.headers.get("content-type") ?? "";
      if (res.ok && type.startsWith(EventStreamContentType)) return;
      throw await toApiError(res); // 401 / 404 / 409 / 422 / 429 come back as JSON
    },

    onmessage(msg) {
      // Keep-alive comments (": keep-alive") arrive as empty messages: ignore them.
      if (!msg.event) return;
      let data: unknown = {};
      try {
        data = msg.data ? JSON.parse(msg.data) : {};
      } catch {
        return; // never act on unparseable data
      }
      if (TERMINAL_EVENTS.has(msg.event)) sawTerminal = true;
      opts.onEvent({ type: msg.event, data } as ChatEvent);
    },

    onclose() {
      if (!sawTerminal) throw new ApiError(0, "stream closed before the turn finished");
    },

    onerror(err) {
      throw err instanceof ApiError ? err : new ApiError(0, "network"); // no auto-retry
    },
  });
}
