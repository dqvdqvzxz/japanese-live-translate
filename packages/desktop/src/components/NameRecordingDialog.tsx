import { useEffect, useRef, useState } from "react";

interface Props {
  defaultName: string;
  lines: number;
  onSave: (name: string) => void;
  onSkip: () => void;
}

/**
 * Asked once, right after Stop.
 *
 * Naming a recording is worth prompting for because the alternative — a list
 * of timestamps — is unsearchable a week later. But it must never block:
 * skipping keeps the generated name, and the name can be changed from the
 * history list at any time.
 */
export function NameRecordingDialog({ defaultName, lines, onSave, onSkip }: Props) {
  const [name, setName] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const commit = () => {
    const trimmed = name.trim();
    if (trimmed) onSave(trimmed);
    else onSkip();
  };

  return (
    <div className="modal" role="dialog" aria-modal="true" aria-labelledby="name-title">
      <div className="modal__card">
        <h2 id="name-title">Đặt tên cho bản ghi</h2>
        <p>
          {lines > 0
            ? `Phiên vừa kết thúc với ${lines} dòng đã dịch.`
            : "Phiên vừa kết thúc."}{" "}
          Đặt tên để sau này dễ tìm. Bỏ qua cũng được — hệ thống sẽ tự đặt, và
          bạn đổi lại lúc nào cũng được.
        </p>

        <input
          ref={inputRef}
          type="text"
          value={name}
          placeholder={defaultName}
          maxLength={120}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
            if (e.key === "Escape") onSkip();
          }}
          aria-label="Tên bản ghi"
        />

        <div className="modal__actions">
          <button className="btn btn--primary" onClick={commit} disabled={!name.trim()}>
            Lưu tên
          </button>
          <button className="btn" onClick={onSkip}>
            Bỏ qua
          </button>
        </div>
      </div>
    </div>
  );
}
