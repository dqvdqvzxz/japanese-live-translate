/**
 * Client for the local backend.
 *
 * The backend binds to 127.0.0.1 on an OS-assigned port and requires a bearer
 * token generated per process. Neither is known at build time: the Rust shell
 * learns them from the backend's handshake and hands them to the renderer.
 *
 * The renderer never sees the OpenAI API key. It only ever learns
 * `api_key_present`. See docs/security.md §2.
 */

import type { AudioSource, JltEvent, SessionStats, Usage } from "../types/events";
import { isJltEvent } from "../types/events";
import { backendInfo } from "./tauri";

export interface BackendInfo {
  port: number;
  token: string;
}

export interface SessionSummary {
  overview: string;
  action_items: string[];
  generated_at: number;
  model: string;
}

export interface SessionRecord {
  session_id: string;
  /** What the user called it. Empty means never named; fall back to the date. */
  name: string;
  started_at: number;
  ended_at: number | null;
  duration_s: number;
  source_name: string;
  segments_translated: number;
  segments_failed: number;
  audio_seconds_sent: number;
  usage: Usage;
  /** Projection from published unit prices, not a billed amount. */
  estimated_cost_usd: number;
  summary: SessionSummary | null;
  /** Where the readable transcript was written, when a folder is configured. */
  file_path?: string | null;
}

export interface StoredSegment {
  segment_id: string;
  seq: number;
  source_text: string;
  target_text: string | null;
  state: string;
}

export interface Settings {
  audio: { include_microphone: boolean };
  stt: { delay: string; languages: string[]; prompt: string | null };
  translation: Record<string, unknown>;
  segmentation: Record<string, unknown>;
  vad: Record<string, unknown>;
  ui: { theme: "system" | "light" | "dark"; font_size: number; always_on_top: boolean; show_partials: boolean };
  privacy: {
    persist_transcripts: boolean;
    /** Where readable transcript files go. Null means database only. */
    transcript_folder: string | null;
    persist_audio: boolean;
  };
  api_key_present: boolean;
}

/** An error the UI can act on, rather than a string to print. */
export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }

  static async from(response: Response): Promise<ApiError> {
    try {
      const body = (await response.json()) as { detail?: unknown };
      const detail = body.detail;
      if (detail && typeof detail === "object" && "code" in detail) {
        const { code, message } = detail as { code: string; message?: string };
        return new ApiError(code, message ?? code, response.status);
      }
      if (typeof detail === "string") {
        return new ApiError("error", detail, response.status);
      }
    } catch {
      // Fall through to the status line.
    }
    return new ApiError(
      "error",
      `${response.status} ${response.statusText}`,
      response.status,
    );
  }
}

export class BackendClient {
  private socket: WebSocket | null = null;
  private closedByUs = false;
  private reconnectDelayMs = 500;

  constructor(private readonly info: BackendInfo) {}

  private get base(): string {
    return `http://127.0.0.1:${this.info.port}`;
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(`${this.base}${path}`, {
      ...init,
      headers: {
        ...(init.headers ?? {}),
        Authorization: `Bearer ${this.info.token}`,
        ...(init.body ? { "Content-Type": "application/json" } : {}),
      },
    });
    if (!response.ok) {
      // The backend's error messages are content-free by design, so surfacing
      // them verbatim cannot leak meeting text. The code is what the UI
      // branches on — showing the raw JSON body to a user, as an earlier
      // version did, is not an error message.
      throw await ApiError.from(response);
    }
    return (await response.json()) as T;
  }

  listSources(): Promise<{ sources: AudioSource[] }> {
    return this.request("/api/audio/sources");
  }

  getSettings(): Promise<Settings> {
    return this.request("/api/settings");
  }

  patchSettings(patch: Record<string, unknown>): Promise<Settings> {
    return this.request("/api/settings", { method: "PATCH", body: JSON.stringify(patch) });
  }

  getSession(): Promise<{
    session_id: string | null;
    state: string;
    source: AudioSource | null;
  }> {
    return this.request("/api/session");
  }

  start(sourceId: string): Promise<{ session_id: string }> {
    return this.request("/api/session/start", {
      method: "POST",
      body: JSON.stringify({ source_id: sourceId }),
    });
  }

  pause(): Promise<{ state: string }> {
    return this.request("/api/session/pause", { method: "POST" });
  }

  resume(): Promise<{ state: string }> {
    return this.request("/api/session/resume", { method: "POST" });
  }

  stop(): Promise<{ state: string; stats: SessionStats }> {
    return this.request("/api/session/stop", { method: "POST" });
  }

  listSessions(): Promise<{ persisting: boolean; sessions: SessionRecord[] }> {
    return this.request("/api/history/sessions");
  }

  sessionDetail(id: string): Promise<{ session: SessionRecord; segments: StoredSegment[] }> {
    return this.request(`/api/history/sessions/${encodeURIComponent(id)}`);
  }

  summarize(id: string): Promise<SessionSummary> {
    return this.request(`/api/history/sessions/${encodeURIComponent(id)}/summary`, {
      method: "POST",
    });
  }

  deleteSession(id: string): Promise<{ status: string }> {
    return this.request(`/api/history/sessions/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
  }

  renameSession(id: string, name: string): Promise<SessionRecord> {
    return this.request(`/api/history/sessions/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    });
  }

  metrics(): Promise<SessionStats> {
    return this.request("/api/metrics");
  }

  /**
   * Subscribe to the event stream, reconnecting until `close()` is called.
   *
   * The token travels in the query string because a browser cannot set headers
   * on a WebSocket handshake. It is loopback-only and per-process; the threat
   * analysis is in docs/security.md §3.
   */
  connect(onEvent: (event: JltEvent) => void, onOpenChange?: (open: boolean) => void): void {
    this.closedByUs = false;
    const open = () => {
      const socket = new WebSocket(
        `ws://127.0.0.1:${this.info.port}/ws?token=${encodeURIComponent(this.info.token)}`,
      );
      this.socket = socket;

      socket.onopen = () => {
        this.reconnectDelayMs = 500;
        onOpenChange?.(true);
      };
      socket.onmessage = (message) => {
        let parsed: unknown;
        try {
          parsed = JSON.parse(String(message.data));
        } catch {
          return; // A frame we cannot parse is not worth crashing the UI over.
        }
        if (isJltEvent(parsed)) onEvent(parsed);
      };
      socket.onclose = () => {
        onOpenChange?.(false);
        if (this.closedByUs) return;
        window.setTimeout(open, this.reconnectDelayMs);
        this.reconnectDelayMs = Math.min(this.reconnectDelayMs * 2, 10_000);
      };
      socket.onerror = () => socket.close();
    };
    open();
  }

  close(): void {
    this.closedByUs = true;
    this.socket?.close();
    this.socket = null;
  }
}

/**
 * Discover the backend.
 *
 * Under the desktop shell the Rust side supplies the port and token. In a
 * plain browser there is no shell, so `VITE_JLT_BACKEND` can point at a
 * hand-started backend; failing that the UI falls back to a mock stream rather
 * than pretending to be connected.
 */
export async function resolveBackend(): Promise<BackendInfo | null> {
  const fromShell = await backendInfo();
  if (fromShell) return fromShell;

  const override = import.meta.env.VITE_JLT_BACKEND as string | undefined;
  if (override) {
    const [port, token] = override.split(":");
    if (port && token) return { port: Number(port), token };
  }
  return null;
}
