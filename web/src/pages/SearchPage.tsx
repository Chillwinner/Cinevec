import { AnimatePresence, motion } from "motion/react";
import {
  type ChangeEvent,
  type FormEvent,
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link } from "react-router-dom";
import {
  ArrowCounterClockwise,
  ArrowRight,
  ArrowUpRight,
  Cube,
  FilmStrip,
  ImageSquare,
  MagnifyingGlass,
  Plus,
  SlidersHorizontal,
  SpinnerGap,
  SquaresFour,
  X,
} from "@phosphor-icons/react";
import { api, authorizedMediaUrl } from "../api";
import { useI18n, type Lang } from "../i18n";
import ArcIndicator from "../cinema/ArcIndicator";
import CinemaCarousel, { type CinemaCarouselHandle, type CinemaSlideItem } from "../cinema/CinemaCarousel";
import ScreeningRoom, { type ScreeningTarget } from "../cinema/ScreeningRoom";
import AddFlow from "../components/AddFlow";
import { AnimatedNumber, Banner, Empty, Segmented } from "../components/ui";
import { EASE, spring } from "../motion";
import type { Asset, LexicalHit, SearchHit, SearchResponse, SystemStatus } from "../types";

type Mode = "text" | "image" | "lexical";
type HitFilter = "all" | "image" | "video" | "subtitle";
type ViewMode = "cinema" | "grid";

function getSourceLabel(lang: Lang, key: string) {
  const isEn = lang === "en";
  const map: Record<string, string> = {
    visual: isEn ? "Visual" : "画面",
    subtitle: isEn ? "Subtitle" : "字幕",
    mixed: isEn ? "Visual + Subtitle" : "画面 + 字幕",
    visual_only: isEn ? "Visual only" : "仅画面",
    subtitle_only: isEn ? "Subtitle only" : "仅字幕",
    visual_and_subtitle: isEn ? "Visual + Subtitle" : "画面 + 字幕",
  };
  return map[key] || key;
}

const textExamplesEn = ["sunset by the sea", "elephant", "lit street in the rain", "oranges on kitchen table"];
const textExamplesZh = ["海边的黄昏", "大象", "雨里亮着灯的街", "厨房桌上的橘子"];
const lexicalExamplesEn = ["IMG_2043", "birthday", "mp4"];
const lexicalExamplesZh = ["IMG_2043", "生日", "mp4"];

function fileName(item: { display_name?: string; object_key: string }) {
  return item.display_name || item.object_key.split("/").pop() || item.object_key;
}

function hitToTarget(hit: SearchHit): ScreeningTarget {
  const cover = hit.previews[0]?.locator || `/api/previews/${hit.asset_id}/frames/0`;
  return {
    assetId: hit.asset_id,
    kind: hit.kind,
    title: fileName(hit),
    coverLocator: cover,
    videoCoverLocator: hit.video_cover_locator ?? cover,
    description: hit.description ?? "",
    sourceUrl: hit.source_url ?? null,
    storeOriginal: hit.store_original ?? (hit.origin ? hit.origin === "hosted" : undefined),
    startSeconds: hit.time_seconds,
    sourceMissing: hit.source_available === false,
    score: hit.score,
    hitSource: hit.hit_method || hit.hit_source,
    subtitleExcerpt: hit.subtitle_excerpt,
    objectKey: hit.object_key,
    collection: hit.collection,
    dim: hit.dim,
    modelId: hit.model_id,
    tags: hit.tags ?? [],
    previews: hit.previews,
  };
}

function assetToTarget(item: LexicalHit | Asset): ScreeningTarget {
  const cover = item.cover_locator || `/api/previews/${item.id}/frames/0`;
  return {
    assetId: item.id,
    kind: item.kind,
    title: fileName(item),
    coverLocator: cover,
    videoCoverLocator: item.video_cover_locator || item.cover_locator || cover,
    description: item.description ?? "",
    sourceUrl: item.source_url ?? null,
    storeOriginal: item.store_original ?? (item.origin ? item.origin === "hosted" : undefined),
    sourceMissing: item.source_deleted === true,
    objectKey: item.object_key,
    tags: item.tags ?? [],
  };
}

function scoreFill(score: number) {
  return Math.max(0, Math.min(1, score));
}

function RotatingPlaceholder({ examples }: { examples: string[] }) {
  const { t } = useI18n();
  const [index, setIndex] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setIndex((current) => (current + 1) % examples.length);
    }, 3200);
    return () => window.clearInterval(timer);
  }, [examples.length]);

  return (
    <span className="ghost-placeholder" aria-hidden="true">
      <AnimatePresence mode="wait">
        <motion.span
          key={examples[index]}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: 0.32, ease: EASE }}
        >
          {t(`e.g. ${examples[index]}`, `例如：${examples[index]}`)}
        </motion.span>
      </AnimatePresence>
    </span>
  );
}

export default function SearchPage() {
  const { lang, t } = useI18n();
  const carouselRef = useRef<CinemaCarouselHandle | null>(null);

  const [viewMode, setViewMode] = useState<ViewMode>("cinema");
  const [mode, setMode] = useState<Mode>("text");
  const [query, setQuery] = useState("");
  const [topN, setTopN] = useState(20);
  const [threshold, setThreshold] = useState<string>("0");
  const [tuneOpen, setTuneOpen] = useState(false);

  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [lexicalItems, setLexicalItems] = useState<LexicalHit[] | null>(null);
  const [lexicalNote, setLexicalNote] = useState<string | null>(null);

  const [imageFile, setImageFile] = useState<File | null>(null);
  const [imagePreview, setImagePreview] = useState<string | null>(null);

  const [libraryTotal, setLibraryTotal] = useState<number | null>(null);
  const [indexedTotal, setIndexedTotal] = useState<number | null>(null);
  const [pendingTotal, setPendingTotal] = useState(0);
  const [showcaseAssets, setShowcaseAssets] = useState<Asset[]>([]);

  const [hitFilter, setHitFilter] = useState<HitFilter>("all");
  const [screening, setScreening] = useState<ScreeningTarget | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  const [activeIndex, setActiveIndex] = useState(0);
  const [scrollFloat, setScrollFloat] = useState(0);

  const loadBootstrap = useCallback(() => {
    void api.status().then(setStatus).catch(() => setStatus(null));
    void api
      .getSettings()
      .then((s) => {
        if (s.search?.top_n) setTopN(s.search.top_n);
      })
      .catch(() => undefined);
    void api.assets({ limit: 1 }).then((data) => setLibraryTotal(data.total)).catch(() => setLibraryTotal(null));
    void api.assets({ limit: 1, indexed: true }).then((data) => setIndexedTotal(data.total)).catch(() => setIndexedTotal(null));
    void api.assets({ limit: 1, pending: true }).then((data) => setPendingTotal(data.total)).catch(() => setPendingTotal(0));
    void api
      .assets({ limit: 24, excluded: false })
      .then((data) => {
        const visual = (data.items || []).filter((item) => item.kind === "image" || item.kind === "video");
        visual.sort((a, b) => Number(Boolean(b.vectorize)) - Number(Boolean(a.vectorize)));
        setShowcaseAssets(visual);
      })
      .catch(() => setShowcaseAssets([]));
  }, []);

  useEffect(() => {
    loadBootstrap();
  }, [loadBootstrap]);

  const milvusReady = ["ok", "configured"].includes(status?.milvus.status || "");
  const modelReady =
    ["ok", "ready"].includes(status?.embedding_model.status || "") ||
    Boolean(status?.embedding_model.inference_ready);
  const connected = Boolean(milvusReady && modelReady);

  function resetResults() {
    setError(null);
    setResult(null);
    setLexicalItems(null);
    setLexicalNote(null);
    setHitFilter("all");
    setActiveIndex(0);
    setScrollFloat(0);
  }

  function setPickedImage(file: File | null) {
    setImageFile(file);
    setImagePreview((current) => {
      if (current) URL.revokeObjectURL(current);
      return file ? URL.createObjectURL(file) : null;
    });
  }

  function onPickImage(event: ChangeEvent<HTMLInputElement>) {
    setPickedImage(event.target.files?.[0] ?? null);
  }

  const extra = {
    top_n: topN,
    score_threshold: (() => {
      const n = Number(threshold);
      if (threshold.trim() === "" || Number.isNaN(n) || n <= 0) return null;
      return n;
    })(),
  };

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setTuneOpen(false);
    resetResults();
    try {
      if (mode === "text") {
        setResult(await api.search(query, extra));
      } else if (mode === "image") {
        if (!imageFile) return;
        setResult(await api.searchImage(imageFile, extra));
      } else {
        const data = await api.lexicalSearch(query);
        setLexicalNote((data.notes || []).join(" "));
        setLexicalItems(data.items as LexicalHit[]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("No results, or search is not connected yet", "没搜到，或检索还没接通"));
    } finally {
      setBusy(false);
    }
  }

  async function searchSimilar(file: File) {
    setMode("image");
    setPickedImage(file);
    setScreening(null);
    setBusy(true);
    resetResults();
    try {
      setResult(await api.searchImage(file, extra));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("No results, or search is not connected yet", "没搜到，或检索还没接通"));
    } finally {
      setBusy(false);
    }
  }

  function spotlight(event: ReactPointerEvent<HTMLFormElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    event.currentTarget.style.setProperty("--spot-x", `${event.clientX - rect.left}px`);
    event.currentTarget.style.setProperty("--spot-y", `${event.clientY - rect.top}px`);
  }

  const canSubmit = mode === "image" ? Boolean(imageFile) : Boolean(query.trim());
  const searched = Boolean(result || lexicalItems);
  const canVisual = Boolean(connected && (indexedTotal ?? 0) > 0);

  const visibleHits = useMemo(() => {
    const items = result?.items || [];
    if (hitFilter === "all") return items;
    if (hitFilter === "image" || hitFilter === "video") return items.filter((item) => item.kind === hitFilter);
    return items.filter(
      (item) => item.hit_source === "subtitle" || item.hit_method === "subtitle_only" || Boolean(item.subtitle_excerpt),
    );
  }, [result, hitFilter]);

  const images = useMemo(() => visibleHits.filter((item) => item.kind === "image"), [visibleHits]);
  const videos = useMemo(() => visibleHits.filter((item) => item.kind === "video"), [visibleHits]);

  // 将当前视图数据（语义检索结果 / 词法匹配 / 馆藏展映）统一映射为 3D 轮播与放映室靶点
  const { slides, screeningMap } = useMemo(() => {
    const nextSlides: CinemaSlideItem[] = [];
    const map = new Map<string, ScreeningTarget>();

    if (result) {
      for (const hit of visibleHits) {
        const target = hitToTarget(hit);
        nextSlides.push({
          id: hit.asset_id,
          assetId: hit.asset_id,
          title: target.title,
          kind: hit.kind,
          coverUrl: target.coverLocator || `/api/previews/${hit.asset_id}/frames/0`,
          score: hit.score,
          timeSeconds: hit.time_seconds,
          hitSource: getSourceLabel(lang, hit.hit_method || hit.hit_source),
          subtitleExcerpt: hit.subtitle_excerpt,
          sourceMissing: hit.source_available === false,
        });
        map.set(hit.asset_id, target);
      }
    } else if (lexicalItems) {
      for (const item of lexicalItems) {
        const target = assetToTarget(item);
        nextSlides.push({
          id: item.id,
          assetId: item.id,
          title: target.title,
          kind: item.kind,
          coverUrl: target.coverLocator || `/api/previews/${item.id}/frames/0`,
          hitSource: item.vectorize ? t("Indexed", "已建索引") : t("Catalog only", "仅登记"),
          sourceMissing: item.source_deleted === true,
        });
        map.set(item.id, target);
      }
    } else {
      for (const item of showcaseAssets) {
        const target = assetToTarget(item);
        nextSlides.push({
          id: item.id,
          assetId: item.id,
          title: target.title,
          kind: item.kind,
          coverUrl: target.coverLocator || `/api/previews/${item.id}/frames/0`,
          hitSource: item.vectorize ? t("Archive Reel", "馆藏展映") : t("Pending index", "待向量化"),
          sourceMissing: item.source_deleted === true,
        });
        map.set(item.id, target);
      }
    }

    return { slides: nextSlides, screeningMap: map };
  }, [result, visibleHits, lexicalItems, showcaseAssets, lang, t]);

  const openSlideById = useCallback(
    (assetId: string) => {
      const target = screeningMap.get(assetId);
      if (target) setScreening(target);
    },
    [screeningMap],
  );

  const openActiveSlide = useCallback(() => {
    if (!slides.length) return;
    const item = slides[((activeIndex % slides.length) + slides.length) % slides.length];
    if (item) openSlideById(item.assetId);
  }, [slides, activeIndex, openSlideById]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (viewMode !== "cinema" || screening || addOpen) return;
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;

      if (event.key === "ArrowLeft") {
        event.preventDefault();
        carouselRef.current?.step(-1);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        carouselRef.current?.step(1);
      } else if (event.key === "Enter" && slides.length > 0) {
        event.preventDefault();
        openActiveSlide();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [viewMode, screening, addOpen, slides.length, openActiveSlide]);

  let idleEmpty: { title: string; body: string; action?: { to: string; label: string } } | null = null;
  if (!searched && !error && !busy && slides.length === 0) {
    if ((libraryTotal ?? 0) === 0) {
      idleEmpty = {
        title: t("Library is empty", "资料库还是空的"),
        body: t(
          "Click Import below to index a MinIO bucket or upload local videos and images.",
          "点击下方「添加」引入 MinIO 桶或上传本地影片图片。",
        ),
      };
    } else if (!canVisual) {
      idleEmpty = {
        title: t("Visual search is not ready yet", "现在还不能按画面搜"),
        body: connected
          ? pendingTotal > 0
            ? t("Files are still extracting frames or embeddings. You can search by filename or tags in the meantime.", "有文件还在抽帧或编码。可以先用「文件名 / 标签」。")
            : t("Only catalog-only assets are in the library. Search them by filename/tags, or click Index in Library for visual search.", "库里有仅登记的文件。文件名或标签能搜到它们；要按画面搜，到资料库点「加入检索」。")
          : t("Text and image search require both the embedding model and Milvus to be ready. You can use Lexical search meanwhile.", "文字和图片搜索需要模型和索引都接通。可以先用「文件名 / 标签」。"),
        action: connected
          ? pendingTotal > 0
            ? { to: "/activity", label: t("Go to Activity", "去仪表盘") }
            : { to: "/library", label: t("Go to Library", "去资料库") }
          : { to: "/settings", label: t("Go to Settings", "去设置") },
      };
    }
  }

  return (
    <div
      className={`cinema-explore-root${viewMode === "cinema" ? " mode-cinema" : " mode-grid"}${dragOver ? " dragging-file" : ""}`}
      onDragOver={(event) => {
        if (event.dataTransfer.types.includes("Files")) {
          event.preventDefault();
          setDragOver(true);
        }
      }}
      onDragLeave={(event) => {
        if (event.currentTarget === event.target) setDragOver(false);
      }}
      onDrop={(event) => {
        const file = Array.from(event.dataTransfer.files).find((item) => item.type.startsWith("image/"));
        setDragOver(false);
        if (!file) return;
        event.preventDefault();
        setMode("image");
        setPickedImage(file);
        resetResults();
      }}
    >
      {/* 顶部影院状态与视图切换条 */}
      <div className="cinema-stage-hud">
        <div className="stage-hud-left">
          {result ? (
            <div className="stage-status-chip">
              <span className="status-tag">SEMANTIC CUT</span>
              <strong>{visibleHits.length}</strong>
              <span>{t("matching frames", "个相近画面")}</span>
            </div>
          ) : lexicalItems ? (
            <div className="stage-status-chip">
              <span className="status-tag">LEXICAL MATCH</span>
              <strong>{lexicalItems.length}</strong>
              <span>{t("matching assets", "个匹配档案")}</span>
            </div>
          ) : (
            <div className="stage-status-chip">
              <span className="status-tag">ARCHIVE REEL</span>
              <span>
                {libraryTotal != null ? <AnimatedNumber value={libraryTotal} /> : "—"} {t("in library", "在库")} ·{" "}
                {indexedTotal != null ? <AnimatedNumber value={indexedTotal} /> : "—"} {t("searchable", "可搜")}
              </span>
              {pendingTotal > 0 ? <span className="stage-pending-dot">{pendingTotal} {t("processing", "处理中")}</span> : null}
            </div>
          )}

          {result ? (
            <div className="stage-filter-row">
              <Segmented
                id="hit-filter"
                ariaLabel={t("Filter results", "结果筛选")}
                value={hitFilter}
                onChange={setHitFilter}
                options={[
                  { value: "all", label: t("All", "全部") },
                  { value: "image", label: t("Images", "图片") },
                  { value: "video", label: t("Videos", "视频") },
                  { value: "subtitle", label: t("Subtitles", "有字幕") },
                ]}
              />
            </div>
          ) : null}

          {searched ? (
            <button type="button" className="stage-reset-btn" onClick={resetResults}>
              <ArrowCounterClockwise size={13} />
              <span>{t("Back to Reel", "返回展映")}</span>
            </button>
          ) : null}
        </div>

        <div className="stage-hud-right">
          <div className="view-mode-switch" data-tour="view-toggle" role="group" aria-label={t("View mode", "视图切换")}>
            <button
              type="button"
              className={viewMode === "cinema" ? "active" : ""}
              onClick={() => setViewMode("cinema")}
            >
              <Cube size={14} weight={viewMode === "cinema" ? "fill" : "regular"} />
              <span>{t("3D Cinema", "3D 影院")}</span>
            </button>
            <button
              type="button"
              className={viewMode === "grid" ? "active" : ""}
              onClick={() => setViewMode("grid")}
            >
              <SquaresFour size={14} weight={viewMode === "grid" ? "fill" : "regular"} />
              <span>{t("Film Grid", "胶片网格")}</span>
            </button>
          </div>
        </div>
      </div>

      {/* 错误提示浮条 */}
      <AnimatePresence>
        {error ? (
          <motion.div
            key="error"
            className="stage-error-float"
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
          >
            <Banner kind="warn" title={t("Search incomplete", "检索未完成")}>{error}</Banner>
          </motion.div>
        ) : null}
      </AnimatePresence>

      {/* 主舞台：3D WebGL 曲面柱面影院 OR 胶片暗室网格 */}
      {viewMode === "cinema" ? (
        <section className="cinema-webgl-stage" aria-label={t("3D Cinema Film Corridor", "3D 电影胶片走廊")}>
          {busy ? (
            <div className="stage-center-state">
              <SpinnerGap size={28} className="spin" />
              <p>{t("Traversing semantic vector space...", "正在穿透高维语义空间...")}</p>
            </div>
          ) : slides.length > 0 ? (
            <>
              <CinemaCarousel
                ref={carouselRef}
                items={slides}
                paused={Boolean(screening || addOpen)}
                onActiveChange={(idx, flt) => {
                  setActiveIndex(idx);
                  setScrollFloat(flt);
                }}
                onSelect={(item) => openSlideById(item.assetId)}
              />
              <div className="stage-arc-slot">
                <ArcIndicator
                  items={slides}
                  activeIndex={activeIndex}
                  scrollFloat={scrollFloat}
                  onPrev={() => carouselRef.current?.step(-1)}
                  onNext={() => carouselRef.current?.step(1)}
                  onSelectActive={openActiveSlide}
                />
              </div>
            </>
          ) : idleEmpty ? (
            <div className="stage-center-state">
              <Empty
                title={idleEmpty.title}
                action={
                  idleEmpty.action ? (
                    <Link className="btn" to={idleEmpty.action.to}>{idleEmpty.action.label}</Link>
                  ) : (
                    <button type="button" className="btn primary" onClick={() => setAddOpen(true)}>
                      {t("Import Media", "添加资源")}
                    </button>
                  )
                }
              >
                {idleEmpty.body}
              </Empty>
            </div>
          ) : (
            <div className="stage-center-state">
              <Empty title={t("No frames match this filter", "当前筛选条件没有匹配画面")}>
                <button type="button" className="btn" onClick={resetResults}>{t("Show Full Reel", "返回全部展映")}</button>
              </Empty>
            </div>
          )}
        </section>
      ) : (
        <section className="cinema-grid-stage">
          {busy ? (
            <SearchSkeleton />
          ) : result ? (
            <div className="results">
              {result.items.length === 0 ? (
                <Empty title={t("No similar frames or subtitles found", "没有找到相近的画面或字幕")} action={<Link className="btn" to="/library">{t("Go to Library", "去资料库")}</Link>}>
                  {t(
                    "Low-confidence results are never padded in. For catalog-only assets, try Lexical search by filename or tags.",
                    "不会为了凑满数量硬塞不相近的内容。库里若有仅登记的文件，用「文件名或标签」找。",
                  )}
                </Empty>
              ) : (
                <>
                  {hitFilter !== "video" && images.length > 0 ? (
                    <div className="result-block">
                      <div className="result-block-head">
                        <span className="eyebrow">{t("Image Frames", "图片帧")}</span>
                        <span className="result-count">{images.length}</span>
                      </div>
                      <div className="hit-grid">
                        {images.map((item, index) => (
                          <SearchHitCard key={item.asset_id} item={item} index={index} onOpen={setScreening} />
                        ))}
                      </div>
                    </div>
                  ) : null}

                  {hitFilter !== "image" && videos.length > 0 ? (
                    <div className="result-block">
                      <div className="result-block-head">
                        <span className="eyebrow">{t("Video Reels", "视频卷")}</span>
                        <span className="result-count">{videos.length}</span>
                      </div>
                      <div className="hit-grid">
                        {videos.map((item, index) => (
                          <SearchHitCard key={item.asset_id} item={item} index={index} onOpen={setScreening} />
                        ))}
                      </div>
                    </div>
                  ) : null}

                  {visibleHits.length === 0 ? <p className="hint">{t("No hits in this category.", "这一类里没有结果。")}</p> : null}
                </>
              )}
            </div>
          ) : lexicalItems ? (
            <div className="results">
              {lexicalNote ? <p className="hint" style={{ marginBottom: 16 }}>{lexicalNote}</p> : null}
              {lexicalItems.length === 0 ? (
                <Empty title={t("No matching filename or tag", "没有这个名字或标签")} action={<Link className="btn" to="/library">{t("Go to Library", "去资料库")}</Link>}>
                  {t("Lexical search uses substring matching and does not auto-correct typos.", "这是包含匹配，不会理解错别字。")}
                </Empty>
              ) : (
                <div className="hit-grid">
                  {lexicalItems.map((item, index) => (
                    <LexicalHitCard key={item.id} item={item} index={index} onOpen={setScreening} />
                  ))}
                </div>
              )}
            </div>
          ) : slides.length > 0 ? (
            <div className="results">
              <div className="result-block-head" style={{ marginBottom: 18 }}>
                <span className="eyebrow">{t("ARCHIVE SHOWCASE · RECENT MEDIA", "ARCHIVE SHOWCASE · 最近档案")}</span>
                <span className="result-count">{slides.length}</span>
              </div>
              <div className="hit-grid">
                {showcaseAssets.map((item, index) => (
                  <LexicalHitCard key={item.id} item={item as LexicalHit} index={index} onOpen={setScreening} />
                ))}
              </div>
            </div>
          ) : idleEmpty ? (
            <Empty
              title={idleEmpty.title}
              action={
                idleEmpty.action ? (
                  <Link className="btn" to={idleEmpty.action.to}>{idleEmpty.action.label}</Link>
                ) : (
                  <button type="button" className="btn primary" onClick={() => setAddOpen(true)}>{t("Import Media", "添加资源")}</button>
                )
              }
            >
              {idleEmpty.body}
            </Empty>
          ) : null}
        </section>
      )}

      {/* 底部悬浮导演控制台（Director Search Dock） */}
      <div className="director-dock-wrap">
        <AnimatePresence>
          {tuneOpen && mode !== "lexical" ? (
            <motion.div
              className="director-tune-popover"
              initial={{ opacity: 0, y: 10, scale: 0.97 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 8, scale: 0.97 }}
              transition={{ duration: 0.2, ease: EASE }}
            >
              <label className="dock-tune-field">
                <span>{t("Top N", "返回数量")}</span>
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={topN}
                  onChange={(e) => setTopN(Number(e.target.value) || 20)}
                />
              </label>
              <label className="dock-tune-field">
                <span>{t("Threshold", "相似阈值")}</span>
                <input
                  type="number"
                  step={0.01}
                  min={0}
                  max={1}
                  placeholder={t("0 (no limit)", "0 (不限制)")}
                  value={threshold}
                  onChange={(e) => setThreshold(e.target.value)}
                />
              </label>
              <span className="dock-tune-hint">
                {t("Cosine similarity is for ranking; set a threshold to trim weak hits.", "余弦相关度仅作排序，设阈值可裁掉低匹配帧")}
              </span>
            </motion.div>
          ) : null}
        </AnimatePresence>

        <form className="director-dock" onSubmit={onSubmit} onPointerMove={spotlight}>
          <div className="dock-mode-tabs" data-tour="search-modes" role="tablist" aria-label={t("Search mode", "检索模式")}>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "text"}
              className={mode === "text" ? "active" : ""}
              onClick={() => {
                setMode("text");
                setError(null);
              }}
            >
              {t("Semantic", "语义")}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "image"}
              className={mode === "image" ? "active" : ""}
              onClick={() => {
                setMode("image");
                setError(null);
              }}
            >
              {t("Image", "以图")}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "lexical"}
              className={mode === "lexical" ? "active" : ""}
              onClick={() => {
                setMode("lexical");
                setError(null);
              }}
            >
              {t("Lexical", "词法")}
            </button>
          </div>

          <div className="dock-divider" />

          {mode === "image" ? (
            <div className="dock-input-area" data-tour="search-input">
              {imagePreview ? (
                <img className="dock-query-thumb" src={imagePreview} alt="" />
              ) : (
                <span className="dock-query-icon">
                  <ImageSquare size={17} />
                </span>
              )}
              <label className="dock-file-label">
                <span>{imageFile ? imageFile.name : t("Choose or drop a reference image...", "选择或拖入一张样图...")}</span>
                <input type="file" accept="image/*" hidden disabled={busy} onChange={onPickImage} />
              </label>
              {imageFile ? (
                <button
                  type="button"
                  className="dock-icon-btn"
                  onClick={() => setPickedImage(null)}
                  aria-label={t("Clear image", "清除图片")}
                >
                  <X size={14} />
                </button>
              ) : null}
            </div>
          ) : (
            <div className="dock-input-area" data-tour="search-input">
              <span className="dock-query-icon">
                <MagnifyingGlass size={17} />
              </span>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label={mode === "lexical" ? t("Filename or tags", "文件名或标签") : t("Visual scene description", "画面语义描述")}
              />
              {!query ? (
                <RotatingPlaceholder
                  examples={
                    mode === "lexical"
                      ? lang === "en"
                        ? lexicalExamplesEn
                        : lexicalExamplesZh
                      : lang === "en"
                        ? textExamplesEn
                        : textExamplesZh
                  }
                />
              ) : (
                <button
                  type="button"
                  className="dock-icon-btn"
                  onClick={() => setQuery("")}
                  aria-label={t("Clear input", "清空输入")}
                >
                  <X size={14} />
                </button>
              )}
            </div>
          )}

          <div className="dock-actions">
            {mode !== "lexical" ? (
              <button
                type="button"
                className={`dock-icon-btn${tuneOpen ? " active" : ""}`}
                onClick={() => setTuneOpen((v) => !v)}
                title={t("Search tuning (Top N / Threshold)", "检索参数 (TopN / 阈值)")}
                aria-label={t("Search tuning", "检索参数")}
              >
                <SlidersHorizontal size={16} />
              </button>
            ) : null}

            <button
              type="button"
              className="dock-add-btn"
              data-tour="import-btn"
              onClick={() => setAddOpen(true)}
              title={t("Import from MinIO or upload new media", "导入 MinIO 资源或上传新媒体")}
            >
              <Plus size={14} weight="bold" />
              <span>{t("Import", "导入")}</span>
            </button>

            <button type="submit" className="dock-submit-btn" disabled={busy || !canSubmit}>
              {busy ? <SpinnerGap size={16} className="spin" /> : <ArrowRight size={16} weight="bold" />}
              <span>{busy ? t("Searching", "检索中") : t("Search", "搜索")}</span>
            </button>
          </div>
        </form>
      </div>

      {/* 拖入图片全屏光晕提示 */}
      <AnimatePresence>
        {dragOver ? (
          <motion.div
            className="cinema-drop-overlay"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <div className="drop-card">
              <ImageSquare size={36} weight="light" />
              <p>{t("Drop image to search visually similar frames in 3D Cinema", "松开图片，立即在 3D 影院空间中搜寻相似构图")}</p>
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>

      {/* 沉浸式放映室详情 */}
      <AnimatePresence>
        {screening ? (
          <ScreeningRoom
            key={screening.assetId}
            target={screening}
            onClose={() => setScreening(null)}
            onSearchSimilar={(file) => void searchSimilar(file)}
          />
        ) : null}
      </AnimatePresence>

      {/* 导入/上传抽屉 */}
      <AddFlow
        open={addOpen}
        onClose={() => {
          setAddOpen(false);
          loadBootstrap();
        }}
      />
    </div>
  );
}

function LexicalHitCard({
  item,
  index,
  onOpen,
}: {
  item: LexicalHit;
  index: number;
  onOpen: (target: ScreeningTarget) => void;
}) {
  const { t } = useI18n();
  const [coverFailed, setCoverFailed] = useState(false);
  const cover = item.cover_locator || `/api/previews/${item.id}/frames/0`;
  const tags = item.tags ?? [];

  return (
    <motion.article
      layout
      className="hit-card"
      initial={{ opacity: 0, y: 18 }}
      animate={{
        opacity: 1,
        y: 0,
        transition: { delay: Math.min(index * 0.035, 0.35), duration: 0.48, ease: EASE },
      }}
      exit={{ opacity: 0, scale: 0.97, transition: { duration: 0.16 } }}
      transition={{ layout: spring }}
    >
      <button
        type="button"
        className="hit-media"
        onClick={() => onOpen(assetToTarget(item))}
      >
        {coverFailed ? (
          <span className="hit-fallback">
            {item.kind === "video" ? <FilmStrip size={22} /> : <ImageSquare size={22} />}
            {item.cover_status === "pending" ? t("Processing", "还在处理") : t("No preview", "无预览")}
          </span>
        ) : (
          <img src={authorizedMediaUrl(cover)} alt="" loading="lazy" onError={() => setCoverFailed(true)} />
        )}
        <span className="hit-kind">
          {item.kind === "video" ? <FilmStrip size={13} /> : <ImageSquare size={13} />}
          {item.kind === "video" ? t("Video", "视频") : t("Image", "图片")}
        </span>
        <span className="hit-open">
          <ArrowUpRight size={15} />
        </span>
      </button>

      <div className="hit-body">
        <strong className="hit-name">{fileName(item)}</strong>
        <p className="hit-meta">
          <span>{item.lifecycle_label || item.status}</span>
          <span className="meta-dot" />
          <span>{item.vectorize ? t("Indexed", "已加入检索") : t("Catalog only", "仅登记")}</span>
        </p>

        <div className="hit-tags">
          {tags.length ? (
            tags.map((tag) => (
              <span className="tag-chip" key={tag}>{tag}</span>
            ))
          ) : (
            <span className="tag-chip muted">{t("No tags yet", "还没有标签")}</span>
          )}
        </div>

        <Link className="hit-link" to={`/library?id=${item.id}`}>
          {t("Open in Library", "在资料库打开")}
          <ArrowUpRight size={13} />
        </Link>
      </div>
    </motion.article>
  );
}

function SearchHitCard({
  item,
  index,
  onOpen,
}: {
  item: SearchHit;
  index: number;
  onOpen: (target: ScreeningTarget) => void;
}) {
  const { lang, t } = useI18n();
  const method = getSourceLabel(lang, item.hit_method || item.hit_source);
  const cover = item.previews[0]?.locator;
  const fill = scoreFill(item.score);

  return (
    <motion.article
      layout
      className="hit-card"
      initial={{ opacity: 0, y: 18 }}
      animate={{
        opacity: 1,
        y: 0,
        transition: { delay: Math.min(index * 0.035, 0.35), duration: 0.48, ease: EASE },
      }}
      exit={{ opacity: 0, scale: 0.97, transition: { duration: 0.16 } }}
      transition={{ layout: spring }}
    >
      <button
        type="button"
        className="hit-media"
        onClick={() => onOpen(hitToTarget(item))}
      >
        {cover ? (
          <img src={authorizedMediaUrl(cover)} alt="" loading="lazy" />
        ) : (
          <span className="hit-fallback">
            <ImageSquare size={22} />
            {t("No preview", "无预览")}
          </span>
        )}
        <span className="hit-kind">
          {item.kind === "video" ? <FilmStrip size={13} /> : <ImageSquare size={13} />}
          {item.kind === "video" ? t("Video", "视频") : t("Image", "图片")}
        </span>
        {item.kind === "video" && item.time_seconds != null ? (
          <span className="hit-time">{item.time_seconds.toFixed(1)}s</span>
        ) : null}
        <span className="hit-open">
          <ArrowUpRight size={15} />
        </span>
      </button>

      <div className="hit-body">
        <strong className="hit-name">{fileName(item)}</strong>
        <p className="hit-meta">
          <span>{method}</span>
          <span className="meta-dot" />
          <span>{item.source_available ? t("Source available", "源文件可用") : t("Source unavailable", "源文件不可用")}</span>
        </p>

        <div className="score-line" title={t("Relevance score is for ranking only, not accuracy", "相关度只用来排序，不是正确率")}>
          <span className="score-value">{item.score.toFixed(4)}</span>
          <span className="score-track">
            <motion.span
              className="score-fill"
              initial={{ scaleX: 0 }}
              animate={{ scaleX: fill }}
              transition={{ duration: 0.9, ease: EASE, delay: 0.2 }}
            />
          </span>
        </div>

        {item.subtitle_excerpt ? <p className="hit-excerpt">{item.subtitle_excerpt}</p> : null}

        {item.previews.length > 1 ? (
          <div className="preview-strip">
            {item.previews.slice(1).map((frame) => (
              <img
                key={`${item.asset_id}-${frame.frame_index}`}
                src={authorizedMediaUrl(frame.locator)}
                alt=""
                loading="lazy"
              />
            ))}
          </div>
        ) : null}

        <Link className="hit-link" to={`/library?id=${item.asset_id}`}>
          {t("Open in Library", "在资料库打开")}
          <ArrowUpRight size={13} />
        </Link>
      </div>
    </motion.article>
  );
}

function SearchSkeleton() {
  return (
    <motion.section className="results" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
      <div className="hit-grid">
        {Array.from({ length: 6 }).map((_, index) => (
          <div className="hit-card skeleton-card" key={index}>
            <div className="skeleton skeleton-media" />
            <div className="hit-body">
              <div className="skeleton skeleton-line" style={{ width: "72%" }} />
              <div className="skeleton skeleton-line" style={{ width: "44%" }} />
            </div>
          </div>
        ))}
      </div>
    </motion.section>
  );
}
