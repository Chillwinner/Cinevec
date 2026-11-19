<div align="center">

# CineVec

### 自托管多模态媒资检索平台 · 影院级 3D 放映室 UI

**别再跟 `VID_20240819_最终版_真最终版2.mp4` 玩捉迷藏了。**
输入一句画面描述、拖入一张参考图，或者搜一句台词——CineVec 直接在 3D WebGL 影院走廊里带你精准空降到视频对应的那一秒。

[English](README.md) · **简体中文** · [日本語](README.ja.md) · [한국어](README.ko.md) · [Español](README.es.md)

[![EmbeddingGemma 2](https://img.shields.io/badge/Embedding-Gemma%202%20(768d)-4285F4?style=for-the-badge&logo=google&logoColor=white)](https://ai.google.dev/)
[![Milvus Lite / v2](https://img.shields.io/badge/Milvus-Lite%20%2F%20REST%20v2-00A1EA?style=for-the-badge)](https://milvus.io/)
[![FastAPI](https://img.shields.io/badge/FastAPI-Python%203.11%2B-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React + Three.js](https://img.shields.io/badge/React%2019-Three.js%20WebGL-111111?style=for-the-badge&logo=threedotjs&logoColor=white)](https://threejs.org/)
[![MinIO / S3](https://img.shields.io/badge/Storage-S3%20%2F%20MinIO-C72E49?style=for-the-badge&logo=minio&logoColor=white)](https://min.io/)

[能做什么](#它能做什么) • [界面预览](#界面预览) • [核心优势](#为什么选-cinevec相较同类的优点) • [插件与 CineGram](#插件生态与-cinegram) • [替换底层组件](#如何替换向量库模型或存储) • [快速部署](#快速部署教程)

</div>

---

## 它能做什么

**CineVec** 是一个面向剪辑师、素材收藏党和自托管玩家的**多模态图片/视频语义检索中枢**，专治“硬盘和群里存了上万条素材，真要用时死活翻不到”的顽疾。

- **三合一导演搜索栏**：自然语言以文搜画面（如“雨里亮着灯的街”）、直接拖图以图搜镜头、以及按文件名/标签/描述文案的毫秒级模糊检索。
- **时间戳直跳放映室**：点开命中的视频直接定位到对应关键帧秒数，配套关键帧胶片条、自定义封面与 `.srt/.vtt/.ass` 字幕时间轴。
- **首次启动交互式新手引导**：初次打开自动弹出配置体检大弹窗 + 贴边聚光灯气泡导览，手把手告诉你先配什么、每个按钮干嘛用。

---

## 界面预览

### 多模态语义搜索 — 3D 影院走廊模式

<table>
  <tr>
    <td width="50%" align="center">
      <img src="docs/images/search-3d-forest.png" alt="CineVec 3D 语义搜索 - forest" />
      <br />
      <sub><b>搜索词：<code>"forest"</code></b> — 3D 走廊精准将红杉林画面推至首位（余弦 <code>0.696</code>）</sub>
    </td>
    <td width="50%" align="center">
      <img src="docs/images/search-3d-dog.png" alt="CineVec 3D 语义搜索 - dog" />
      <br />
      <sub><b>搜索词：<code>"dog"</code></b> — 3D 走廊实时聚合相关镜头（余弦 <code>0.718</code>）</sub>
    </td>
  </tr>
</table>

### 硬件遥测、4 阶段流水线与资料库管理

<table>
  <tr>
    <td width="50%" align="center">
      <img src="docs/images/activity-pipeline.png" alt="CineVec 仪表盘" />
      <br />
      <sub><b>仪表盘</b> — 实时 CPU / 内存 / GPU 仪表盘与 4 阶段红绿灯流水线</sub>
    </td>
    <td width="50%" align="center">
      <img src="docs/images/library-catalog.png" alt="CineVec 资料库" />
      <br />
      <sub><b>资料库</b> — 资产筛选、批量标签、自定义封面、文案编辑与安全清理</sub>
    </td>
  </tr>
</table>

---

## 为什么选 CineVec（相较同类的优点）

市面上的自托管图库要么逼你把几个 TB 的视频再拷贝一份，要么一跑 AI 编码就把硬盘塞爆，要么代码耦合得像一坨意大利面。CineVec 的设计非常克制且实用：

| 对比维度 | 常见同类图库/片库 | CineVec |
| :--- | :--- | :--- |
| **存储占用** | 必须把原片完整复制进自己的目录。 | **Zero-Retention 零原片留存**：附带外部 `source_url`，抽完关键帧立刻删原片，只留轻量预览图和向量，几百 GB 片库只占几百 MB 索引空间。 |
| **批量视频防堆积** | 单线程串行，前面一个视频在等 GPU 向量化，后面几十个原视频全堆在硬盘里。 | **双通道并发流水线**：`preprocess`（FFmpeg 抽帧）与 `embed`（向量编码）独立线程解耦，原片抽完立刻销毁，绝不积压。 |
| **向量库部署成本** | 光起一个 Milvus 集群就得伺候 etcd + MinIO + standalone 一堆容器。 | **开箱内置 Milvus Lite**（Docker Compose 默认单文件向量库），后期需要集群时改一行环境变量即可无缝切 **Milvus REST v2**。 |
| **外部对象存储安全** | 容易改动或锁死你的原文件。 | **外部桶严格只读**：只挂载不改写；排除素材时「先删向量、再清预览」，绝不动外部原片。 |
| **插件扩展能力** | 只能手动点网页上传。 | **原生插件 API（`/api/v1/*`）**：支持签发独立 Bearer Token，内置批次进度、去重 `ref_id` 与来源失效状态回写，**任何人都可以用任何语言写自己的插件**。 |
| **交互体验** | 传统静态缩略图网格。 | **Three.js 3D 影院走廊** + 双语界面（`EN / 中文`）+ 首次启动大弹窗与贴边小气泡引导。 |

---

## 插件生态与 CineGram

CineVec 本体只专注做好「多模态检索与媒资中枢」，所有外部采集/机器人/浏览器插件均通过 `/api/v1/*` + Bearer API Token（在 **设置 → API Token** 一键生成）完全解耦接入，不碰本体数据库。

### 官方配套 Telegram 插件：**[CineGram](https://github.com/Chillwinner/CineGram)**

想把 Telegram 群组和频道里散落的图片、2GB～4GB 大视频变成可秒搜的 3D 素材库，又不重复占硬盘？直接配官方插件：

> **项目地址**：[https://github.com/Chillwinner/CineGram](https://github.com/Chillwinner/CineGram)

- **MTProto 用户号 & Bot 均完全可选**：独立轻量 FastAPI + Web 管理台，可按需运行仅 Web、仅 Bot、仅用户号或双模并行。
- **精细化筛选导入**：支持按群配置过滤规则（仅视频/仅图、大小/时长阈值、关键词/屏蔽词、回溯天数），还支持**在网页里先看群消息缩略图，打勾只导入想要的那几条**。
- **相册整组文案与 `#标签` 继承**：解决 Telegram 一条相册发多图只把文案挂在第 1 张的痛点，自动广播给同组所有文件，搜标签和模糊搜话里的词一条不漏。
- **定时存活巡检**：自动探测群改名、公开群转私密、被踢出或单条消息被删，自动刷新跳转链接或回写 `source_invalid` 状态。

你也可以照着 `/api/v1/*` 轻松写出自己的 Discord / 浏览器收藏夹 / NAS 监听插件，完全无需修改 CineVec 本体代码。

---

## 如何替换向量库、模型或存储

CineVec 后端采用标准的**六边形架构（Ports & Adapters）**，核心业务层（`backend/app/application/`）完全不直接依赖 Milvus、MinIO 或某个特定模型，只面向 [`backend/app/domain/ports.py`](backend/app/domain/ports.py) 中定义的 `Protocol` 接口编程。

### 1. 内置已支持的切换（零代码，改 `.env` 即可）
- **向量库**：`Milvus Lite`（`PICA_MILVUS_BACKEND=lite`） ↔ 远端 `Milvus REST v2`（`PICA_MILVUS_URI=http://...`）
- **编码模型**：`EmbeddingGemma 2`（HTTP） ↔ `Chinese-CLIP`（HTTP 或本地 ONNX）
- **对象存储**：MinIO / AWS S3 / Cloudflare R2 / 阿里云 OSS（任意 S3 兼容协议）

### 2. 如果想换成 Qdrant、pgvector、Weaviate 或自研模型，需要自己实现接口吗？
**需要，但非常轻松——只需实现对应 `Protocol` 并在工厂函数里接上即可（共 2 处，业务流水线和前端一行不用改）：**

| 要替换的组件 | 需实现的接口（`backend/app/domain/ports.py`） | 参考实现（`backend/app/infra/`） | 装配入口（`backend/app/main.py`） |
| :--- | :--- | :--- | :--- |
| **向量数据库** | `VectorIndex` | `milvus_lite.py` / `milvus_rest.py` | `build_vector_index()` |
| **图文编码器** | `ImageTextEncoder` | `embeddinggemma_http.py` / `clip_http.py` / `chinese_clip.py` | `build_encoders()` |
| **字幕编码器** | `SubtitleEncoder` | `clip_http.py` / `chinese_clip.py` | `build_encoders()` |
| **平台文件存储** | `FileStore` | `file_store.py` | `build_file_store()` |
| **只读外部数据源** | `SourceReader` | `s3_reader.py` | `build_container()` |

比如要把向量库换成 **Qdrant** 或 **pgvector**，只需写一个类实现 `VectorIndex` 的 6 个方法：

```python
class MyCustomVectorIndex:
    def available(self) -> bool: ...
    def status_message(self) -> str: ...
    def upsert_records(self, modality: str, records: list[dict]) -> int: ...
    def search(self, modality: str, vector: list[float], *, limit: int, offset: int = 0, filter_expr: str | None = None) -> list[dict]: ...
    def delete_vectors(self, refs: list) -> VectorDeleteResult: ...
    def delete_by_asset(self, asset_id: str, collections: list[str] | None = None) -> VectorDeleteResult: ...
```

然后在 [`backend/app/main.py`](backend/app/main.py) 的 `build_vector_index()` 里返回你的实例——搜索聚合、先删向量再清文件的安全删除、多模型空间重建、以及 3D 影院前端全部自动生效。

---

## 快速部署教程

### 方式一：Docker Compose 一键部署（强烈推荐 · 2 分钟跑起）

已内置 **MinIO**（自动建 `cinevec-media` 桶并自动登记为平台存储）、**Milvus Lite**（免装独立 Milvus 集群）、**FFmpeg** 与生产版前端：

```bash
git clone https://github.com/Chillwinner/Cinevec.git
cd Cinevec
cp .env.docker.example .env
# 打开 .env，把 PICA_CLIP_HTTP_BASE_URL 填成你的向量编码服务地址
docker compose up -d
```

浏览器打开 **http://localhost:5174**，首次进入会自动弹出新手引导，带你检查配置并逐个认识界面按钮。

### 方式二：本地手动开发部署

**环境要求**：Python 3.11+、Node.js 20+、FFmpeg、MinIO/S3 以及向量编码服务。

```bash
# 1. 配置环境变量（切勿提交 backend/.env 与 backend/data/*.db）
cp .env.example backend/.env

# 2. 启动后端（8008 端口）——首次启动自动初始化 SQLite 与 Milvus Lite
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows PowerShell 用: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install "pymilvus[milvus_lite]>=2.4.9"   # 如本机使用 Milvus Lite 则安装
uvicorn app.main:app --host 127.0.0.1 --port 8008

# 3. 新开终端启动前端（5174 端口）
cd web
npm install
npm run dev
```

浏览器访问 **http://127.0.0.1:5174** 即可。

### 测试与构建校验

```bash
cd backend && pytest -q
cd web && npm run build && npm test
```
