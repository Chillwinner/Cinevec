# 内部扩展指南：模型、向量库与存储解耦架构

Pica 采用端口-适配器（Ports & Adapters / Hexagonal）分层设计：业务编排层（`backend/app/application/*`）只依赖 `backend/app/domain/ports.py` 中定义的 Python `Protocol` 接口，不直接耦合具体模型框架、向量数据库或对象存储 SDK。

> 元数据层说明：`MetadataRepository` 使用 `backend/app/infra/sqlite_repos.py` 的 SQLite 实现并保持固定，避免跨数据库 SQL 方言、事务语义和迁移脚本分歧导致的隐性故障。需要扩展检索/存储能力的开发者，请按本文对接 **编码模型**、**向量库** 与 **对象存储** 三个可替换层。

---

## 1. 架构总览与装配入口

所有端口都在 [ports.py](file:///d:/Code/vec/backend/app/domain/ports.py) 声明，并在 [main.py](file:///d:/Code/vec/backend/app/main.py) 的工厂函数完成装配：

| 层级 | Port 协议（`domain/ports.py`） | 内置默认实现（`infra/`） | 装配点（`main.py`） |
| :--- | :--- | :--- | :--- |
| 图文编码模型 | `ImageTextEncoder` | `ClipHttpVisualEncoder` / `ChineseClipVisualEncoder` | `build_encoders()` |
| 字幕文本编码 | `SubtitleEncoder` | `ClipHttpSubtitleEncoder` / `ChineseClipSubtitleEncoder` | `build_encoders()` |
| 向量检索库 | `VectorIndex` | `MilvusRestIndex` | `build_vector_index()` |
| 平台对象存储 | `FileStore` | `PlatformResolvingFileStore` (`S3SourceReader` + `LocalFileStore`) | `build_file_store()` |
| 外部只读网盘 | `SourceReader` | `S3SourceReader` | `build_container()` |

测试或二次开发也可以直接向 `create_app(..., encoder=..., subtitle_encoder=..., index=..., file_store=..., source_reader=...)` 或 `build_container(...)` 注入自定义实例，无需改动上层服务。

---

## 2. 接入自定义图文 / 向量编码模型

核心协议定义在 [ports.py](file:///d:/Code/vec/backend/app/domain/ports.py)：

```python
class ImageTextEncoder(Protocol):
    def available(self) -> bool: ...
    def status_message(self) -> str: ...
    def encode_text(self, text: str) -> list[float]: ...
    def encode_image(self, image_bytes: bytes) -> list[float]: ...

class SubtitleEncoder(Protocol):
    def available(self) -> bool: ...
    def status_message(self) -> str: ...
    def encode_text(self, text: str) -> list[float]: ...
```

### 契约要求
1. **向量空间一致性**：图片与文本向量必须落在同一向量空间并保持相同维度（例如 512 维或 768 维）。若切换模型族（如从 Chinese-CLIP 切换到 SigLIP2 / EmbeddingGemma），建议配置不同的 `PICA_VECTOR_PROFILE` 或 `PICA_MODEL_ID` / `PICA_MODEL_DIM`，避免历史向量混淆。
2. **禁止静默回退**：若权重缺失或远程服务不可用，`available()` 应返回 `False`，`encode_*` 应抛出领域异常，严禁返回全零或随机向量。
3. **注册方式**：
   - 方式 A（推荐，免改核心代码）：启动兼容 HTTP 服务，并通过 `PICA_ENCODER_BACKEND=remote_http` 与 `PICA_CLIP_HTTP_BASE_URL` 配置接入。
   - 方式 B（Python 进程内适配器）：新增 `backend/app/infra/my_encoder.py` 实现 `ImageTextEncoder`（和可选的 `SubtitleEncoder`），并在 [main.py](file:///d:/Code/vec/backend/app/main.py) 的 `build_encoders()` 中根据 `settings.encoder_backend` 分支返回实例。

---

## 3. 接入自定义向量库（VectorIndex）

核心协议定义在 [ports.py](file:///d:/Code/vec/backend/app/domain/ports.py)：

```python
class VectorIndex(Protocol):
    def available(self) -> bool: ...
    def status_message(self) -> str: ...
    def delete_vectors(self, refs: list) -> VectorDeleteResult: ...
    def delete_by_asset(self, asset_id: str, collections: list[str] | None = None) -> VectorDeleteResult: ...
    def upsert_records(self, modality: str, records: list[dict]) -> int: ...
    def search(
        self,
        modality: str,
        vector: list[float],
        *,
        limit: int,
        offset: int = 0,
        filter_expr: str | None = None,
    ) -> list[dict]: ...
```

### 记录与元数据约定
- **`upsert_records(modality, records)`**：`modality` 为 `"image"`、`"frame"` 或 `"subtitle"`；每条 `record` 至少包含 `id`、`vector`、`asset_id`、`version`、`unit_index` 等轻量标量字段（完整描述、标签、外部网址均存于 SQLite）。
- **资产级一键清理（`delete_by_asset`）**：所有同一图片或同一视频的抽帧/字幕向量都绑定统一标量字段 `asset_id`。当用户排除或重索引资产时，系统会优先依据 `asset_id` 清理该资产名下的全部向量；即使逐条主键丢失，向量库适配器仍可通过 `delete_by_asset(asset_id)` 保障删除干净。
- **注册方式**：新增 `backend/app/infra/my_vector_index.py` 实现 `VectorIndex` 协议（如 Qdrant / pgvector / LanceDB 适配器），并在 [main.py](file:///d:/Code/vec/backend/app/main.py) 的 `build_vector_index()` 按配置返回。

---

## 4. 接入存储层与多 S3 分工规则

Pica 将对象存储拆分为两类角色：

1. **平台负责存储（`FileStore`，`is_platform_storage=True` 或 env `PICA_S3_*`）**
   - 必需方法：`available()`, `status_message()`, `exists(key)`, `put_bytes(...)`, `get_bytes(...)`, `head(key)`, `get_range(...)`, `put_stream(...)`, `delete_object(key)`, `delete_objects(keys)`。
   - **职责**：永久托管抽帧缩略图、图片预览图、视频封面（`covers/{asset_id}/...`），以及用户选择 `store_original=true` 时上传的原文件。
   - 若用户设置 `store_original=false` 并提供 `source_url`（或外部 `ref`），预处理抽帧与封面后会立即删除临时本地原件，不占用 `originals/` 空间。
2. **外部加载资源库（`SourceReader`，可接入多个 S3 / MinIO 桶）**
   - **职责**：按前缀浏览只读对象、按需流式拉取字节做抽帧/编码，不在 Pica 端长期留存原片副本。
   - 如需接入 WebDAV、NAS SMB 或私有对象协议，实现 `SourceReader` 并传入 `build_container(source_reader=...)` 即可。

---

## 5. 最小可用检查清单（Checklist）

实现任意自定义适配器后，建议直接在单元测试通过依赖注入验证行为：

```python
from pathlib import Path
from app.infra.config import Settings
from app.main import create_app

app = create_app(
    Settings(sqlite_path=Path("./tmp.db"), worker_enabled=False),
    encoder=MyCustomEncoder(),
    index=MyCustomVectorIndex(),
    file_store=MyCustomFileStore(),
)
```

- 编码器：`encode_text` 与 `encode_image` 输出维度严格匹配向量库 schema。
- 向量库：`delete_by_asset(asset_id)` 后，`search` 不得再返回该 `asset_id` 的旧切片。
- 存储层：`put_bytes` / `get_bytes` / `delete_objects` 对 `covers/` 与 `previews/` 键往返一致。
