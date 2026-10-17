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

/// Ask macOS for a folder, using the system picker.
///
/// Implemented with AppleScript rather than a dialog plugin: it is the native
/// panel either way, and it keeps the dependency list short for one call.
/// Returns None when the user cancels, which is not an error.
#[tauri::command]
fn choose_folder() -> Result<Option<String>, String> {
    let script = r#"POSIX path of (choose folder with prompt "Choose where to save transcripts")"#;
    let output = Command::new("/usr/bin/osascript")
        .args(["-e", script])
        .output()
        .map_err(|e| e.to_string())?;

    if !output.status.success() {
        // Cancelling exits non-zero with "User canceled"; that is not a failure.
        let stderr = String::from_utf8_lossy(&output.stderr);
        if stderr.contains("-128") || stderr.to_lowercase().contains("cancel") {
            return Ok(None);
        }
        return Err(stderr.trim().to_string());
    }

    let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
    Ok(if path.is_empty() { None } else { Some(path) })
}

/// Reveal a path in Finder, so the user can check where transcripts went.
#[tauri::command]
fn reveal_in_finder(path: String) -> Result<(), String> {
    Command::new("/usr/bin/open")
        .args(["-R", &path])
        .spawn()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

/// Open System Settings directly at Privacy & Security > Screen Recording.
///
/// macOS requires this permission for ScreenCaptureKit even though the app
/// only reads audio. Telling a user to "find it in System Settings" is a poor
/// substitute for taking them there.
#[tauri::command]
fn open_screen_recording_settings() -> Result<(), String> {
    let url = "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture";
    Command::new("/usr/bin/open")
        .arg(url)
        .spawn()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

/// Restart the app.
///
/// The backend is handed the API key in its environment when it is spawned, so
/// a key added afterwards only takes effect on the next launch. Restarting is
/// clearer than appearing to accept a key that does nothing.
#[tauri::command]
fn relaunch(app: tauri::AppHandle) {
    kill_backend(&app);
    app.restart()
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

    // Packaged: the frozen backend shipped in Resources, which needs no
    // Python, no uv and no source checkout. Development: the uv environment,
    // located from the repo root rather than a relative path, because a
    // GUI-launched app does not inherit the shell's working directory.
    let bundled = resource_dir.join("jlt-backend");
    let mut command = if bundled.exists() {
        ensure_executable(&bundled);
        Command::new(bundled)
    } else {
        let repo = repo_root().ok_or("not running from a source checkout")?;
        let mut dev = Command::new("uv");
        dev.args(["run", "python", "-m", "jlt"]);
        dev.current_dir(repo.join("packages/backend"));
        dev
    };

    // A GUI-launched app gets a minimal PATH that excludes Homebrew and
    // rustup, so `uv` would not be found. Harmless for the frozen backend.
    if let Ok(path) = std::env::var("PATH") {
        command.env(
            "PATH",
            format!(
                "{path}:/opt/homebrew/bin:/usr/local/bin:{}/.local/bin",
                home_dir()
            ),
        );
    }

    // Point the backend at the bundled capture helper when there is one;
    // otherwise it falls back to its own discovery of the build directory.
    let helper = resource_dir.join("teams-audio-tap");
    if helper.exists() {
        ensure_executable(&helper);
        command.env("JLT_AUDIO_HELPER", &helper);
    }

    command.stdout(Stdio::piped()).stderr(Stdio::inherit());

    // Let the backend watch us. Killing it on quit (below) covers the normal
    // path, but a SIGKILL or a crash gives us no chance to run anything, and
    // an orphaned backend would keep capturing after the user closed the app.
    command.env("JLT_PARENT_PID", std::process::id().to_string());

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

/// Terminate the backend child, if one is still ours to terminate.
fn kill_backend(handle: &tauri::AppHandle) {
    let child = handle.state::<AppState>().child.lock().unwrap().take();
    if let Some(mut child) = child {
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn home_dir() -> String {
    std::env::var("HOME").unwrap_or_default()
}

/// Resource copying does not always preserve the executable bit, and a
/// backend that cannot be executed produces a baffling "spawn failed".
fn ensure_executable(path: &std::path::Path) {
    use std::os::unix::fs::PermissionsExt;
    if let Ok(metadata) = std::fs::metadata(path) {
        let mode = metadata.permissions().mode();
        if mode & 0o111 == 0 {
            let mut permissions = metadata.permissions();
            permissions.set_mode(mode | 0o755);
            let _ = std::fs::set_permissions(path, permissions);
        }
    }
}

/// Walk up from the executable looking for the monorepo root.
///
/// Used only in development. Counting parent directories breaks as soon as
/// the binary moves between `target/debug` and `target/release`.
fn repo_root() -> Option<std::path::PathBuf> {
    let exe = std::env::current_exe().ok()?;
    for ancestor in exe.ancestors() {
        if ancestor.join("packages/backend/pyproject.toml").is_file() {
            return Some(ancestor.to_path_buf());
        }
    }
    None
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
            open_screen_recording_settings,
            choose_folder,
            reveal_in_finder,
            relaunch,
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
                kill_backend(&window.app_handle().clone());
            }
        })
        .build(tauri::generate_context!())
        .expect("failed to start Japanese Live Translator")
        .run(|handle, event| {
            // Window Destroyed does not fire on every exit path — a SIGTERM
            // from the system, for instance. Exit does, so both are handled.
            if let tauri::RunEvent::Exit = event {
                kill_backend(handle);
            }
        });
}
