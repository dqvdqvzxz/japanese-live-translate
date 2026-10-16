/**
 * A scripted event stream for developing and demonstrating the UI without a
 * backend, a meeting, or an API key.
 *
 * It replays a realistic Japanese IT stand-up with the timing characteristics
 * actually measured in `docs/benchmarks.md`: partials about a second after
 * speech starts, finals shortly after, translations roughly a second later and
 * occasionally out of order. It includes a reconnect warning and one failed
 * translation, because the UI has to look right when things go wrong too.
 */

import type { JltEvent } from "../types/events";

interface Line {
  ja: string;
  vi: string;
  /** Simulate a failed translation for this line. */
  fail?: boolean;
}

const SCRIPT: Line[] = [
  { ja: "おはようございます。朝会を始めます。", vi: "Chào buổi sáng. Chúng ta bắt đầu họp sáng." },
  { ja: "来週のリリースは延期されません。", vi: "Bản phát hành tuần tới sẽ không bị hoãn." },
  { ja: "チケットABC-1234のステータスを確認してください。", vi: "Vui lòng kiểm tra status của ticket ABC-1234." },
  { ja: "レビューが終われば、今日中にマージできます。", vi: "Nếu review xong thì có thể merge trong hôm nay." },
  { ja: "ステージング環境でロールバックを試しましたが、失敗しました。", vi: "Chúng tôi đã thử rollback trong môi trường staging, nhưng đã thất bại.", fail: true },
  { ja: "レスポンスタイムが250ミリ秒から1.8秒に悪化しました。", vi: "Response time đã xấu đi từ 250 milliseconds lên 1.8 seconds." },
  { ja: "本番環境にはデプロイしないでください。", vi: "Xin đừng deploy lên môi trường production." },
];

const SESSION = "s_mock";
const now = () => Date.now() / 1000;

export function startMockStream(onEvent: (event: JltEvent) => void): () => void {
  const timers: number[] = [];
  const at = (ms: number, make: () => JltEvent) => {
    timers.push(window.setTimeout(() => onEvent(make()), ms));
  };

  at(0, () => ({
    type: "session.started", ts: now(), session_id: SESSION,
    source: { id: "app:com.microsoft.teams2", kind: "application",
      name: "Microsoft Teams (mock)", bundle_id: "com.microsoft.teams2", is_recommended: true },
  }));

  let clock = 400;
  SCRIPT.forEach((line, index) => {
    const seq = index + 1;
    const segmentId = `seg_${seq}`;
    const start = clock;

    // Partials stream in as the sentence is spoken.
    const steps = Math.max(2, Math.ceil(line.ja.length / 5));
    for (let step = 1; step <= steps; step += 1) {
      const text = line.ja.slice(0, Math.ceil((line.ja.length * step) / steps));
      at(start + step * 160, () => ({
        type: "transcript.partial", ts: now(), session_id: SESSION,
        item_id: `item_${seq}`, seq, text,
      }));
    }

    const finalAt = start + steps * 160 + 300;
    at(finalAt, () => ({
      type: "transcript.final", ts: now(), session_id: SESSION,
      item_id: `item_${seq}`, seq, text: line.ja, stt_latency_ms: 690,
    }));
    at(finalAt + 60, () => ({
      type: "segment.created", ts: now(), session_id: SESSION,
      segment_id: segmentId, seq, source_text: line.ja, source_seqs: [seq], state: "translating",
    }));

    if (line.fail) {
      at(finalAt + 2600, () => ({
        type: "segment.failed", ts: now(), session_id: SESSION,
        segment_id: segmentId, seq, source_text: line.ja, state: "failed", reason: "timeout",
      }));
    } else {
      at(finalAt + 1100, () => ({
        type: "segment.translated", ts: now(), session_id: SESSION,
        segment_id: segmentId, seq, source_text: line.ja, target_text: line.vi,
        state: "translated", translation_latency_ms: 1096, e2e_latency_ms: 2200,
        usage: { input_tokens: 240, output_tokens: 32 },
      }));
    }

    clock = finalAt + 900;
  });

  // Status heartbeat with a plausible wandering level meter.
  const heartbeat = window.setInterval(() => {
    onEvent({
      type: "status", ts: now(), session_id: SESSION,
      capture: "running", stt: "connected", translation: "ready",
      audio_level_dbfs: -45 + Math.random() * 25, queue_depth: 0, detail: null,
    });
  }, 500);

  // The UI must also look right when the network wobbles.
  at(6200, () => ({
    type: "error", ts: now(), session_id: SESSION, severity: "warning",
    code: "stt_reconnecting", message: "Reconnecting to OpenAI (attempt 1).", retryable: true,
  }));

  return () => {
    timers.forEach(window.clearTimeout);
    window.clearInterval(heartbeat);
  };
}
