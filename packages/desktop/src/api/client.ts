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

import type { AudioSource, JltEvent, SessionStats } from "../types/events";
import { isJltEvent } from "../types/events";

export interface BackendInfo {
  port: number;
  token: string;
}

export interface Settings {
  stt: { delay: string; languages: string[]; prompt: string | null };
  translation: Record<string, unknown>;
  segmentation: Record<string, unknown>;
  vad: Record<string, unknown>;
  ui: { theme: "system" | "light" | "dark"; font_size: number; always_on_top: boolean; show_partials: boolean };
  privacy: { persist_transcripts: boolean; persist_audio: boolean };
  api_key_present: boolean;
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
      // them verbatim cannot leak meeting text.
      const detail = await response.text().catch(() => "");
      throw new Error(detail || `${response.status} ${response.statusText}`);
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
 * Under Tauri the Rust shell supplies the port and token. In a plain browser
 * (`npm run dev`) there is no shell, so the UI falls back to a mock stream
 * rather than pretending to be connected.
 */
export async function resolveBackend(): Promise<BackendInfo | null> {
  const tauri = (window as unknown as {
    __TAURI__?: { core?: { invoke(cmd: string): Promise<BackendInfo> } };
  }).__TAURI__;
  if (tauri?.core?.invoke) {
    try {
      return await tauri.core.invoke("backend_info");
    } catch {
      return null;
    }
  }
  const override = import.meta.env.VITE_JLT_BACKEND as string | undefined;
  if (override) {
    const [port, token] = override.split(":");
    if (port && token) return { port: Number(port), token };
  }
  return null;
}
