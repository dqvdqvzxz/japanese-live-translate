/**
 * The bridge to the native shell.
 *
 * Imported from `@tauri-apps/api` rather than read off `window.__TAURI__`:
 * that global only exists when `withGlobalTauri` is enabled, and relying on it
 * silently turned the packaged app into a demo — `resolveBackend` found no
 * bridge, so the UI fell back to the mock event stream instead of connecting
 * to the real backend.
 *
 * Every function here is safe to call in a plain browser: `isDesktop()` is
 * false and the rest become no-ops, so `npm run dev` still works without a
 * shell.
 */

import { invoke } from "@tauri-apps/api/core";

import type { BackendInfo } from "./client";

export function isDesktop(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export async function backendInfo(): Promise<BackendInfo | null> {
  if (!isDesktop()) return null;
  try {
    return await invoke<BackendInfo>("backend_info");
  } catch {
    return null;
  }
}

/** Stores the key in the macOS Keychain. It never reaches the backend over HTTP. */
export async function storeApiKey(key: string): Promise<void> {
  await invoke("store_api_key", { key });
}

export async function clearApiKey(): Promise<void> {
  await invoke("clear_api_key");
}

export async function hasApiKey(): Promise<boolean> {
  if (!isDesktop()) return false;
  return invoke<boolean>("has_api_key");
}

export async function setAlwaysOnTop(enabled: boolean): Promise<void> {
  if (!isDesktop()) return;
  await invoke("set_always_on_top", { enabled });
}

/** Opens System Settings at Privacy & Security > Screen Recording. */
export async function openScreenRecordingSettings(): Promise<void> {
  await invoke("open_screen_recording_settings");
}

/**
 * Restart the app.
 *
 * The backend receives the API key in its environment when it is spawned, so a
 * key added after startup only takes effect on the next launch. Restarting is
 * clearer than silently doing nothing until the user quits.
 */
export async function relaunch(): Promise<void> {
  await invoke("relaunch");
}
