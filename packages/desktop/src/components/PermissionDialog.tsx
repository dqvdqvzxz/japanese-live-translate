import { useState } from "react";
import { isDesktop, openScreenRecordingSettings, relaunch } from "../api/tauri";

interface Props {
  message: string;
  onRetry: () => void;
  onDismiss: () => void;
}

/**
 * Shown when macOS has not granted Screen Recording.
 *
 * This is the first thing most users will hit, and it is not something they
 * can fix inside the app — so the dialog takes them to the exact System
 * Settings pane rather than describing where to find it. macOS does not apply
 * the change to a running process, so a restart button sits beside it; without
 * one the user grants permission, returns, and finds nothing has changed.
 */
export function PermissionDialog({ message, onRetry, onDismiss }: Props) {
  const [opened, setOpened] = useState(false);

  return (
    <div className="modal" role="dialog" aria-modal="true" aria-labelledby="perm-title">
      <div className="modal__card">
        <h2 id="perm-title">Screen Recording permission needed</h2>

        <p>
          macOS requires Screen Recording permission to capture audio from
          another application. This app only ever reads audio — it never
          captures a single video frame — but macOS offers no narrower
          permission.
        </p>

        {opened ? (
          <ol className="modal__steps">
            <li>Find <strong>Japanese Live Translator</strong> in the list.</li>
            <li>Turn the switch on.</li>
            <li>Come back and press <strong>Restart app</strong>.</li>
          </ol>
        ) : (
          <p className="modal__detail">{message}</p>
        )}

        <div className="modal__actions">
          {isDesktop() ? (
            <>
              <button
                className="btn btn--primary"
                onClick={async () => {
                  setOpened(true);
                  try {
                    await openScreenRecordingSettings();
                  } catch {
                    // The pane could not be opened; the written steps remain.
                  }
                }}
              >
                Open System Settings
              </button>
              {opened && (
                <button className="btn" onClick={() => void relaunch()}>
                  Restart app
                </button>
              )}
            </>
          ) : (
            <p className="modal__detail">
              System Settings → Privacy &amp; Security → Screen Recording
            </p>
          )}
          <button className="btn" onClick={onRetry}>
            Try again
          </button>
          <button className="btn" onClick={onDismiss}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
