interface Props {
  title: string;
  live: boolean;
}

/**
 * The window's drag handle.
 *
 * A strip of its own rather than relying on gaps in the control bar. Dragging
 * in Tauri works through `data-tauri-drag-region` on whatever element the
 * pointer actually hits, so a bar packed with buttons leaves almost nothing
 * to grab — which is exactly how the previous version behaved. (The
 * `-webkit-app-region: drag` CSS it also carried is an Electron API and does
 * nothing here at all.)
 *
 * It doubles as the space for the macOS traffic lights, so the control row
 * below no longer needs its awkward 78px of left padding.
 */
export function TitleBar({ title, live }: Props) {
  return (
    <div className="titlebar" data-tauri-drag-region>
      <span className="titlebar__title" data-tauri-drag-region>
        {live && <span className="titlebar__live" aria-hidden />}
        {title}
      </span>
    </div>
  );
}
