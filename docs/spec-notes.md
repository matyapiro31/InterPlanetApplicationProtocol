# 仕様解釈ノート (RFC-9999 IPAP v2 → 参照実装)

RFC-9999-IPAP-v2.docx が規定していない、または曖昧な箇所について、
`src/ipap/` の参照実装が採用した解釈をまとめる。

## 5. Message Format

| 項目 | 解釈 | 実装 |
|---|---|---|
| ヘッダ | 16 バイト固定、ネットワークバイトオーダー (big-endian)。`Version`=1 | `packet.py` |
| Packet Length | ヘッダ + ペイロード + 署名 (あれば) の**総バイト長**。最大 65535 | `Packet.length` |
| Mission Timestamp | ミッションエポック (2026-01-01T00:00:00Z) からの秒 (uint32)。シミュレーションではシミュレーション時刻 | `constants.MISSION_EPOCH_UNIX` |
| Priority | LOW=0, NORMAL=1, HIGH=2, CRITICAL=3。省略時は 5.2 の表の既定値 | `constants.Priority` |
| LLM Version | 8bit 番号 ↔ `ipap-` 名前空間のモデル ID のレジストリ (0 = 未指定) | `registry.ModelRegistry` |
| Flags | bit0 `SIGNED`, bit1 `ENCRYPTED`, bit2 `COMPRESSED` (zlib)。未知のビットが立っていれば破棄 | `constants.Flags` |
| Reserved | 送信時 0、受信時は無視 | |
| ペイロード | UTF-8 JSON (5.3 / 8.2 の例の形式)。圧縮 → 暗号化 → 署名の順に適用 | `payloads.py`, `session.py` |

### 各メッセージのペイロード

| Type | 主なフィールド |
|---|---|
| WAKE | `ipap_version` |
| READY | `in_reply_to`, `power`, `power_level`, `llm` (`AVAILABLE`/`DEFERRED`/`UNAVAILABLE`), `llm_model`, `reason` |
| EXEC | 5.3 / 8.2 の全フィールド + `input` (実行時入力) + `constraints.return_code` |
| RESULT | `in_reply_to`, `status`, `output`, `error`, `program` (`generated` / `fallback:<id>`), `code_sha256`, `verification`, `metrics` |
| ABORT | `target_seq` (省略時は実行中のジョブ), `reason` |
| STATUS | `state`, `power`, `power_level`, `llm`, `active_seq` |

- 7.2 の例では READY が WAKE と同じ `seq=1001` を使っているが、本実装では
  **送信方向ごとに独立したシーケンス番号**を使い、応答はペイロードの `in_reply_to` で対応付ける。
- RESULT の `status`: `OK`, `VERIFY_FAILED`, `RESOLVE_FAILED`, `EXEC_FAILED`, `ABORTED`, `REJECTED`, `DEFERRED`。
- LLM の生成自体が失敗した場合も、検証を通過したプログラムが得られなかったとして
  `VERIFY_FAILED` (error が `generation failed: ...`) とし、fallback に切り替える。

## 6. State Machine

RFC の図は WAKE と EXEC の処理を 1 本の流れで描いているため、次のように分けた。

- **WAKE**: `IDLE → READY_CHECK → IDLE` (READY 応答)、電力不足なら `READY_CHECK → DEFER → IDLE`。
- **EXEC**: `IDLE → READY_CHECK → LLM_ACTIVE → VERIFYING → EXECUTING → REPORTING → IDLE`。
  検証失敗時は `VERIFYING → ROLLBACK → REPORTING`。電力不足なら `READY_CHECK → DEFER → REPORTING` (RESULT `DEFERRED`)。
- アセット解決失敗・未知のテストベクター・未対応言語・モデル不一致は `READY_CHECK → REPORTING`
  (LLM を起動しない)。
- **ABORT** はどの処理中状態からでも `REPORTING` に遷移し RESULT `ABORTED` を返す
  (生成中・検証中・実行中の子プロセスは kill される)。処理中でなければ RESULT `REJECTED` (`NO_ACTIVE_JOB`)。
- 処理中に届いた EXEC は RESULT `REJECTED` (`BUSY`)、WAKE は READY `UNAVAILABLE` (`BUSY`)。
- 遷移表は `node.TRANSITIONS`。表にない遷移は `IllegalTransition` 例外。

## 7. Power Management

7.1 の表は 60% 超の帯を "CRITICAL" と呼んでおり、メッセージ優先度の CRITICAL と紛らわしいため、
内部名を `FULL` とした (挙動は表どおり)。

| 内部名 | 範囲 | 挙動 |
|---|---|---|
| FULL | > 60% | LLM 起動可 |
| NORMAL | 30–60% | LLM 起動可、タイムアウト ×0.5 |
| LOW | 10–30% | LLM 起動不可。WAKE には READY `DEFERRED`、EXEC には RESULT `DEFERRED` |
| EMERGENCY | < 10% | ABORT 以外を拒否。WAKE/EXEC には拒否を知らせる最小限の応答のみ返す |

- DEFER は 5.2 にメッセージ型が無いため、READY / RESULT の値として表現した。
- CRITICAL 優先度の EXEC はハンドシェイクを省略できるが (7.2 MAY)、電力閾値は優先度に関係なく適用する。

## 8. IPFS Integration

- `assets` の各要素は `ipfs://<CID>  (ラベル)` 形式を受け付ける (ラベルは任意)。
- CID は CIDv0 (sha2-256 multihash の base58btc)。ただし **生バイト列のハッシュ**であり、
  UnixFS/dag-pb でラップする実際の `ipfs add` の CID とは一致しない。実 IPFS ノードとの
  連携は `AssetStore` を差し替えて行う想定。
- 取得したデータは CID と照合してから使う。見つからなければ RESULT `RESOLVE_FAILED` (8.2 MUST)。

## 9. Verification and Safety

(RFC 本文では「8. Verification」と章番号が重複しているが、目次に従い 9 章として扱う。)

- **生成プログラムの規約**: トップレベルに `main(input, assets)` を定義し、JSON 化できる値を返す。
  `assets` は CID → バイト列の辞書。
- **層1 静的解析 (MUST)**: 構文、`main` の有無、import 許可リスト (math, json, heapq など純計算系のみ)、
  `open` / `eval` / `exec` / `getattr` / `__import__` などの禁止、dunder 属性アクセスの禁止。
- **層2 テストベクター (MUST)**: `constraints.test_vectors` はノードに事前配備されたベクター ID、
  またはインラインの `{id, input, expected[, tolerance]}`。ベクターが 1 つも無い EXEC は既定で検証失敗とする。
- **層3 サンドボックス (SHOULD)**: 実際の `input` で隔離実行 (dry run) し、例外・タイムアウトが無いことを確認。
- サンドボックス: 別プロセスの `python -I -S`、空の一時ディレクトリ、環境変数の除去、
  rlimit (CPU・メモリ・ファイルサイズ・FD 数・プロセス数)、壁時計タイムアウト、
  実行時の import 制限と組み込み関数の除去。**seccomp や名前空間による隔離は行っていない**ため、
  実機では OS/ハイパーバイザレベルの隔離と組み合わせる必要がある。
- ロールバック (9.2): 生成コードを破棄し、`fallback` (`program_id:<id>`) の事前配備プログラムを
  実行して、その出力付きで RESULT `VERIFY_FAILED` を返す。

## 10. Security Considerations

- 全パケットに Ed25519 署名 (MUST)。署名はヘッダ (SIGNED フラグと最終 Length を含む) +
  ペイロードに対して計算し、パケット末尾に 64 バイトで付加する。署名なし・不正署名は破棄。
- E2E 暗号化 (SHOULD): ペイロードのみ NaCl `Box` (X25519 + XSalsa20-Poly1305)。
  X25519 鍵は Ed25519 鍵から変換するので、各エンドポイントの秘密情報は 32 バイトのシード 1 つ。
- **リプレイ対策** (RFC 未規定): 送信元ごとに受信済みシーケンス番号を記録し、
  重複およびウィンドウ (1024) より古い番号を破棄する。
- 伸張後サイズは 1 MiB に制限 (zip bomb 対策)。
- プロンプトインジェクションへの防御は「署名による送信元認証」と「検証層」に依存する。
  LLM 自体の出力は信頼せず、必ず 3 層検証を通す。

## 本実装の対象外

実ネットワーク (DTN/Bundle Protocol 実装、CCSDS)、実 IPFS ノード連携、フラグメンテーション、
鍵配送・失効、複数ノードへのマルチキャスト。
