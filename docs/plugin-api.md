# Pica 外部插件与客户端 API (`/api/v1`)

`/api/v1` 面向浏览器扩展、桌面采集器、第三方客户端与自动化脚本开放：开发者无需依赖自带 Web 页面，即可完成 **媒体入库、任务轮询、三路独立检索、元数据读写、视频封面替换与预览图/原片访问**。

---

## 1. 鉴权说明

所有 `/api/v1/*` 接口均需携带 API Token（在设置页生成的 `pica_...` 密钥，明文仅展示一次）：

- **常规 API 请求**：Header `Authorization: Bearer <token>`
- **图片/封面/音视频流媒体地址**（如 `<img src>` / `<video src>`）：支持在查询参数附加 `?access_token=<token>`，方便浏览器客户端直接加载媒体资源。

探活示例：

```bash
curl -sS -H "Authorization: Bearer $PICA_TOKEN" \
  http://127.0.0.1:8000/api/v1/health
```

---

## 2. 媒体上传与零留存外链模式 (`POST /api/v1/imports`)

使用 `multipart/form-data` 提交，包含：
- `files`：待处理的图片、视频或关联字幕文件
- `meta`：JSON 数组（与 `files` 对应），支持字段：
  - `relative_path` *(str)*：相对路径或文件名
  - `vectorize` *(bool, 默认 true)*：是否进入向量化队列
  - `store_original` *(bool, 默认 true，兼容字段 `copy`)*：
    - `true`：将原片/原图复制保存到平台主 S3 存储（`originals/`）
    - `false`：**零留存外链模式**——不保存原件到平台 S3，Pica 仅将抽帧预览图、视频封面与向量持久化存储，并在预处理完成后立刻删除本地临时原文件，防止压垮磁盘
  - `source_url` *(str)*：**当 `store_original=false` 时必填**（也可传 `ref.ref_id` + `ref.locator`），用于记录视频/图片原始来源网页地址
  - `description` *(str, 可选)*：图片或视频文字描述，写入 SQLite 并参与元数据检索
  - `subtitle_relative_path` *(str, 可选)*：关联同步上传的字幕文件路径
  - `cover_relative_path` *(str, 可选)* / `is_cover` *(bool, 可选)*：随批次上传指定封面图，或直接通过额外表单字段 `cover` 提交单文件封面图
- `cover` *(可选文件)*：可选的视频/资产封面图；若未上传，系统会在抽帧完成后自动回退采用首个保留帧作为封面

> **单次传输队列上限**：系统配置项 `max_upload_files`（默认 `20`，可在设置页 1–500 范围调整）会限制单次请求携带的文件总数；超限将直接拒绝并返回 HTTP 400（错误码 `upload_limit_exceeded`），防止客户端一次传过多文件压垮磁盘。

### 示例 A：保留原文件 + 上传封面 + 描述

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/imports \
  -H "Authorization: Bearer $PICA_TOKEN" \
  -F 'meta=[{"relative_path":"game/draw.mp4","vectorize":true,"store_original":true,"description":"你画我猜录像"}]' \
  -F "files=@draw.mp4;type=video/mp4" \
  -F "cover=@cover.png;type=image/png"
```

### 示例 B：不保存原文件（仅存预览、封面、网址与向量）

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/imports \
  -H "Authorization: Bearer $PICA_TOKEN" \
  -F 'meta=[{"relative_path":"clip.mp4","store_original":false,"source_url":"https://example.com/watch?v=123","description":"外部爬取片段"}]' \
  -F "files=@clip.mp4;type=video/mp4"
```

缺少 `source_url`（且未传完整 `ref`）时将直接返回 HTTP 400。

---

## 3. 轮询导入与处理进度

- `GET /api/v1/jobs/{job_id}`：查看单个资产的处理阶段
- `GET /api/v1/batches/{batch_id}`：按批次查看整批导入汇总

`phase` 枚举：`accepted` | `queued` | `preprocess` | `encode` | `index_write` | `searchable` | `blocked` | `failed`。建议每 1–2 秒轮询，进入 `searchable`、`failed` 或 `blocked` 后停止。

---

## 4. 三条独立检索路径（不强制跨路融合）

| 路径 | 接口 | 说明 |
| :--- | :--- | :--- |
| 纯文本语义检索 | `POST /api/v1/search/text` | JSON Body：`{"query": "日落海边", "top_n": 20, "score_threshold": null}`。按视觉相似度返回命中帧（`previews[]`）与 `video_cover_locator`、`source_url`、`description`、`tags`。 |
| 纯单张图片检索 | `POST /api/v1/search/image` | Multipart Form：`image` 文件 + 可选 `top_n`、`score_threshold`。按画面向量检索最相似镜头。 |
| 名字 / 标签 / 描述检索 | `GET /api/v1/search/metadata?q=...` 或 `POST /api/v1/search/metadata` | JSON Body：`{"query": "关键词", "source_id": null}`。纯 SQLite 模糊包含匹配文件名、人工标签及 `description`，不调用模型。 |

---

## 5. 资产元数据、封面与流媒体接口

第三方客户端可完整读写并展示所有展示所需数据，无需访问内部私有路径：

- `GET /api/v1/assets?kind=&excluded=&pending=&indexed=&q=&limit=60&offset=0`：分页列举资产
- `GET /api/v1/assets/{asset_id}`：查看单个资产详情（含 `description`、`source_url`、`store_original`、`cover_url`、`tags`、`searchable`、`ref`）
- `PATCH /api/v1/assets/{asset_id}`：更新 `description`、`display_name`、`source_url` 或全量替换 `tags` 数组
- `GET /api/v1/assets/{asset_id}/cover`：读取视频/资产封面图（未单独上传时自动回退至首个有效帧）
- `POST /api/v1/assets/{asset_id}/cover`：上传并替换封面图（Multipart `cover` 字段）
- `GET /api/v1/previews/{asset_id}/{kind}/{unit_index}`：读取预览图 / 抽帧缩略图（`kind` 如 `frames`）
- `GET /api/v1/assets/{asset_id}/media`：支持 `Range` 分片播放已托管的视频/图片原件
- `POST /api/v1/assets/{asset_id}/tags`、`DELETE /api/v1/assets/{asset_id}/tags/{tag}`：单标签增删
- `POST /api/v1/assets/{asset_id}/exclude`、`POST /api/v1/assets/{asset_id}/restore`：排除（自动按 `asset_id` 清理向量与平台预览/封面）与恢复
