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
          音声とテキストの送信先は <strong>OpenAI API のみ</strong>です。
          開発者のサーバーを含め、他のどこにも送信されません。
        </li>
        <li>音声ファイルは一切保存されません。</li>
        <li>
          文字起こしと翻訳は既定では端末に保存されません。
          設定で明示的に有効にした場合のみ保存されます。
        </li>
        <li>
          OpenAI へのリクエストは <code>store: false</code> を指定しているため、
          内容が OpenAI 側に保持されることはありません。
        </li>
        <li>利用統計やテレメトリの送信は一切ありません。</li>
        <li>
          録音は「Start」を押したときのみ開始されます。
          自動的に始まることはありません。
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
