# Pica

家庭服务器上的自部署图文检索平台（开发中）。本目录为已授权的开发检出，当前停在**真实集成关口**：开发功能已就绪，MinIO / Milvus / 官方 Chinese-CLIP 权重尚未在本实例联调，不再继续实现未授权的新功能。不打包、不把密钥写入仓库。

## 已实现的实际功能

- 分层 FastAPI 后端 + 独立 React（Vite + TypeScript）界面；单进程 SQLite 任务队列与调度。
- S3/MinIO 只读来源接入、导入、刷新与生命周期（媒体完成 ≠ 索引就绪；内容更新后旧向量在新写入并关联完整前不参与检索；确认缺失仍可查并标失效）。
- FFmpeg 抽帧（默认每 5 秒 / 可改）、预览与模型输入独立生成；外部字幕手工关联。
- 官方 Milvus REST v2 适配与检索聚合：文字走画面+字幕（画面优先），以图搜图只走画面；同视频画面分取 max；默认 Top 20，单视频最多 5 帧预览且不足不补。
- 可选单用户登录（默认关闭）。开启须完整账号并明确确认沿用预设或自定义签名密钥。预设只写入 **gitignored** 的本地开发配置（`backend/.env` 中的 `PICA_JWT_SECRET`），不进源代码、仓库、接口响应或页面。
- 可配置的 CPU 本地 **Chinese-CLIP 官方分路 ONNX** 图文编码器加载路径（不下载模型本体）。配置必须明确 `model_family=chinese_clip`、官方结构（如 `ViT-B-16`）以及输入输出签名。文件缺失或不支持的 checkpoint 返回明确 blocked，不会用随机或哈希向量充数。字幕编码器为独立端口，默认可与 text 侧共用同一文本 ONNX，语义质量 **待验证**。两路按需加载，并在同一推理进程内共享模型实例（batch=1）。合成 ONNX 测试只覆盖加载/形状/归一化，**不代表实际检索效果已验证**。
- 来源密钥入库后 API 只返回是否已配置的布尔值，不回显明文；探测失败信息会脱敏。保存来源配置本身不表示连接成功。

## 尚未联调、不得假装已接通的依赖

- 本机已有的 MinIO（S3 兼容）与 Milvus：未提供连接信息前，状态为未配置，搜索不会返回假结果。
- 官方 Chinese-CLIP 权重与词表：需要本地已有的 `*.img.fp32.onnx`（或 fp16）、`*.txt.fp32.onnx`（或 fp16）以及官方 `cn_clip/clip/vocab.txt`。当前开发实例默认不配置这些路径，编码器保持阻断。
- 可选依赖 `onnxruntime` 只安装在项目 `backend/.venv`。若当前解释器（例如 Python 3.14）没有可用轮子，会如实记录兼容性，不改系统 Python。
- 字幕共用 text 侧的语义质量、以及真实权重下的检索效果，均待接入后验证。
- 图视频混合 TopN 计数默认按资产，仍为待确认默认；接口保留 `counting_mode=video`。

## 目录

- `backend/` Python FastAPI 分层服务
- `web/` 独立 React（Vite + TypeScript）界面

## 环境与启动

使用本机已有 Python（开发机当前为 3.14.x）与 Node.js。不使用 Docker，不做打包。FFmpeg 优先使用虚拟环境中的 `imageio-ffmpeg`（不改动系统安装）。

本地开发配置：把仓库根目录的 `.env.example` 复制为 `backend/.env`（该文件已被 gitignore）。文件中只保留占位符示例，不要把真实密钥写进示例文件或仓库。登录鉴权在配置里应保持 `PICA_AUTH_ENABLED=false`，直到你在设置页手动开启。不要改系统全局环境，也不要把变量写进其他项目的 shell。

### 启动后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
# 可选：若要用官方 Chinese-CLIP ONNX，再在本 venv 安装 onnxruntime（不要装到系统 Python）
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

健康检查：http://127.0.0.1:8000/api/health  
状态：http://127.0.0.1:8000/api/status

本开发实例绑定 IPv4 的 `127.0.0.1:8000`。

### 启动前端

```bash
cd web
npm install
npm run dev -- --host 127.0.0.1 --port 5174
```

本项目开发界面：http://127.0.0.1:5174 （`/api` 代理到后端 8000）。若 5173 已被其他项目占用，请继续使用 5174，不要去停掉别人的进程。

## 测试

```bash
cd backend
source .venv/bin/activate
pytest -q
```

测试会在临时目录自生成小视频或小型合成 ONNX，明确不是用户数据，也不会写入默认界面或开发数据库。合成编码器测试不宣称检索效果。

```bash
cd web
npm run build
```

## 真实集成需要的最小资料字段（请自行填入本地配置或界面，不要在聊天里发送明文密码）

以下只列出字段名，便于接入时对照。密钥与口令在本机填写，接口与文档都不回显。

**MinIO / S3 来源（界面「来源」或探测用字段）**

- `endpoint`（服务地址）
- `bucket`
- `region`（可空）
- `access_key` / `secret_key`（保存后只显示是否已配置）
- 可选：前缀 / 来源名称 / 角色（输入源或平台存储）

**Milvus**

- `PICA_MILVUS_URI`
- `PICA_MILVUS_DB`（默认 `_default`）
- `PICA_MILVUS_TOKEN` 或 `PICA_MILVUS_USER` + `PICA_MILVUS_PASSWORD`
- `PICA_VECTOR_PROFILE`、`PICA_MODEL_ID`、`PICA_MODEL_VERSION`、`PICA_MODEL_DIM`（须与所用模型特征维一致）

**官方 Chinese-CLIP CPU ONNX（不下载；路径指向你已有的本地文件）**

- `PICA_MODEL_FAMILY=chinese_clip`（只支持这一族，不会臆造通用适配）
- `PICA_MODEL_ARCHITECTURE`：`ViT-B-16` / `ViT-L-14` / `ViT-L-14-336` / `ViT-H-14` / `RN50`
- `PICA_IMAGE_ONNX_PATH`：官方 `{prefix}.img.fp32.onnx` 或 `.img.fp16.onnx`（fp16 可能带有不可单独挪走的 `.extra_file` 伴随文件）
- `PICA_TEXT_ONNX_PATH`：官方 `{prefix}.txt.fp32.onnx` 或 `.txt.fp16.onnx`
- `PICA_TOKENIZER_VOCAB_PATH`：官方 `cn_clip/clip/vocab.txt`（Google 中文 BERT WordPiece 词表）
- 签名默认与官方部署文档一致，一般无需改：`image` / `unnorm_image_features`，`text` / `unnorm_text_features`，`PICA_TEXT_CONTEXT_LENGTH=52`
- `PICA_SUBTITLE_SHARE_TEXT_ENCODER=true`（字幕端口共用 text 侧，质量待验证）

官方预处理按 OFA-Sys `image_transform`：将图像 Resize 到结构对应分辨率的正方形（ViT-B-16 / ViT-L-14 / ViT-H-14 / RN50 为 224，ViT-L-14-336 为 336，BICUBIC），转 RGB，缩放到 `[0,1]`，再按 CLIP 均值方差归一化。官方该函数不再额外做 CenterCrop。文本按官方 `tokenize`：`[CLS]` + WordPiece + `[SEP]`，默认长度 52。推理后对 `unnorm_*_features` 做 L2 归一化。

**登录（可选，当前开发实例保持关闭）**

- `PICA_AUTH_ENABLED=false`（实例开关仍以设置页 / 运行时库为准，默认关闭）
- `PICA_JWT_SECRET` 仅写在 gitignored 的 `backend/.env`
- 若以后要开启：在设置页登记账号密码，并明确确认沿用预设或自定义密钥。不要在聊天中发送密码。

## 检索与生命周期摘要

- 向量集合按 `pica_{profile}_{model}_{version}_{dim}_{modality}` 隔离，按需创建，不删除他人集合。
- 预览通过 `/api/previews/...` 后端读取，不签发对象存储的长期公开地址。
- Fake VectorIndex / Encoder 仅用于测试，不是运行时回退。
- 不要在共享开发实例上随意开启登录或写入真实用户密码，以免把自己锁在外面。


## 真实接入（本地开发例外）

远程图文编码、S3 与 Milvus 各自走独立 infra 适配器，application / domain 只依赖端口。远程 HTTP 编码器与本地 ONNX 实现可替换，禁止同时启用以免混入不可比较空间。凭证只写在 gitignored 的 `backend/.env`（权限 600），接口不回显。当前开发实例登录保持关闭。

远程 Chinese-CLIP 特征服务经 OpenAPI 与实调用核对：`GET /health`，`POST /api/v1/embed/text`（`{text}` → `{dimension,vector}`），`POST /api/v1/embed/texts`（`{texts}` → `{count,dimension,vectors}`），`POST /api/v1/embed/image`（multipart `file` → `{dimension,vector}`）。该服务无认证；`ViT-B-16` / `int8_dynamic` / 维度 512，返回向量已 L2 归一化，文本与图片同属该空间。不要把该服务自己的 Milvus 连接当成 Pica 的集合。

对象存储请使用 Pica 专用桶（例如 `pica-dev`）及前缀：`source/` 放测试源，`previews/` 为既有衍生预览键。不要扫描或改写其它已有桶。Milvus 使用库 `default`（不是 `_default`），只创建 `pica_*` 集合，不改动已有集合（例如 `video_frames_v1`）。
