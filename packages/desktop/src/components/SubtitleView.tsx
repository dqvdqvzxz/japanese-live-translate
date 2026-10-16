import { useEffect, useRef } from "react";
import type { LivePartial, SubtitleLine } from "../state/subtitles";

interface Props {
  lines: SubtitleLine[];
  partial: LivePartial | null;
  fontSize: number;
  showPartials: boolean;
}

const FAILURE_TEXT: Record<string, string> = {
  timeout: "Translation timed out",
  api_error: "Translation failed",
  cancelled: "Translation cancelled",
  queue_full: "Skipped — too many lines at once",
};

/**
 * The subtitle track.
 *
 * Vietnamese sits directly beneath its own Japanese so the pairing is never
 * ambiguous. Japanese stays on screen when a translation fails, because a
 * reader who knows some Japanese is better served by the source than by a gap.
 *
 * Auto-scroll follows the newest line, but only while the user is already at
 * the bottom: yanking the view away from someone reading back is worse than
 * making them scroll down again.
 */
export function SubtitleView({ lines, partial, fontSize, showPartials }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const pinnedToBottom = useRef(true);

  const handleScroll = () => {
    const element = containerRef.current;
    if (!element) return;
    const distance = element.scrollHeight - element.scrollTop - element.clientHeight;
    pinnedToBottom.current = distance < 48;
  };

  useEffect(() => {
    if (!pinnedToBottom.current) return;
    const element = containerRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [lines, partial]);

  const empty = lines.length === 0 && !partial;

  return (
    <div
      className="subtitles"
      ref={containerRef}
      onScroll={handleScroll}
      style={{ fontSize: `${fontSize}px` }}
      role="log"
      aria-live="polite"
      aria-label="Subtitles"
    >
      {empty && (
        <p className="placeholder">
          Choose the application playing the meeting audio, then press Start.
        </p>
      )}

      {lines.map((line) => (
        <article key={line.key} className={`line line--${line.state}`}>
          <p className="line__ja" lang="ja">
            {line.japanese}
          </p>
          {line.vietnamese && (
            <p className="line__vi" lang="vi">
              {line.vietnamese}
            </p>
          )}
          {line.state === "translating" && (
            <p className="line__pending" aria-label="Translating">
              …
            </p>
          )}
          {line.state === "failed" && (
            <p className="line__error">
              {FAILURE_TEXT[line.failureReason ?? ""] ?? "Translation failed"}
            </p>
          )}
        </article>
      ))}

      {showPartials && partial && (
        <article className="line line--partial" aria-label="In progress">
          <p className="line__ja" lang="ja">
            {partial.text}
          </p>
        </article>
      )}
    </div>
  );
}
