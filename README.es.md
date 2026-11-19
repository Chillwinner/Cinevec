<div align="center">

# CineVec

### Buscador Multimodal Autoalojado con Sala de Proyección 3D

**Deja de jugar al detective con `VID_20240819_final_FINAL2.mp4`.**
Describe una escena, arrastra una captura o busca una frase de subtítulos — CineVec te lleva al segundo exacto del vídeo dentro de un corredor 3D WebGL.

[English](README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · **Español**

[![EmbeddingGemma 2](https://img.shields.io/badge/Embedding-Gemma%202%20(768d)-4285F4?style=for-the-badge&logo=google&logoColor=white)](https://ai.google.dev/)
[![Milvus Lite / v2](https://img.shields.io/badge/Milvus-Lite%20%2F%20REST%20v2-00A1EA?style=for-the-badge)](https://milvus.io/)
[![FastAPI](https://img.shields.io/badge/FastAPI-Python%203.11%2B-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React + Three.js](https://img.shields.io/badge/React%2019-Three.js%20WebGL-111111?style=for-the-badge&logo=threedotjs&logoColor=white)](https://threejs.org/)
[![MinIO / S3](https://img.shields.io/badge/Storage-S3%20%2F%20MinIO-C72E49?style=for-the-badge&logo=minio&logoColor=white)](https://min.io/)

[Qué hace](#qué-hace-cinevec) • [Capturas](#galería-visual) • [Ventajas](#por-qué-cinevec-destaca) • [Requisitos y EmbeddingGemma 2](#configuración-recomendada-y-por-qué-embeddinggemma-2) • [Plugins y CineGram](#ecosistema-de-plugins--cinegram) • [Arquitectura Lego Desacoplada](#desacoplamiento-real-combina-modelos-bd-y-almacenamiento-como-lego) • [Despliegue y Compose](#despliegue-rápido-y-personalización-de-docker-composeyml)

</div>

---

## Qué hace CineVec

**CineVec** es un **motor multimodal autoalojado de archivo y búsqueda semántica** para editores de vídeo, coleccionistas y entusiastas del *self-hosting* con miles de clips imposibles de ordenar a mano.

- **Tres modos en un solo dock**: búsqueda semántica en lenguaje natural, búsqueda visual arrastrando una imagen de referencia y búsqueda léxica instantánea por nombre, etiquetas y descripciones.
- **Sala de proyección con salto exacto**: los resultados de vídeo saltan directamente al segundo del fotograma coincidente, con tira de fotogramas, portadas personalizadas y línea de tiempo de subtítulos `.srt/.vtt/.ass`.
- **Interfaz en 5 idiomas y guía inicial**: soporta `EN / 中文 / 日本語 / 한국어 / ES` (inglés por defecto, recuerda tu elección en `localStorage`), más un asistente interactivo que revisa tu configuración al arrancar.

---

## Galería visual

<table>
  <tr>
    <td width="50%" align="center">
      <img src="docs/images/search-3d-forest.png" alt="CineVec 3D Cinema Semantic Search - forest" />
      <br />
      <sub><b>Consulta: <code>"forest"</code></b> — Corredor 3D resaltando la mejor toma (cosine <code>0.696</code>)</sub>
    </td>
    <td width="50%" align="center">
      <img src="docs/images/search-3d-dog.png" alt="CineVec 3D Cinema Semantic Search - dog" />
      <br />
      <sub><b>Consulta: <code>"dog"</code></b> — Clasificación instantánea de escenas similares (cosine <code>0.718</code>)</sub>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <img src="docs/images/activity-pipeline.png" alt="CineVec Activity Dashboard" />
      <br />
      <sub><b>Panel de actividad</b> — Telemetría CPU / RAM / GPU y pipeline en 4 etapas</sub>
    </td>
    <td width="50%" align="center">
      <img src="docs/images/library-catalog.png" alt="CineVec Library Catalog" />
      <br />
      <sub><b>Biblioteca</b> — Filtros, etiquetado masivo, portadas personalizadas y purga segura</sub>
    </td>
  </tr>
</table>

---

## Por qué CineVec destaca

| Característica | Galerías tradicionales | CineVec |
| :--- | :--- | :--- |
| **Espacio en disco** | Duplican terabytes de vídeo en su carpeta propietaria. | **Modo Zero-Retention**: guarda `source_url`, miniaturas ligeras y vectores; borra el archivo original temporal en cuanto extrae fotogramas. |
| **Ingesta masiva** | Una GPU lenta bloquea todo y acumula vídeos en disco. | **Worker de doble carril**: `preprocess` (FFmpeg) y `embed` corren en hilos separados; el vídeo bruto se elimina al instante. |
| **BD Vectorial** | Exigen montar un clúster Milvus/etcd completo. | **Milvus Lite integrado** (un solo archivo en Docker Compose), o cambia una variable para usar **Milvus REST v2**. |
| **Libertad de stack** | Un único modelo CLIP soldado al código principal. | **Arquitectura Lego en dos niveles**: el servicio EmbeddingGemma 2 corre en su propio contenedor (`encoder/`) vía HTTP; cámbialo o muévelo cuando quieras. |
| **Buckets externos** | Mueven o alteran tus archivos. | **Multi-Bucket S3/MinIO solo lectura**: jamás toca tus buckets externos y purga vectores antes de limpiar vistas previas. |
| **Extensibilidad y UX** | Solo subida manual desde la web. | **API de plugins `/api/v1/*`** + **Corredor 3D WebGL** + **Web UI en 5 idiomas**. |

---

## Configuración recomendada y por qué `EmbeddingGemma 2`

### Hardware recomendado

| Nivel | CPU / GPU / RAM | Disco | Uso típico | Despliegue sugerido |
| :--- | :--- | :--- | :--- | :--- |
| **Inicial (Solo CPU)** | 4 vCPU · 8 GB RAM | 20 GB SSD | Pruebas y bibliotecas pequeñas (<10k fotogramas) | `docker compose up -d` estándar (imagen CPU ligera con `TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu`) |
| **Uso diario (Recomendado)** | 6+ núcleos · **GPU NVIDIA (6–8 GB VRAM)** o **Apple Silicon (16 GB+ RAM unificada)** · 16 GB RAM | 50 GB+ NVMe | Ingesta rápida de vídeo y búsqueda fluida | Linux/Windows GPU: `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d` (`bfloat16`); Mac: ejecutar `encoder/server.py` nativo con **MPS** |
| **Equipo / Gran escala** | 8+ núcleos · RTX 3080/4080+ · 32 GB+ RAM | 200 GB+ NVMe | +100k fotogramas y múltiples plugins | Mover `encoder` a un servidor GPU dedicado y cambiar `PICA_MILVUS_BACKEND=rest` hacia un clúster Milvus v2 |

### ¿Por qué elegimos `google/embeddinggemma-2` por defecto?

1. **Modular y ligero (~440M parámetros activos)**:
   El modelo completo suma 740M parámetros (270M texto + 170M visión + 300M audio). Como CineVec indexa imágenes, fotogramas y subtítulos, `EMBED_DISABLE_AUDIO=true` (`audio_config=None`) omite los 300M de la torre de audio, reduciendo drásticamente el consumo de VRAM y RAM.
2. **Más de 100 idiomas en un único espacio de 768 dimensiones**:
   Busca en español, inglés, chino, japonés o coreano —incluso mezclándolos— y acierta en las mismas escenas y subtítulos sin mantener múltiples índices.
3. **Fotograma completo sin recorte central**:
   Envía el fotograma intacto (`full_frame_passthrough_v1`) sin mutilar bordes ni subtítulos en vídeos panorámicos.
4. **Ventana de contexto de 8K y prefijos asimétricos sin duplicación**:
   Separa instrucciones de consulta y documento con aplicación única garantizada.
5. **Licencia Apache 2.0 y protección contra `float16`**:
   Evita desbordamientos numéricos exigiendo **`bfloat16`** o **`float32`** (prohíbe `float16`).

---

## Ecosistema de Plugins & CineGram

CineVec se mantiene limpio como núcleo de búsqueda; los bots, scripts y extensiones se conectan mediante `/api/v1/*` con Bearer API Tokens (**Settings → API Tokens**).

### Plugin oficial de Telegram: **[CineGram](https://github.com/Chillwinner/CineGram)**

> **Repositorio**: [https://github.com/Chillwinner/CineGram](https://github.com/Chillwinner/CineGram)

- **Cuenta MTProto y Bot totalmente opcionales**: funciona en modo solo Web, solo Bot, solo MTProto o dual.
- **Importación selectiva**: reglas de filtrado por grupo **más** un explorador web de miniaturas para marcar solo los mensajes que deseas importar.
- **Herencia de texto y `#etiquetas` en álbumes**: propaga automáticamente la descripción del primer archivo al resto del álbum de Telegram.
- **Monitor de disponibilidad**: detecta cambios de nombre, pasos a grupo privado o mensajes borrados y actualiza `source_url` o marca `source_invalid`.

---

## Desacoplamiento real: combina modelos, BD y almacenamiento como Lego

CineVec está desacoplado en **dos niveles**:

1. **Nivel de servicios (Docker Compose)**: `web`, `backend` (+ Milvus Lite), `encoder` y `minio` hablan solo por HTTP / REST / S3. `backend` no fuerza `depends_on: encoder`, así que puedes quitar o mover el contenedor del modelo cuando quieras.
2. **Nivel de código (Arquitectura Hexagonal — Ports & Adapters)**: la aplicación solo depende de los `Protocol` en [`backend/app/domain/ports.py`](backend/app/domain/ports.py).
   - **Cambios sin código vía `.env`**: Milvus Lite ↔ Milvus REST v2, EmbeddingGemma 2 ↔ Chinese-CLIP, MinIO ↔ AWS S3 / R2 / OSS.
   - **¿Quieres usar Qdrant, pgvector o tu propio modelo?** Implementa el `Protocol` correspondiente y regístralo en [`backend/app/main.py`](backend/app/main.py) sin tocar el pipeline ni la UI 3D:

| Componente | Interfaz (`backend/app/domain/ports.py`) | Referencia (`backend/app/infra/`) | Fábrica (`backend/app/main.py`) |
| :--- | :--- | :--- | :--- |
| **BD Vectorial** | `VectorIndex` | `milvus_lite.py` / `milvus_rest.py` | `build_vector_index()` |
| **Modelo Imagen/Texto** | `ImageTextEncoder` | `embeddinggemma_http.py` / `clip_http.py` / `chinese_clip.py` | `build_encoders()` |
| **Modelo Subtítulos** | `SubtitleEncoder` | `embeddinggemma_http.py` / `clip_http.py` / `chinese_clip.py` | `build_encoders()` |
| **Almacenamiento** | `FileStore` | `file_store.py` | `build_file_store()` |
| **Fuente solo lectura** | `SourceReader` | `s3_reader.py` | `build_container()` |

---

## Despliegue rápido y personalización de `docker-compose.yml`

### Despliegue completo en 1 clic (con EmbeddingGemma 2 incluido)

```bash
git clone https://github.com/Chillwinner/Cinevec.git
cd Cinevec
cp .env.docker.example .env
# Opcional: define HF_TOKEN (y/o HF_ENDPOINT) en .env si Hugging Face pide autenticación

# Por defecto / CPU:
docker compose up -d

# Con aceleración NVIDIA GPU (bfloat16):
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

Abre **http://localhost:5174** — el asistente inicial comprobará tus servicios y te guiará botón por botón.

---

### Cómo editar `docker-compose.yml` si prefieres usar tus propios servicios

#### Receta 1: Usar tu propio modelo, Apple Silicon MPS en Mac o GPU remota
Docker en macOS no accede a la GPU Metal; en Mac conviene correr el codificador de forma nativa:
1. Comenta la sección `encoder:` en [`docker-compose.yml`](docker-compose.yml) (o ejecuta `docker compose up -d minio minio-init backend web`).
2. Lanza el codificador en tu Mac o servidor GPU:
   ```bash
   python3 -m venv .venv-encoder && source .venv-encoder/bin/activate
   pip install "torch>=2.4.0" -r encoder/requirements.txt
   EMBED_DEVICE=mps HF_TOKEN=tu_token uvicorn server:app --app-dir encoder --host 0.0.0.0 --port 8001
   ```
3. Apunta `PICA_CLIP_HTTP_BASE_URL=http://host.docker.internal:8001` en `.env` (o la IP de tu servidor GPU; para Chinese-CLIP 512d ajusta además `PICA_MODEL_FAMILY=chinese_clip` y `PICA_MODEL_DIM=512`).

#### Receta 2: Cambiar Milvus Lite por un clúster Milvus REST v2 externo
No hace falta editar `docker-compose.yml`; solo cambia dos líneas en `.env`:
```env
PICA_MILVUS_BACKEND=rest
PICA_MILVUS_URI=http://tu-milvus:19530
```

#### Receta 3: Usar tu propio S3 (AWS S3 / Cloudflare R2 / MinIO existente)
Comenta `minio:`, `minio-init:` y `backend.depends_on` en [`docker-compose.yml`](docker-compose.yml) y rellena tus credenciales `PICA_S3_*` en `.env`.
