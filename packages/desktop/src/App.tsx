import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { BackendClient, resolveBackend, type Settings } from "./api/client";
import { startMockStream } from "./api/mock";
import { setAlwaysOnTop } from "./api/tauri";
import { Controls } from "./components/Controls";
import { HistoryPanel } from "./components/HistoryPanel";
import { SettingsPanel } from "./components/SettingsPanel";
import { StatusBar } from "./components/StatusBar";
import { SubtitleView } from "./components/SubtitleView";
import { initialState, subtitleReducer } from "./state/subtitles";
import type { AudioSource } from "./types/events";

const FALLBACK_SETTINGS: Settings = {
  stt: { delay: "low", languages: ["ja"], prompt: null },
  translation: {},
  segmentation: {},
  vad: {},
  ui: { theme: "system", font_size: 18, always_on_top: true, show_partials: true },
  privacy: { persist_transcripts: false, persist_audio: false },
  api_key_present: false,
};

type Panel = "none" | "settings" | "history";

export default function App() {
  const [state, dispatch] = useReducer(subtitleReducer, initialState);
  const [client, setClient] = useState<BackendClient | null>(null);
  const [mock, setMock] = useState(false);
  const [connected, setConnected] = useState(false);
  const [sources, setSources] = useState<AudioSource[]>([]);
  const [selected, setSelected] = useState("");
  const [settings, setSettings] = useState<Settings | null>(null);
  const [panel, setPanel] = useState<Panel>("none");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const stopMock = useRef<(() => void) | null>(null);

  // Connect to the backend, or fall back to the demo stream when there is no
  // Tauri shell to supply the port and token.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const info = await resolveBackend();
      if (cancelled) return;

      if (!info) {
        setMock(true);
        setSettings(FALLBACK_SETTINGS);
        setSources([
          { id: "app:com.microsoft.teams2", kind: "application",
            name: "Microsoft Teams (mock)", is_recommended: true },
        ]);
        setSelected("app:com.microsoft.teams2");
        stopMock.current = startMockStream(dispatch);
        return;
      }

      const backend = new BackendClient(info);
      backend.connect(dispatch, setConnected);
      setClient(backend);

      try {
        const [loaded, discovered, current] = await Promise.all([
          backend.getSettings(),
          backend.listSources(),
          backend.getSession(),
        ]);
        if (cancelled) return;
        setSettings(loaded);
        setSources(discovered.sources);

        // If a session is already running — the page was reloaded, or the
        // window reopened — adopt the source the backend is actually
        // capturing. Showing a different application than the one being sent
        // to OpenAI would break the guarantee in docs/security.md §7.
        const running = current.source?.id;
        setSelected(
          running ??
            discovered.sources.find((s) => s.is_recommended)?.id ??
            discovered.sources[0]?.id ??
            "",
        );
        if (current.session_id) {
          dispatch({
            type: "status",
            ts: Date.now() / 1000,
            session_id: current.session_id,
            capture: current.state as never,
            stt: "connected",
            translation: "ready",
            audio_level_dbfs: null,
            queue_depth: 0,
            detail: null,
          });
        }
      } catch (error) {
        if (!cancelled) setActionError(String(error));
      }
    })();

    return () => {
      cancelled = true;
      stopMock.current?.();
      client?.close();
    };
    // Intentionally runs once: re-running would restart the stream.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Theme. "system" means follow the OS, which is the default.
  const theme = settings?.ui.theme ?? "system";
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") {
      root.removeAttribute("data-theme");
    } else {
      root.setAttribute("data-theme", theme);
    }
  }, [theme]);

  const run = useCallback(
    async (action: () => Promise<unknown>) => {
      setBusy(true);
      setActionError(null);
      try {
        await action();
      } catch (error) {
        setActionError(String(error));
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const patchSettings = useCallback(
    (patch: Record<string, unknown>) => {
      setSettings((current) => (current ? deepMerge(current, patch) : current));
      if (client) void run(() => client.patchSettings(patch));
    },
    [client, run],
  );

  const toggleAlwaysOnTop = useCallback(() => {
    const next = !(settings?.ui.always_on_top ?? true);
    patchSettings({ ui: { always_on_top: next } });
    void setAlwaysOnTop(next);
  }, [settings, patchSettings]);

  const fontSize = settings?.ui.font_size ?? 18;
  const showPartials = settings?.ui.show_partials ?? true;
  const banner = state.fatal ?? actionError ?? state.warning;
  const bannerKind = state.fatal || actionError ? "fatal" : "warning";

  const content = useMemo(
    () => (
      <SubtitleView
        lines={state.lines}
        partial={state.partial}
        fontSize={fontSize}
        showPartials={showPartials}
        capture={state.capture}
      />
    ),
    [state.lines, state.partial, fontSize, showPartials, state.capture],
  );

  return (
    <div className="app">
      <Controls
        sources={sources}
        selected={selected}
        onSelect={setSelected}
        capture={state.capture}
        busy={busy || mock}
        alwaysOnTop={settings?.ui.always_on_top ?? true}
        onStart={() => client && run(() => client.start(selected))}
        onStop={() => client && run(() => client.stop())}
        onPause={() => client && run(() => client.pause())}
        onResume={() => client && run(() => client.resume())}
        onToggleAlwaysOnTop={toggleAlwaysOnTop}
        onOpenSettings={() => setPanel(panel === "settings" ? "none" : "settings")}
      />

      {banner && (
        <div className={`banner banner--${bannerKind}`} role="alert">
          {banner}
        </div>
      )}

      <main className="app__body">
        {content}
        {panel === "settings" && (
          <SettingsPanel
            settings={settings}
            onPatch={patchSettings}
            onClose={() => setPanel("none")}
            onClearHistory={() => dispatch({
              type: "session.started", ts: Date.now() / 1000,
              session_id: state.sessionId ?? "s_cleared",
              source: { id: selected, kind: "application", name: "cleared" },
            })}
          />
        )}
        {panel === "history" && (
          <HistoryPanel lines={state.lines} stats={state.stats} onClose={() => setPanel("none")} />
        )}
      </main>

      <button
        className="app__history-toggle btn btn--icon"
        onClick={() => setPanel(panel === "history" ? "none" : "history")}
      >
        History
      </button>

      <StatusBar state={state} connected={connected} mock={mock} />
    </div>
  );
}

/** Shallow-per-section merge, which matches the shape of a settings patch. */
function deepMerge(base: Settings, patch: Record<string, unknown>): Settings {
  const next = { ...base } as Record<string, unknown>;
  for (const [key, value] of Object.entries(patch)) {
    const current = next[key];
    next[key] =
      current && typeof current === "object" && value && typeof value === "object"
        ? { ...(current as object), ...(value as object) }
        : value;
  }
  return next as unknown as Settings;
}
