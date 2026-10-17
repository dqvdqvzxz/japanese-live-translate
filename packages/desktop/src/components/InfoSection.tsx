/**
 * Attribution and a plain-language account of where data goes.
 *
 * Written in Japanese because the people most likely to ask — the other
 * participants in the meeting — are Japanese speakers.
 *
 * It deliberately does NOT say "data is never sent to a third party". That
 * would be false: audio goes to OpenAI for transcription and the Japanese text
 * goes to OpenAI for translation, and OpenAI is a third party. A privacy
 * notice that a colleague might rely on has to be accurate, so this states the
 * true and still reassuring version: the only destination is the OpenAI API,
 * nothing is stored, and there is no telemetry.
 */

const APP_VERSION = "0.1.0";

export function InfoSection() {
  return (
    <section className="panel__section">
      <h3>情報 / Info</h3>

      <p className="info__app">
        Japanese Live Translator <span className="info__version">v{APP_VERSION}</span>
      </p>
      <p className="panel__hint">
        日本語の会議をリアルタイムでベトナム語に翻訳します。
        <br />
        開発者 / Developer: <strong>VuongDQ</strong>
      </p>

      <h4 className="info__heading">データの取り扱い</h4>
      <ul className="info__list" lang="ja">
        <li>
          <strong>外部に残りません。</strong>
          音声とテキストは文字起こしと翻訳のために OpenAI API へ送られますが、
          リクエストには <code>store: false</code> を指定しているため、
          OpenAI 側に保存されることはありません。
          OpenAI のポリシーにより、API 経由のデータが
          モデルの学習に使われることもありません。
        </li>
        <li>
          <strong>送信先は OpenAI API だけです。</strong>
          開発者のサーバーや解析サービスなど、他のどこにも送信されません。
          利用統計やテレメトリの送信も一切ありません。
        </li>
        <li>
          <strong>音声は保存しません。</strong>
          録音ファイルは作成されず、音声はメモリ上を通過するだけです。
        </li>
        <li>
          <strong>文字起こしと翻訳も既定では保存しません。</strong>
          設定で明示的に有効にした場合のみ、この Mac 内にのみ保存されます。
          その場合も外部に送信されることはありません。
        </li>
        <li>
          <strong>勝手に録音しません。</strong>
          「Start」を押したときだけ録音が始まり、
          録音中は画面下のインジケーターで常に確認できます。
        </li>
      </ul>

      <p className="info__note" lang="ja">
        ご注意：文字起こしと翻訳は OpenAI のサーバー上で処理されます。
        機密性の高い会議で使用する場合は、所属組織の規定をご確認ください。
        また、参加者の音声を処理することについて、
        必要に応じて事前に同意を得てください。
      </p>

      <p className="panel__hint">
        Lưu ý: âm thanh và văn bản được gửi tới OpenAI để xử lý — đó là cách
        ứng dụng hoạt động. Không có nơi nhận nào khác.
      </p>
    </section>
  );
}
