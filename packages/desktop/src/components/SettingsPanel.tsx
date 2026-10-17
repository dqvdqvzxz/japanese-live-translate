import { useState } from "react";
import type { Settings } from "../api/client";
import { chooseFolder, clearApiKey, isDesktop, relaunch, revealInFinder, storeApiKey } from "../api/tauri";
import { InfoSection } from "./InfoSection";

interface Props {
  settings: Settings | null;
  onPatch: (patch: Record<string, unknown>) => void;
  onClose: () => void;
  onClearHistory: () => void;
}

const DELAYS = ["minimal", "low", "medium", "high", "xhigh"] as const;

/**
 * Settings.
 *
 * The API key is conspicuously absent: it lives in the macOS Keychain and the
 * renderer only ever learns whether one is present. Entering it is a Tauri
 * command handled on the Rust side (docs/security.md §2).
 */
export function SettingsPanel({ settings, onPatch, onClose, onClearHistory }: Props) {
  const [confirmPersist, setConfirmPersist] = useState(false);
  const [keyInput, setKeyInput] = useState("");
  const [keyState, setKeyState] = useState<"idle" | "saving" | "saved" | "removed">("idle");
  const [keyError, setKeyError] = useState<string | null>(null);
  const [replacingKey, setReplacingKey] = useState(false);
  const [folderError, setFolderError] = useState<string | null>(null);

  const pickFolder = async () => {
    setFolderError(null);
    try {
      const folder = await chooseFolder();
      // A cancelled picker returns null, which must not wipe the current
      // choice — cancelling means "leave it alone", not "clear it".
      if (folder) onPatch({ privacy: { transcript_folder: folder } });
    } catch (error) {
      setFolderError(String(error));
    }
  };

  if (!settings) return null;

  const ui = settings.ui;
  const privacy = settings.privacy;

  return (
    <div className="panel" role="dialog" aria-label="Settings">
      <div className="panel__head">
        <h2>Settings</h2>
        <button className="btn btn--icon" onClick={onClose} aria-label="Close settings">
          ×
        </button>
      </div>

      <section className="panel__section">
        <h3>Appearance</h3>
        <label>
          Theme
          <select
            value={ui.theme}
            onChange={(e) => onPatch({ ui: { theme: e.target.value } })}
          >
            <option value="system">Follow system</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </select>
        </label>
        <label>
          Subtitle size: {ui.font_size}px
          <input
            type="range"
            min={12}
            max={40}
            value={ui.font_size}
            onChange={(e) => onPatch({ ui: { font_size: Number(e.target.value) } })}
          />
        </label>
        <label className="panel__check">
          <input
            type="checkbox"
            checked={ui.show_partials}
            onChange={(e) => onPatch({ ui: { show_partials: e.target.checked } })}
          />
          Show Japanese as it is being spoken
        </label>
      </section>

      <section className="panel__section">
        <h3>Transcription</h3>
        <label>
          Latency / accuracy
          <select
            value={String(settings.stt.delay)}
            onChange={(e) => onPatch({ stt: { delay: e.target.value } })}
          >
            {DELAYS.map((delay) => (
              <option key={delay} value={delay}>
                {delay}
              </option>
            ))}
          </select>
        </label>
        <p className="panel__hint">
          Lower settings show text sooner; higher settings give the model more
          audio before it commits, which can improve accuracy.
        </p>
      </section>

      <section className="panel__section">
        <h3>Privacy</h3>
        <p className="panel__hint">
          Audio and transcripts are sent to OpenAI to be processed. Nothing is
          saved on this Mac unless you turn it on below.
        </p>
        <label className="panel__check">
          <input
            type="checkbox"
            checked={privacy.persist_transcripts}
            onChange={(e) => {
              if (e.target.checked && !confirmPersist) {
                setConfirmPersist(true);
                return;
              }
              setConfirmPersist(false);
              onPatch({ privacy: { persist_transcripts: e.target.checked } });
            }}
          />
          Save transcripts to this Mac
        </label>

        {privacy.persist_transcripts && (
          <div className="panel__folder">
            <p className="panel__hint">
              Thư mục lưu transcript. Mỗi phiên là một tệp Markdown, đặt theo tên
              bản ghi.
            </p>
            {privacy.transcript_folder ? (
              <code className="panel__path" title={privacy.transcript_folder}>
                {privacy.transcript_folder}
              </code>
            ) : (
              <p className="panel__warn">
                Chưa chọn thư mục — hiện chỉ lưu vào cơ sở dữ liệu nội bộ của app.
              </p>
            )}

            {/* The picker is the macOS system panel, so it exists only in the
                app. Showing a button here that silently does nothing in a
                browser is worse than saying why it is unavailable. */}
            {isDesktop() ? (
              <div className="panel__row">
                <button
                  className={`btn btn--small ${privacy.transcript_folder ? "" : "btn--primary"}`}
                  onClick={pickFolder}
                >
                  {privacy.transcript_folder ? "Đổi thư mục" : "Chọn thư mục…"}
                </button>
                {privacy.transcript_folder && (
                  <button
                    className="btn btn--small"
                    onClick={() => void revealInFinder(privacy.transcript_folder!)}
                  >
                    Mở trong Finder
                  </button>
                )}
              </div>
            ) : (
              <p className="panel__hint">
                Chọn thư mục chỉ làm được trong ứng dụng desktop — trình duyệt
                không mở được hộp thoại chọn thư mục của macOS.
              </p>
            )}

            {folderError && <p className="panel__error">{folderError}</p>}
          </div>
        )}
        {confirmPersist && (
          <div className="panel__confirm">
            This writes meeting content to disk in your Application Support
            folder. Turn it on?
            <div>
              <button
                className="btn btn--primary"
                onClick={() => {
                  setConfirmPersist(false);
                  onPatch({ privacy: { persist_transcripts: true } });
                }}
              >
                Turn on
              </button>
              <button className="btn" onClick={() => setConfirmPersist(false)}>
                Cancel
              </button>
            </div>
          </div>
        )}
        <button className="btn" onClick={onClearHistory}>
          Delete all history
        </button>
      </section>

      <section className="panel__section">
        <h3>OpenAI API key</h3>
        <p className={settings.api_key_present ? "panel__ok" : "panel__warn"}>
          {settings.api_key_present ? (
            <>
              <span className="panel__tick" aria-hidden>
                ✓
              </span>{" "}
              Đã cấu hình API key (lưu trong macOS Keychain).
            </>
          ) : (
            "Chưa có API key. Không thể bắt đầu thu âm."
          )}
        </p>

        {isDesktop() ? (
          <>
            {settings.api_key_present && !replacingKey ? (
              <button className="btn btn--small" onClick={() => setReplacingKey(true)}>
                Thay khoá khác
              </button>
            ) : (
            <>
            <label>
              {settings.api_key_present ? "Khoá mới" : "Paste your key"}
              <input
                type="password"
                value={keyInput}
                placeholder="sk-…"
                autoComplete="off"
                spellCheck={false}
                onChange={(e) => {
                  setKeyInput(e.target.value);
                  setKeyState("idle");
                }}
              />
            </label>
            <p className="panel__hint">
              The key goes straight to your Keychain. It is never written to a
              file and never reaches this window again.
            </p>
            <div className="panel__row">
              <button
                className="btn btn--primary"
                disabled={!keyInput.trim() || keyState === "saving"}
                onClick={async () => {
                  setKeyState("saving");
                  try {
                    await storeApiKey(keyInput.trim());
                    // Overwrite the copy held in this component immediately.
                    setKeyInput("");
                    setKeyState("saved");
                    setReplacingKey(false);
                  } catch (error) {
                    setKeyError(String(error));
                    setKeyState("idle");
                  }
                }}
              >
                Save to Keychain
              </button>
              {settings.api_key_present && (
                <button
                  className="btn"
                  onClick={async () => {
                    try {
                      await clearApiKey();
                      setKeyState("removed");
                    } catch (error) {
                      setKeyError(String(error));
                    }
                  }}
                >
                  Remove
                </button>
              )}
            </div>

            {settings.api_key_present && (
              <button className="btn btn--small" onClick={() => setReplacingKey(false)}>
                Huỷ
              </button>
            )}
            </>
            )}

            {keyError && <p className="panel__error">{keyError}</p>}

            {(keyState === "saved" || keyState === "removed") && (
              <div className="panel__confirm">
                {keyState === "saved" ? "Key saved." : "Key removed."} The
                backend is given the key when it starts, so this takes effect
                after a restart.
                <div>
                  <button className="btn btn--primary" onClick={() => void relaunch()}>
                    Restart now
                  </button>
                  <button className="btn" onClick={() => setKeyState("idle")}>
                    Later
                  </button>
                </div>
              </div>
            )}
          </>
        ) : (
          <p className="panel__hint">
            Đang chạy trong trình duyệt. Khoá chỉ nhập được trong ứng dụng
            desktop, vì chỉ ứng dụng mới ghi được vào macOS Keychain.
          </p>
        )}
      </section>
      <InfoSection />
    </div>
  );
}
