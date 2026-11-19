<div align="center">

# CineVec

### 시네마급 3D 스크리닝 UI를 갖춘 셀프호스팅 멀티모달 미디어 검색 엔진

**`VID_20240819_final_FINAL2.mp4` 파일명을 기억하느라 고생하지 마세요.**
장면을 말로 설명하거나, 스크린샷을 드롭하거나, 대사를 검색하면 3D WebGL 상영관에서 정확한 타임스탬프로 바로 이동합니다.

[English](README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어** · [Español](README.es.md)

[![EmbeddingGemma 2](https://img.shields.io/badge/Embedding-Gemma%202%20(768d)-4285F4?style=for-the-badge&logo=google&logoColor=white)](https://ai.google.dev/)
[![Milvus Lite / v2](https://img.shields.io/badge/Milvus-Lite%20%2F%20REST%20v2-00A1EA?style=for-the-badge)](https://milvus.io/)
[![FastAPI](https://img.shields.io/badge/FastAPI-Python%203.11%2B-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React + Three.js](https://img.shields.io/badge/React%2019-Three.js%20WebGL-111111?style=for-the-badge&logo=threedotjs&logoColor=white)](https://threejs.org/)
[![MinIO / S3](https://img.shields.io/badge/Storage-S3%20%2F%20MinIO-C72E49?style=for-the-badge&logo=minio&logoColor=white)](https://min.io/)

[주요 기능](#주요-기능) • [화면 미리보기](#화면-미리보기) • [차별점](#다른-도구와-비교한-장점) • [플러그인 & CineGram](#플러그인-생태계--cinegram) • [백엔드 교체](#벡터-db모델스토리지-교체-방법) • [빠른 배포](#빠른-배포-가이드)

</div>

---

## 주요 기능

**CineVec**은 수만 개의 영상 클립과 이미지를 관리하는 편집자·수집가·셀프호스팅 사용자를 위한 **멀티모달 시맨틱 미디어 아카이브**입니다.

- **하나의 도크에서 3가지 검색**: 자연어 장면 검색, 참조 이미지 드래그 앤 드롭 유사 샷 검색, 파일명·태그·설명 기반 초고속 어휘 검색.
- **타임스탬프 즉시 이동 상영관**: 검색된 영상은 매칭된 키프레임 초로 바로 점프하며, 키프레임 필름스트립·커스텀 커버·`.srt/.vtt/.ass` 자막 타임라인을 지원합니다.
- **최초 실행 인터랙티브 온보딩**: 처음 접속하면 설정 점검 모달과 각 버튼 옆 스포트라이트 말풍선 투어가 자동으로 안내합니다.

---

## 화면 미리보기

<table>
  <tr>
    <td width="50%" align="center">
      <img src="docs/images/search-3d-forest.png" alt="CineVec 3D Cinema Semantic Search - forest" />
      <br />
      <sub><b>검색어: <code>"forest"</code></b> — 3D 시네마 복도에서 가장 유사한 장면을 최상단 배치 (cosine <code>0.696</code>)</sub>
    </td>
    <td width="50%" align="center">
      <img src="docs/images/search-3d-dog.png" alt="CineVec 3D Cinema Semantic Search - dog" />
      <br />
      <sub><b>검색어: <code>"dog"</code></b> — 관련 장면을 실시간 랭킹 (cosine <code>0.718</code>)</sub>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <img src="docs/images/activity-pipeline.png" alt="CineVec Activity Dashboard" />
      <br />
      <sub><b>활동 대시보드</b> — 실시간 CPU / RAM / GPU 게이지 및 4단계 파이프라인</sub>
    </td>
    <td width="50%" align="center">
      <img src="docs/images/library-catalog.png" alt="CineVec Library Catalog" />
      <br />
      <sub><b>라이브러리</b> — 필터링, 일괄 태깅, 커스텀 커버, 설명 편집 및 안전한 삭제</sub>
    </td>
  </tr>
</table>

---

## 다른 도구와 비교한 장점

| 항목 | 일반 미디어 갤러리 | CineVec |
| :--- | :--- | :--- |
| **디스크 용량** | 원본 영상을 통째로 복사해 저장 공간 낭비. | **Zero-Retention 모드**: `source_url`만 기록하고 키프레임 추출 직후 원본 임시 파일 삭제. 가벼운 미리보기와 벡터만 유지. |
| **대량 영상 처리** | GPU 임베딩 대기 중 원본 영상이 로컬에 쌓임. | **듀얼 레인 비동기 워커**: 전처리(FFmpeg)와 임베딩 스레드를 분리해 추출 즉시 원본 삭제. |
| **벡터 DB 운영** | Milvus + etcd 클러스터 구축 부담. | **Milvus Lite 기본 내장**(Docker Compose 단일 파일 동작), 환경변수 하나로 **Milvus REST v2** 클러스터 전환 가능. |
| **외부 버킷 보호** | 원본 파일 이동 또는 변경 위험. | **읽기 전용 멀티 버킷**: 외부 S3/MinIO 원본은 절대 수정하지 않으며, 삭제 시 벡터 우선 정리. |
| **플러그인 확장성** | 웹 UI 수동 업로드만 지원. | **플러그인 전용 `/api/v1/*` API**: Bearer 토큰 기반으로 원하는 언어로 직접 플러그인 제작 가능. |

---

## 플러그인 생태계 & CineGram

CineVec 코어는 검색 엔진 역할에 집중하며, 모든 외부 수집기·봇·자동화 도구는 `/api/v1/*`와 Bearer API Token(**Settings → API Tokens**)으로 완전히 분리 연동됩니다.

### 공식 Telegram 플러그인: **[CineGram](https://github.com/Chillwinner/CineGram)**

> **저장소**: [https://github.com/Chillwinner/CineGram](https://github.com/Chillwinner/CineGram)

- **MTProto 계정과 Bot 모두 선택 사항**: Web 전용 / Bot 전용 / MTProto 전용 / 듀얼 모드 지원.
- **선택적 가져오기**: 그룹별 필터 규칙(미디어 유형, 용량, 길이, 키워드, 기간)과 **웹에서 썸네일을 보고 체크한 항목만 가져오는 UI** 제공.
- **앨범 캡션 및 `#태그` 자동 상속**: 여러 파일을 한 번에 올릴 때 첫 메시지에만 붙는 캡션을 그룹 내 모든 파일에 자동 적용.
- **링크 생존 점검**: 채널 이름 변경, 비공개 전환, 메시지 삭제를 주기적으로 점검해 `source_url` 갱신 또는 `source_invalid` 표시.

---

## 벡터 DB·모델·스토리지 교체 방법

CineVec은 **헥사고날 아키텍처(Ports & Adapters)** 로 설계되어 비즈니스 로직은 [`backend/app/domain/ports.py`](backend/app/domain/ports.py)의 `Protocol` 인터페이스에만 의존합니다.

### 1. 코드 수정 없이 교체 가능 (`.env` 설정)
- **벡터 DB**: 내장 **Milvus Lite** (`PICA_MILVUS_BACKEND=lite`) ↔ **Milvus REST v2** (`PICA_MILVUS_URI=http://...`)
- **임베딩 모델**: **EmbeddingGemma 2** (HTTP) ↔ **Chinese-CLIP** (HTTP / 로컬 ONNX)
- **스토리지**: MinIO, AWS S3, Cloudflare R2 등 모든 S3 호환 스토리지

### 2. Qdrant·pgvector·커스텀 모델을 직접 연결하려면?
**네, 해당 `Protocol` 인터페이스를 구현하고 팩토리 함수에 연결하면 됩니다 (비즈니스 로직과 프론트엔드는 수정 불필요):**

| 교체 대상 | 인터페이스 (`backend/app/domain/ports.py`) | 참고 어댑터 (`backend/app/infra/`) | 연결 위치 (`backend/app/main.py`) |
| :--- | :--- | :--- | :--- |
| **벡터 DB** | `VectorIndex` | `milvus_lite.py` / `milvus_rest.py` | `build_vector_index()` |
| **이미지/텍스트 모델** | `ImageTextEncoder` | `embeddinggemma_http.py` / `clip_http.py` | `build_encoders()` |
| **자막 모델** | `SubtitleEncoder` | `clip_http.py` / `chinese_clip.py` | `build_encoders()` |
| **파일 스토리지** | `FileStore` | `file_store.py` | `build_file_store()` |
| **읽기 전용 소스** | `SourceReader` | `s3_reader.py` | `build_container()` |

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

## 빠른 배포 가이드

### 방법 A: Docker Compose 원클릭 배포 (추천 · 2분 완료)

**MinIO**(버킷 자동 생성 및 등록), 내장 **Milvus Lite**, **FFmpeg**, 프로덕션 **3D Web UI**가 한 번에 실행됩니다:

```bash
git clone https://github.com/Chillwinner/Cinevec.git
cd Cinevec
cp .env.docker.example .env
# .env에서 PICA_CLIP_HTTP_BASE_URL에 임베딩 모델 서버 주소 입력
docker compose up -d
```

브라우저에서 **http://localhost:5174**를 열면 최초 실행 가이드가 설정 상태와 UI 버튼 사용법을 순서대로 안내합니다.

### 방법 B: 로컬 수동 실행

```bash
cp .env.example backend/.env

# 백엔드 실행 (8008)
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install "pymilvus[milvus_lite]>=2.4.9"
uvicorn app.main:app --host 127.0.0.1 --port 8008

# 새 터미널에서 프론트엔드 실행 (5174)
cd web && npm install && npm run dev
```
