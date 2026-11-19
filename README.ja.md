<div align="center">

# CineVec

### シネマ級 3D スクリーニング UI を備えたセルフホスト型マルチモーダルメディア検索

**`VID_20240819_final_FINAL2.mp4` を探す探偵ごっこはもう終わりにしましょう。**
シーンの文章入力・参考画像のドラッグ＆ドロップ・字幕検索だけで、3D WebGL シアターから該当タイムスタンプへ一発ジャンプできます。

[English](README.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md) · [Español](README.es.md)

[![EmbeddingGemma 2](https://img.shields.io/badge/Embedding-Gemma%202%20(768d)-4285F4?style=for-the-badge&logo=google&logoColor=white)](https://ai.google.dev/)
[![Milvus Lite / v2](https://img.shields.io/badge/Milvus-Lite%20%2F%20REST%20v2-00A1EA?style=for-the-badge)](https://milvus.io/)
[![FastAPI](https://img.shields.io/badge/FastAPI-Python%203.11%2B-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React + Three.js](https://img.shields.io/badge/React%2019-Three.js%20WebGL-111111?style=for-the-badge&logo=threedotjs&logoColor=white)](https://threejs.org/)
[![MinIO / S3](https://img.shields.io/badge/Storage-S3%20%2F%20MinIO-C72E49?style=for-the-badge&logo=minio&logoColor=white)](https://min.io/)

[機能](#できること) • [スクリーンショット](#ビジュアルショーケース) • [強み](#他ツールとの違い) • [プラグイン & CineGram](#プラグインエコシステム--cinegram) • [バックエンド差し替え](#ベクトル-dbモデルストレージの差し替え) • [クイックスタート](#クイックスタートデプロイ手順)

</div>

---

## できること

**CineVec** は、大量の動画素材やスクリーンショットを抱える映像クリエイター・コレクター向けの**セルフホスト型マルチモーダル検索ハブ**です。

- **3 つの検索モードを 1 つのドックに集約**：自然言語でのセマンティック検索、参考画像ドロップによる類似フレーム検索、ファイル名・タグ・説明文の高速レキシカル検索。
- **タイムスタンプ直行スクリーニングルーム**：ヒットした動画は該当キーフレームの秒数から即再生。キーフレームフィルムストリップ、カスタムカバー、`.srt/.vtt/.ass` 字幕タイムラインに対応。
- **初回起動インタラクティブガイド**：初回アクセス時に設定チェック用モーダル＋各ボタン横のスポットライト解説ツアーが自動起動します。

---

## ビジュアルショーケース

<table>
  <tr>
    <td width="50%" align="center">
      <img src="docs/images/search-3d-forest.png" alt="CineVec 3D Cinema Semantic Search - forest" />
      <br />
      <sub><b>クエリ: <code>"forest"</code></b> — 3D シアター廊下で最適なカットを上位表示（cosine <code>0.696</code>）</sub>
    </td>
    <td width="50%" align="center">
      <img src="docs/images/search-3d-dog.png" alt="CineVec 3D Cinema Semantic Search - dog" />
      <br />
      <sub><b>クエリ: <code>"dog"</code></b> — 類似シーンを瞬時にランキング（cosine <code>0.718</code>）</sub>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <img src="docs/images/activity-pipeline.png" alt="CineVec Activity Dashboard" />
      <br />
      <sub><b>アクティビティダッシュボード</b> — CPU / RAM / GPU ゲージと 4 段階パイプライン</sub>
    </td>
    <td width="50%" align="center">
      <img src="docs/images/library-catalog.png" alt="CineVec Library Catalog" />
      <br />
      <sub><b>ライブラリカタログ</b> — 絞り込み、一括タグ付け、カバー変更、安全削除</sub>
    </td>
  </tr>
</table>

---

## 他ツールとの違い

| 機能 | 一般的なギャラリーツール | CineVec |
| :--- | :--- | :--- |
| **ストレージ容量** | 元動画を丸ごと複製してディスクを圧迫。 | **Zero-Retention モード**：`source_url` のみ紐づけ、キーフレーム抽出直後に元ファイルを自動削除。軽量プレビューとベクトルだけ保持。 |
| **大量動画の取り込み** | GPU 推論待ちで前処理が詰まり元動画が堆積。 | **デュアルレーン並列ワーカー**：前処理（FFmpeg）とベクトル化（Embed）を別スレッド化し、抽出後即座に元動画を削除。 |
| **ベクトル DB 運用** | Milvus + etcd + MinIO の構築が重い。 | **Milvus Lite 標準内蔵**（Docker Compose は単一ファイル動作）。環境変数 1 つで **Milvus REST v2** クラスタへ移行可能。 |
| **外部バケット安全性** | 既存ファイルを移動・改変しがち。 | **読み取り専用マルチバケット**：外部 S3/MinIO は一切変更せず、削除時もベクトル優先パージで整合性を保証。 |
| **拡張性** | Web 画面からの手動アップロード限定。 | **プラグイン専用 API（`/api/v1/*`）**：Bearer トークン認証で任意の言語から独自プラグインを開発可能。 |

---

## プラグインエコシステム & CineGram

CineVec 本体は「検索コア」に専念し、外部連携はすべて `/api/v1/*` + Bearer API Token（**Settings → API Tokens** で発行）で疎結合に接続します。

### 公式 Telegram プラグイン：**[CineGram](https://github.com/Chillwinner/CineGram)**

> **リポジトリ**: [https://github.com/Chillwinner/CineGram](https://github.com/Chillwinner/CineGram)

- **MTProto ユーザーアカウント＆Bot は両方オプション**：Web のみ / Bot のみ / MTProto のみ / デュアルモードを自由に選択可能。
- **選択的インポート**：グループ別のフィルタ規則（種類・サイズ・長さ・キーワード・遡及日数）に加え、**Web 画面でサムネイルを見てチェックしたメッセージだけ取り込み**が可能。
- **アルバム本文＆`#タグ` 自動継承**：Telegram アルバムの 2 枚目以降にキャプションが付かない仕様を補完し、全ファイルへ自動引き継ぎ。
- **リンク生存監視**：グループ名変更・非公開化・削除を定期巡回し、`source_url` の自動更新や `source_invalid` 反映を実行。

---

## ベクトル DB・モデル・ストレージの差し替え

CineVec は **ヘキサゴナルアーキテクチャ（Ports & Adapters）** を採用しており、ビジネスロジックは [`backend/app/domain/ports.py`](backend/app/domain/ports.py) の `Protocol` インターフェースのみに依存します。

### 1. コード変更なしで切り替え可能（`.env` のみ）
- **ベクトル DB**：内蔵 **Milvus Lite**（`PICA_MILVUS_BACKEND=lite`）↔ **Milvus REST v2**（`PICA_MILVUS_URI=http://...`）
- **エンコーダ**：**EmbeddingGemma 2**（HTTP）↔ **Chinese-CLIP**（HTTP / ローカル ONNX）
- **ストレージ**：MinIO / AWS S3 / Cloudflare R2 など S3 互換ストレージ全般

### 2. Qdrant や pgvector など独自バックエンドを使いたい場合
**はい、対応する `Protocol` インターフェースを実装してファクトリ関数に登録するだけです（業務ロジックや UI は無改修）：**

| 差し替え対象 | インターフェース (`backend/app/domain/ports.py`) | 参考実装 (`backend/app/infra/`) | 登録先 (`backend/app/main.py`) |
| :--- | :--- | :--- | :--- |
| **ベクトル DB** | `VectorIndex` | `milvus_lite.py` / `milvus_rest.py` | `build_vector_index()` |
| **画像・テキストモデル** | `ImageTextEncoder` | `embeddinggemma_http.py` / `clip_http.py` | `build_encoders()` |
| **字幕モデル** | `SubtitleEncoder` | `clip_http.py` / `chinese_clip.py` | `build_encoders()` |
| **ファイル保存** | `FileStore` | `file_store.py` | `build_file_store()` |
| **読み取り専用ソース** | `SourceReader` | `s3_reader.py` | `build_container()` |

```python
class MyCustomVectorIndex:
    def available(self) -> bool: ...
    def status_message(self) -> str: ...
    def upsert_records(self, modality: str, records: list[dict]) -> int: ...
    def search(self, modality: str, vector: list[float], *, limit: int, offset: int = 0, filter_expr: str | None = None) -> list[dict]: ...
    def delete_vectors(self, refs: list) -> VectorDeleteResult: ...
    def delete_by_asset(self, asset_id: str, collections: list[str] | None = None) -> VectorDeleteResult: ...
```

---

## クイックスタート（デプロイ手順）

### 方法 A：Docker Compose ワンクリック起動（推奨・約 2 分）

**MinIO**（バケット自動作成＆自動登録）、内蔵 **Milvus Lite**、**FFmpeg**、本番ビルド済み **3D Web UI** がすべて含まれます：

```bash
git clone https://github.com/Chillwinner/Cinevec.git
cd Cinevec
cp .env.docker.example .env
# .env を開き PICA_CLIP_HTTP_BASE_URL にエンコーダサービスの URL を設定
docker compose up -d
```

ブラウザで **http://localhost:5174** を開くと、初回ガイドが設定確認と UI 操作をステップごとに案内します。

### 方法 B：ローカル手動起動

```bash
cp .env.example backend/.env

# バックエンド起動 (8008)
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install "pymilvus[milvus_lite]>=2.4.9"
uvicorn app.main:app --host 127.0.0.1 --port 8008

# 別ターミナルでフロントエンド起動 (5174)
cd web && npm install && npm run dev
```
