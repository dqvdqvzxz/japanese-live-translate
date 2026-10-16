// Japanese Live Translator — Tauri shell.
//
// Three jobs, all of which exist for security reasons (docs/security.md):
//
//  1. Own the OpenAI API key. It is read from and written to the macOS
//     Keychain here and handed to the backend child through its environment —
//     never through argv, which `ps` exposes to any local process, and never
//     to the renderer, which only learns whether a key exists.
//  2. Supervise the Python backend: spawn it, read its one-line handshake from
//     stdout, and make sure it dies when this process does.
//  3. Expose the few native things the renderer needs: the backend's port and
//     token, and the always-on-top toggle.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use tauri::{Manager, State};

const KEYCHAIN_SERVICE: &str = "com.jlt.japanese-live-translator";
const KEYCHAIN_ACCOUNT: &str = "openai_api_key";

#[derive(Clone, Debug, Serialize, Deserialize)]
struct BackendInfo {
    port: u16,
    token: String,
}

#[derive(Debug, Deserialize)]
struct Handshake {
    #[serde(rename = "type")]
    kind: String,
    port: u16,
    token: String,
}

struct AppState {
    backend: Mutex<Option<BackendInfo>>,
    child: Mutex<Option<Child>>,
}

fn keychain_entry() -> Result<keyring::Entry, String> {
    keyring::Entry::new(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT).map_err(|e| e.to_string())
}

fn read_api_key() -> Option<String> {
    keychain_entry().ok()?.get_password().ok()
}

/// The renderer asks for this once at startup. It is the port and token only —
/// never the API key.
#[tauri::command]
fn backend_info(state: State<'_, AppState>) -> Result<BackendInfo, String> {
    state
        .backend
        .lock()
        .map_err(|_| "state poisoned".to_string())?
        .clone()
        .ok_or_else(|| "the backend is not running".to_string())
}

/// Store a key the user typed. It goes straight to the Keychain and is not
/// echoed back, logged, or kept in memory beyond this call.
#[tauri::command]
fn store_api_key(key: String) -> Result<bool, String> {
    let trimmed = key.trim();
    if trimmed.is_empty() {
        return Err("the key is empty".into());
    }
    keychain_entry()?
        .set_password(trimmed)
        .map_err(|e| e.to_string())?;
    Ok(true)
}

#[tauri::command]
fn has_api_key() -> bool {
    read_api_key().is_some()
}

#[tauri::command]
fn clear_api_key() -> Result<bool, String> {
    match keychain_entry()?.delete_credential() {
        Ok(()) => Ok(true),
        // Deleting a key that is not there is the state the caller wanted.
        Err(keyring::Error::NoEntry) => Ok(true),
        Err(e) => Err(e.to_string()),
    }
}

#[tauri::command]
fn set_always_on_top(window: tauri::Window, enabled: bool) -> Result<(), String> {
    window.set_always_on_top(enabled).map_err(|e| e.to_string())
}

/// Spawn the backend and wait for its single handshake line.
///
/// The backend prints exactly one JSON object to stdout and everything else to
/// stderr, so a line read here is unambiguous.
fn spawn_backend(app: &tauri::App) -> Result<(Child, BackendInfo), String> {
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|e| format!("no resource dir: {e}"))?;

    // Packaged: a bundled executable. Development: the uv-managed environment.
    let bundled = resource_dir.join("jlt-backend");
    let mut command = if bundled.exists() {
        Command::new(bundled)
    } else {
        let mut dev = Command::new("uv");
        dev.args(["run", "python", "-m", "jlt"]);
        dev.current_dir("../../backend");
        dev
    };

    command.stdout(Stdio::piped()).stderr(Stdio::inherit());

    // The key reaches the child through its environment, never through argv.
    if let Some(key) = read_api_key() {
        command.env("OPENAI_API_KEY", key);
    }

    let mut child = command.spawn().map_err(|e| format!("spawn failed: {e}"))?;
    let stdout = child.stdout.take().ok_or("backend produced no stdout")?;

    let mut line = String::new();
    BufReader::new(stdout)
        .read_line(&mut line)
        .map_err(|e| format!("handshake read failed: {e}"))?;

    let handshake: Handshake =
        serde_json::from_str(line.trim()).map_err(|e| format!("bad handshake: {e}"))?;
    if handshake.kind != "listening" {
        return Err(format!("unexpected handshake: {}", handshake.kind));
    }

    Ok((
        child,
        BackendInfo {
            port: handshake.port,
            token: handshake.token,
        },
    ))
}

fn main() {
    tauri::Builder::default()
        .manage(AppState {
            backend: Mutex::new(None),
            child: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![
            backend_info,
            store_api_key,
            has_api_key,
            clear_api_key,
            set_always_on_top,
        ])
        .setup(|app| {
            match spawn_backend(app) {
                Ok((child, info)) => {
                    let state = app.state::<AppState>();
                    *state.backend.lock().unwrap() = Some(info);
                    *state.child.lock().unwrap() = Some(child);
                }
                Err(error) => {
                    // The window still opens so the user can read the error
                    // rather than facing a process that silently never appears.
                    eprintln!("backend failed to start: {error}");
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                // Never leave an orphaned backend, which would keep the audio
                // helper alive and keep capturing.
                //
                // The handle is cloned rather than borrowed from `window`:
                // `State` borrows its owner, and the window reference here does
                // not outlive the lock guard.
                let handle = window.app_handle().clone();
                let child = handle.state::<AppState>().child.lock().unwrap().take();
                if let Some(mut child) = child {
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("failed to start Japanese Live Translator");
}
