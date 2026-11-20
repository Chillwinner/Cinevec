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

[能做什么](#它能做什么) • [界面预览](#界面预览) • [核心优势](#为什么选-cinevec相较同类的优点) • [推荐配置与模型选型](#推荐配置与为什么默认选-embeddinggemma-2) • [插件与 CineGram](#插件生态与-cinegram) • [乐高式解耦架构](#真正解耦像拼乐高一样自由拼装组件) • [快速部署与改写 Compose](#快速部署与自定义-docker-composeyml)

</div>

---

## 它能做什么

**CineVec** 是一个面向剪辑师、素材收藏党和自托管玩家的**多模态图片/视频语义检索中枢**，专治“硬盘和群里存了上万条素材，真要用时死活翻不到”的顽疾。

- **三合一导演搜索栏**：自然语言以文搜画面（如“雨里亮着灯的街”）、直接拖图以图搜镜头、以及按文件名/标签/描述文案的毫秒级模糊检索。
- **时间戳直跳放映室**：点开命中的视频直接定位到对应关键帧秒数，配套关键帧胶片条、自定义封面与 `.srt/.vtt/.ass` 字幕时间轴。
- **五语界面 + 首次启动交互引导**：Web 端原生支持 `English / 简体中文 / 日本語 / 한국어 / Español`（默认英文，`localStorage` 自动记住上次语言），首次启动自动弹出配置体检大弹窗与贴边聚光灯气泡教程，新人也能一眼上手。

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

市面上的自托管图库要么逼你把几个 TB 的视频再拷贝一份，要么一跑 AI 编码就把硬盘塞爆，要么把“某个特定模型 + 某个特定数据库”焊死在一坨代码里。CineVec 选择了一套完全解耦、克制实用的打法：

| 对比维度 | 常见同类图库/片库 | CineVec |
| :--- | :--- | :--- |
| **存储占用** | 必须把原片完整复制进自己的目录。 | **Zero-Retention 零原片留存**：附带外部 `source_url`，抽完关键帧立刻删临时原片，只留轻量预览图和向量，几百 GB 片库只占几百 MB 索引空间。 |
| **批量视频防堆积** | 单线程串行，前面一个视频在等 GPU 向量化，后面几十个原视频全堆在硬盘里。 | **双通道并发流水线**：`preprocess`（FFmpeg 抽帧）与 `embed`（向量编码）独立线程解耦，原片抽完立刻销毁，绝不积压。 |
| **向量库部署成本** | 光起一个 Milvus 集群就得伺候 etcd + MinIO + standalone 一堆容器。 | **开箱内置 Milvus Lite**（Docker Compose 默认单文件向量库），后期需要集群时改一行环境变量即可无缝切 **Milvus REST v2**。 |
| **模型与组件自由度** | 模型权重硬编码进主后端，换个模型堪比重构。 | **双层乐高解耦**：内置的 `EmbeddingGemma 2` 运行在独立 HTTP 容器（`encoder/`），想换模型、搬到远端显卡、或换向量库/存储，全都可以单独拆换。 |
| **外部对象存储安全** | 容易改动或锁死你的原文件。 | **外部桶严格只读**：只挂载不改写；排除素材时「先删向量、再清预览」，绝不动外部原片。 |
| **插件与交互体验** | 手动网页上传 + 静态表格。 | **原生插件 API（`/api/v1/*`）** + **Three.js 3D 影院走廊** + **5 语自由切换** + 首次启动向导弹窗。 |

---

## 推荐配置与为什么默认选 `EmbeddingGemma 2`

### 家庭部署推荐配置（本体与模型分开计算）

CineVec 主打轻量家庭部署——内置 **Milvus Lite** 单文件向量库，无需起沉重的集群；**本体**与**模型**完全解耦，资源分开计算：

| 组件 | CPU / 内存占用 | 磁盘建议 | 说明 |
| :--- | :--- | :--- | :--- |
| **CineVec 本体**（后端 + Milvus Lite + 前端） | **1～2 核 CPU · 不到 0.5 GB 内存**（含 MinIO 约 0.8 GB） | 5 GB+（不含自留原片） | 极度轻量，普通入门 NAS、软路由小主机都能长期常驻。 |
| **编码模型**（默认推荐 **`EmbeddingGemma 2`**） | **2 核+ CPU · 约 2.5 GB 内存**（有显卡/Mac MPS 更快） | 约 2 GB 权重缓存 | 裁剪音频塔后约 440M 参数；可和本体同机跑，也可单独丢在另一台电脑/显卡机上按需连接。 |

- **同机一体家庭推荐配置**：**4 核 CPU · 4～6 GB 内存 · 10 GB+ 磁盘**（N100 小主机、老笔记本、家用 NAS 都能跑起整套语义搜图，零显卡也可用）。
- **分体省内存玩法**：如果 NAS 只有 2 GB 内存，只需在 NAS 常驻 **<0.5 GB 的本体**，把 **~2.5 GB 的 `EmbeddingGemma 2`** 放在日常用的电脑或 Mac 上，批量入库和搜图时通过 `PICA_CLIP_HTTP_BASE_URL` 接入即可。

### 为什么默认采用 `google/embeddinggemma-2`？

在踩过传统 CLIP 各种“中文变文盲、多语要装好几套模型、图片强制被中心裁剪”的坑之后，我们最终将 **`google/embeddinggemma-2`** 设为默认主力编码器：

1. **模块化瘦身，激活仅 ~440M 参数（不吃显存也能跑）**：
   完整模型共 740M 参数（270M 文本骨干 + 170M 视觉塔 + 300M 音频塔）。而 CineVec 检索的是图片、视频关键帧和 `.srt/.vtt/.ass` 字幕文本，内置编码服务默认开启 `EMBED_DISABLE_AUDIO=true`（传 `config_kwargs={"audio_config": None}`），启动时直接跳过 300M 音频塔！消费级甜品卡、MacBook、甚至纯 CPU 机器都能轻松带得动。
2. **百种语言共享同一个 768 维向量空间**：
   中文搜英文台词、英文搜日韩片名、或者中英混输（如 *“海边的 sunset 镜头”*），全部落在同一套 768 维向量空间里，再也不用为中英文分别部署两套模型。
3. **整帧无损理解（告别暴力中心裁剪）**：
   老式 CLIP 预处理常把宽银幕电影悄悄居中裁成正方形，画面边缘的人物或字幕直接被切掉。CineVec 通过 `full_frame_passthrough_v1` 将完整画面直接喂给 EmbeddingGemma 2，构图细节一帧不丢。
4. **8K 超长上下文 + 严格防重加前缀机制**：
   长文案和整段字幕都能吃得下；同时后端契约明确区分查询前缀（`task: search result | query: `）与文档前缀（`title: ... | text: `），`encoder/server.py` 保证**有且仅加一次前缀**，图片请求零干扰。
5. **Apache 2.0 开源协议 + 精度安全防护**：
   商用自托管无顾虑；针对 EmbeddingGemma 2 在 `float16` 下会数值溢出的特性，编码服务主动禁用 fp16，自动选用 **`bfloat16`**（支持的 CUDA 显卡）或 **`float32`**。

---

## 插件生态与 CineGram

CineVec 本体只专注做好「多模态检索与媒资中枢」，所有外部采集/机器人/浏览器插件均通过 `/api/v1/*` + Bearer API Token（在 **设置 → API Token** 一键生成）完全解耦接入，不碰本体数据库。

### 官方配套 Telegram 插件：**[CineGram](https://github.com/Chillwinner/CineGram)**

想把 Telegram 群组和频道里散落的图片、2GB～4GB 大视频变成可秒搜的 3D 素材库，又不重复占硬盘？直接配官方插件：

> **项目地址**：[https://github.com/Chillwinner/CineGram](https://github.com/Chillwinner/CineGram)

- **MTProto 用户号 & Bot 均完全可选**：独立轻量 FastAPI + 5 语 Web 管理台，可按需运行仅 Web、仅 Bot、仅用户号或双模并行。
- **精细化筛选导入**：支持按群配置过滤规则（仅视频/仅图、大小/时长阈值、关键词/屏蔽词、回溯天数），还支持**在网页里先看群消息缩略图，打勾只导入想要的那几条**。
- **相册整组文案与 `#标签` 继承**：解决 Telegram 一条相册发多图只把文案挂在第 1 张的痛点，自动广播给同组所有文件，搜标签和模糊搜话里的词一条不漏。
- **定时存活巡检**：自动探测群改名、公开群转私密、被踢出或单条消息被删，自动刷新跳转链接或回写 `source_invalid` 状态。

你也可以照着 `/api/v1/*` 轻松写出自己的 Discord / Obsidian / 浏览器收藏夹 / NAS 监听插件，完全无需修改 CineVec 本体代码。

---

## 真正解耦：像拼乐高一样自由拼装组件

CineVec 在**容器服务层**与**后端代码层**做了彻底的双向解耦——所有核心部件都是可插拔的积木，没有任何“绑死某一家”的设计：

### 第一层：容器与服务解耦（独立块，按需拆装）

```text
  [ web (Nginx + 3D 前端 :5174) ]
                │  /api 反向代理
                ▼
  [ backend (FastAPI :8008) + 内置 Milvus Lite ]
        ├──► HTTP /api/v1/embed/* ──► [ encoder (EmbeddingGemma 2 :8001) ]  <-- 可单独替换/搬去外置显卡机
        └──► S3 协议               ──► [ minio (:9000) ]                     <-- 可随时换 AWS S3 / R2 / OSS
```

特别说明：`docker-compose.yml` 中 `backend` **刻意不写死 `depends_on: encoder`** —— 一方面让 Web 管理台秒开不卡顿，另一方面哪怕你删掉或停掉 `encoder` 容器、改接外部模型，整个编排依然丝滑启动。

### 第二层：代码六边形架构（Ports & Adapters）

业务编排层（`backend/app/application/`）只面向 [`backend/app/domain/ports.py`](backend/app/domain/ports.py) 中定义的 `Protocol` 接口编程，不直接 `import` Milvus、MinIO 或 PyTorch：

- **开箱支持零代码切换（改 `.env` 即可）**：
  - **向量库**：内置 `Milvus Lite`（`PICA_MILVUS_BACKEND=lite`）↔ 远端 `Milvus REST v2`（`PICA_MILVUS_URI=http://...`）
  - **编码模型**：`EmbeddingGemma 2`（HTTP，768 维）↔ `Chinese-CLIP`（HTTP 或本地 ONNX，512 维）
  - **对象存储**：MinIO / AWS S3 / Cloudflare R2 / 阿里云 OSS（任意 S3 兼容协议）
- **想换成 Qdrant、pgvector、Weaviate 或自研模型？** 只需实现对应 `Protocol` 并在 [`backend/app/main.py`](backend/app/main.py) 的工厂函数里接上即可（共 2 处，流水线与 3D 前端一行不用改）：

| 要替换的组件 | 需实现的接口（`backend/app/domain/ports.py`） | 参考实现（`backend/app/infra/`） | 装配入口（`backend/app/main.py`） |
| :--- | :--- | :--- | :--- |
| **向量数据库** | `VectorIndex` | `milvus_lite.py` / `milvus_rest.py` | `build_vector_index()` |
| **图文编码器** | `ImageTextEncoder` | `embeddinggemma_http.py` / `clip_http.py` / `chinese_clip.py` | `build_encoders()` |
| **字幕编码器** | `SubtitleEncoder` | `embeddinggemma_http.py` / `clip_http.py` / `chinese_clip.py` | `build_encoders()` |
| **平台文件存储** | `FileStore` | `file_store.py` | `build_file_store()` |
| **只读外部数据源** | `SourceReader` | `s3_reader.py` | `build_container()` |

```python
class MyCustomVectorIndex:
    def available(self) -> bool: ...
    def status_message(self) -> str: ...
    def upsert_records(self, modality: str, records: list[dict]) -> int: ...
    def search(self, modality: str, vector: list[float], *, limit: int, offset: int = 0, filter_expr: str | None = None) -> list[dict]: ...
    def delete_vectors(self, refs: list) -> VectorDeleteResult: ...
    def delete_by_asset(self, asset_id: str, collections: list[str] | None = None) -> VectorDeleteResult: ...
```

同时所有向量严格按 `(profile, model_id, model_version, dim, preprocess_version)` 进行空间隔离，绝不会发生跨模型维度串味，老索引和业务数据随时安全保留。

---

## 快速部署与自定义 `docker-compose.yml`

### 方式一：Docker Compose 全家桶一键起飞（已内置 EmbeddingGemma 2）

已打包 **MinIO**（自动建 `cinevec-media` 桶并登记）、内置 **Milvus Lite**、**EmbeddingGemma 2 独立编码服务**、**FFmpeg** 与生产版 **3D 前端**：

```bash
git clone https://github.com/Chillwinner/Cinevec.git
cd Cinevec
cp .env.docker.example .env

# 可选：若 Hugging Face 下载 google/embeddinggemma-2 需要授权，在 .env 中填入 HF_TOKEN
# 可选：国内环境可在 .env 中设置 HF_ENDPOINT=https://hf-mirror.com

# 1. 默认 / CPU 模式一键启动：
docker compose up -d

# 2. 若主机有 NVIDIA 显卡，叠加 GPU 编排启用 bfloat16 加速：
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

浏览器打开 **http://localhost:5174**，首次打开会弹出新手向导检查服务状态并介绍每个按钮的作用（首次启动 `encoder` 会在后台自动拉取模型权重到 `hf_cache` 卷）。

> - **可选开启人脸识别**：构建前在 `.env` 中设置 `PICA_FACE_INSTALL=true` 与 `PICA_FACE_ENABLED=true`，再执行 `docker compose up -d --build`。
> - **可选接入 Telegram 插件（[CineGram](https://github.com/Chillwinner/CineGram)）**：`git clone https://github.com/Chillwinner/CineGram.git && cd CineGram && docker compose up -d --build`，打开 **http://localhost:8018** 按页面内置新手引导 1 分钟即可连上本体。

> 小技巧：如果你只用纯 CPU 跑且想缩小镜像体积，构建前在 `.env` 里取消注释 `TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu` 即可。

---

### 如果你需要自行安装或替换组件，如何修改 `docker-compose.yml`？

#### 场景 1：自备模型服务 / Apple Silicon Mac 原生 MPS 加速 / 接入局域网显卡机

macOS 下的 Docker Desktop 容器拿不到 Apple GPU（MPS），或者你可能已经在另一台有显卡的机器上跑着模型了。拆分只需 3 步：

1. **去掉 `encoder` 容器**：在 [`docker-compose.yml`](docker-compose.yml) 中注释掉（或删掉）`encoder:` 整段；或者直接指定服务名启动其余积木：
   ```bash
   docker compose up -d minio minio-init backend web
   ```
2. **在宿主机或显卡机原生启动编码服务**（以 Mac 开启 Metal `mps` 为例）：
   ```bash
   python3 -m venv .venv-encoder && source .venv-encoder/bin/activate
   pip install "torch>=2.4.0" -r encoder/requirements.txt
   EMBED_DEVICE=mps HF_TOKEN=你的HF_TOKEN uvicorn server:app --app-dir encoder --host 0.0.0.0 --port 8001
   ```
3. **在 `.env` 把地址指过去**：
   ```env
   # 指向当前 Docker 宿主机（如 Mac 原生 MPS）：
   PICA_CLIP_HTTP_BASE_URL=http://host.docker.internal:8001
   # 或者指向局域网另一台显卡服务器：
   # PICA_CLIP_HTTP_BASE_URL=http://192.168.1.50:8001
   ```
   *（若改用 **Chinese-CLIP 512 维** HTTP 服务，顺带把 `.env` 中的 `PICA_MODEL_FAMILY=chinese_clip`、`PICA_MODEL_ARCHITECTURE=ViT-B-16`、`PICA_MODEL_ID=chinese-clip-vit-b-16`、`PICA_MODEL_DIM=512`、`PICA_PREPROCESS_VERSION=chinese_clip_official_square_v1` 改好即可。）*

#### 场景 2：不用内置 Milvus Lite，接现成的独立 Milvus 集群

连 `docker-compose.yml` 都不用动，在 `.env` 改两行即切换为分布式 **Milvus REST v2**：

```env
PICA_MILVUS_BACKEND=rest
PICA_MILVUS_URI=http://你的Milvus地址:19530
```

或者把你自己的 Milvus / etcd 服务段贴进 `docker-compose.yml`，然后填 `PICA_MILVUS_URI=http://milvus:19530`。

#### 场景 3：自带对象存储（公司现有 MinIO / AWS S3 / Cloudflare R2 / 阿里云 OSS）

1. 在 [`docker-compose.yml`](docker-compose.yml) 里注释掉 `minio:` 与 `minio-init:` 两个服务，并删掉 `backend:` 下的 `depends_on: minio-init:` 段；
2. 在 `.env` 填入外部对象存储凭据：
   ```env
   PICA_AUTO_INIT_PLATFORM_STORAGE=true
   PICA_S3_ENDPOINT=https://你的S3端点
   PICA_S3_REGION=us-east-1
   PICA_S3_BUCKET=cinevec-media
   PICA_S3_ACCESS_KEY=你的AccessKey
   PICA_S3_SECRET_KEY=你的SecretKey
   ```

---

### 本地手动开发与测试校验

```bash
cp .env.example backend/.env
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install "pymilvus[milvus_lite]>=2.4.9"
uvicorn app.main:app --host 127.0.0.1 --port 8008

# 新开终端启动前端 (5174)
cd web && npm install && npm run dev

# 运行全量校验
cd backend && pytest -q
PYTHONPATH=backend:encoder backend/.venv/bin/pytest -q encoder/test_server.py
cd web && npm run build && npm test
```

---

## 更新日志

### v1.0（`2026-11-20`）
- **修复**：统一图片与视频检索结果的排序逻辑（[`backend/app/application/search_aggregate.py`](backend/app/application/search_aggregate.py) 与 [`web/src/pages/SearchPage.tsx`](web/src/pages/SearchPage.tsx)），解决高相关度视频被排在低相关度图片之后的问题。
- **优化**：为 Milvus REST v2 增加集合校验缓存（[`backend/app/infra/milvus_rest.py`](backend/app/infra/milvus_rest.py)）并并发执行画面路与字幕路召回（[`backend/app/application/search_service.py`](backend/app/application/search_service.py)），跨网检索延迟降低约 50%。
- **新增**：InsightFace（`buffalo_sc`）人脸检测、People 人物相册聚类、`/api/v1/search/face` 接口及 Docker GPU（`EmbeddingGemma 2`）加速部署支持。
