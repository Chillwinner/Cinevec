import { useEffect, useImperativeHandle, useRef, forwardRef } from "react";
import * as THREE from "three";
import gsap from "gsap";
import { createSlideMaterial } from "./cylinderShader";
import { authorizedMediaUrl } from "../api";

export type CinemaSlideItem = {
  id: string;
  assetId: string;
  title: string;
  kind: string;
  coverUrl: string;
  score?: number | null;
  timeSeconds?: number | null;
  hitSource?: string | null;
  subtitleExcerpt?: string | null;
  sourceMissing?: boolean;
};

export type CinemaCarouselHandle = {
  scrollTo: (index: number) => void;
  step: (delta: number) => void;
};

type Props = {
  items: CinemaSlideItem[];
  paused?: boolean;
  onActiveChange?: (index: number, floatIndex: number) => void;
  onSelect?: (item: CinemaSlideItem, index: number) => void;
};

const ASPECT = 0.5625; // 16:9 宽银幕画幅比例，与 webtemp 保持一致
const TOTAL_ARC_DEG = 180;

function drawCover(
  ctx: CanvasRenderingContext2D,
  img: HTMLImageElement,
  w: number,
  h: number,
) {
  const iw = img.naturalWidth || 1;
  const ih = img.naturalHeight || 1;
  const ir = iw / ih;
  const cr = w / h;
  let sx = 0;
  let sy = 0;
  let sw = iw;
  let sh = ih;
  if (ir > cr) {
    sw = ih * cr;
    sx = (iw - sw) / 2;
  } else {
    sh = iw / cr;
    sy = (ih - sh) / 2;
  }
  ctx.drawImage(img, sx, sy, sw, sh, 0, 0, w, h);
}

const CinemaCarousel = forwardRef<CinemaCarouselHandle, Props>(function CinemaCarousel(
  { items, paused = false, onActiveChange, onSelect },
  ref,
) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const pausedRef = useRef(paused);
  pausedRef.current = paused;

  const callbacksRef = useRef({ onActiveChange, onSelect });
  callbacksRef.current = { onActiveChange, onSelect };

  const stateRef = useRef<{
    pos: number;
    target: number;
    spacing: number;
    n: number;
    jumpTo: (idx: number) => void;
    stepBy: (delta: number) => void;
  }>({
    pos: 0,
    target: 0,
    spacing: 1,
    n: 0,
    jumpTo: () => undefined,
    stepBy: () => undefined,
  });

  useImperativeHandle(ref, () => ({
    scrollTo: (index: number) => stateRef.current.jumpTo(index),
    step: (delta: number) => stateRef.current.stepBy(delta),
  }));

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !items.length) return;

    const n = items.length;
    let width = host.clientWidth || window.innerWidth;
    let height = host.clientHeight || window.innerHeight;

    const renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: true,
      powerPreference: "high-performance",
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(width, height);
    renderer.setClearColor(0x080808, 1);
    host.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const fov = 45;
    const fovRad = THREE.MathUtils.degToRad(fov);
    let camDist = height / (2 * Math.tan(fovRad / 2));
    const camera = new THREE.PerspectiveCamera(fov, width / height, 0.1, camDist * 6);
    camera.position.set(0, 0, camDist);
    camera.lookAt(0, 0, 0);

    const computeLayout = (w: number, h: number) => {
      const isSmall = w < 768;
      const widthRatio = isSmall ? 0.78 : 0.58;
      const maxFromHeight = Math.max(400, (h - 200) / ASPECT);
      const slideW = Math.min(1280, w * widthRatio, maxFromHeight);
      const slideH = slideW * ASPECT;
      const gap = isSmall ? 28 : 56;
      const spacing = slideW + gap;
      const loopW = Math.max(n, 9) * spacing;
      const arcRad = THREE.MathUtils.degToRad(TOTAL_ARC_DEG);
      const radiusX = loopW / arcRad;
      const radiusZ = radiusX * 0.85;
      const ovalRatio = radiusZ / radiusX;
      const lightDist = slideW * 0.5853;
      const lightRadius = slideW * 0.8621;
      return { slideW, slideH, spacing, loopW, radiusX, radiusZ, ovalRatio, lightDist, lightRadius };
    };

    let layout = computeLayout(width, height);
    const lightPos = new THREE.Vector3(0, 0, layout.lightDist);
    let activeGeo = new THREE.PlaneGeometry(layout.slideW, layout.slideH, 32, 16);

    const emptyData = new Uint8Array([16, 16, 18, 255]);
    const fallbackTex = new THREE.DataTexture(emptyData, 1, 1);
    fallbackTex.needsUpdate = true;

    let destroyed = false;
    const meshes: THREE.Mesh<THREE.PlaneGeometry, THREE.ShaderMaterial>[] = [];
    const createdTextures: THREE.Texture[] = [fallbackTex];
    const hoverPull = new Array<number>(n).fill(0);
    const hoverOriginX = new Array<number>(n).fill(0.5);
    const hoverOriginY = new Array<number>(n).fill(0.5);

    const texW = 1024;
    const texH = Math.round(texW * ASPECT);

    const loadSlideTexture = (item: CinemaSlideItem, mat: THREE.ShaderMaterial, idx: number): Promise<void> => {
      return new Promise((resolve) => {
        const canvas = document.createElement("canvas");
        canvas.width = texW;
        canvas.height = texH;
        const ctx = canvas.getContext("2d");
        const tex = new THREE.CanvasTexture(canvas);
        tex.generateMipmaps = false;
        tex.minFilter = THREE.LinearFilter;
        tex.magFilter = THREE.LinearFilter;
        tex.anisotropy = Math.min(4, renderer.capabilities.getMaxAnisotropy());
        createdTextures.push(tex);

        const drawFallback = () => {
          if (!ctx) return;
          ctx.fillStyle = "#121215";
          ctx.fillRect(0, 0, texW, texH);
          ctx.fillStyle = "rgba(247,247,247,0.45)";
          ctx.font = "500 26px Inter, sans-serif";
          ctx.textAlign = "center";
          ctx.fillText(item.title || "NO PREVIEW", texW / 2, texH / 2);
          tex.needsUpdate = true;
          mat.uniforms.map.value = tex;
          gsap.to(mat.uniforms.uContentAlpha, { value: 1, duration: 0.45, ease: "power2.out" });
        };

        if (!item.coverUrl || destroyed) {
          drawFallback();
          resolve();
          return;
        }

        const img = new Image();
        img.crossOrigin = "anonymous";
        activeImages.push(img);
        img.onload = () => {
          if (destroyed || !ctx) {
            resolve();
            return;
          }
          drawCover(ctx, img, texW, texH);
          const grad = ctx.createLinearGradient(0, texH * 0.65, 0, texH);
          grad.addColorStop(0, "rgba(8,8,8,0)");
          grad.addColorStop(1, "rgba(8,8,8,0.42)");
          ctx.fillStyle = grad;
          ctx.fillRect(0, texH * 0.65, texW, texH * 0.35);

          tex.needsUpdate = true;
          mat.uniforms.map.value = tex;
          gsap.to(mat.uniforms.uContentAlpha, {
            value: 1,
            duration: 0.55,
            delay: Math.min(idx * 0.02, 0.16),
            ease: "power2.out",
          });
          resolve();
        };
        img.onerror = () => {
          if (!destroyed) drawFallback();
          resolve();
        };
        img.src = authorizedMediaUrl(item.coverUrl);
      });
    };

    const activeImages: HTMLImageElement[] = [];
    const mats: THREE.ShaderMaterial[] = [];

    items.forEach((item, i) => {
      const mat = createSlideMaterial(
        fallbackTex,
        new THREE.Vector2(layout.slideW, layout.slideH),
        layout.radiusX,
        layout.ovalRatio,
        lightPos,
        layout.lightRadius,
      );
      const mesh = new THREE.Mesh(activeGeo, mat);
      mesh.userData = { index: i, item };
      scene.add(mesh);
      meshes.push(mesh);
      mats.push(mat);
    });

    // 按焦点由近及远排队加载纹理（0, 1, n-1, 2, n-2...），避免背面幻灯片抢占首屏并发连接
    const order: number[] = [];
    const seen = new Set<number>();
    if (n > 0) {
      order.push(0);
      seen.add(0);
      for (let d = 1; d < n; d++) {
        const right = d % n;
        const left = (n - (d % n)) % n;
        if (!seen.has(right)) {
          seen.add(right);
          order.push(right);
        }
        if (!seen.has(left)) {
          seen.add(left);
          order.push(left);
        }
      }
    }

    let queueCursor = 0;
    const pumpNext = () => {
      if (destroyed || queueCursor >= order.length) return;
      const idx = order[queueCursor++];
      void loadSlideTexture(items[idx], mats[idx], idx).finally(() => {
        if (!destroyed) pumpNext();
      });
    };
    const concurrency = Math.min(4, order.length);
    for (let c = 0; c < concurrency; c++) pumpNext();

    let pos = 0;
    let target = 0;
    let prevPos = 0;
    let smoothedVel = 0;
    let isDragging = false;
    let startX = 0;
    let startPos = 0;
    let dragDistance = 0;
    let suppressClick = false;
    let hoveredIdx: number | null = null;
    let hasPointer = false;
    let hoverNdcX = 0;
    let hoverNdcY = 0;
    let lightTargetX = 0;
    let lightTargetY = 0;
    let lightCurX = 0;
    let lightCurY = 0;
    let lastReportedIdx = -1;

    const raycaster = new THREE.Raycaster();
    const pointerNdc = new THREE.Vector2();

    const wrapOffset = (rawOffset: number) => {
      if (n <= 3) return rawOffset;
      const totalSpan = n * layout.spacing;
      let d = ((rawOffset % totalSpan) + totalSpan) % totalSpan;
      if (d > totalSpan / 2) d -= totalSpan;
      return d;
    };

    const nearestTargetFor = (idx: number) => {
      if (n <= 3) return -idx * layout.spacing;
      const curStep = Math.round(-pos / layout.spacing);
      const curWrapped = ((curStep % n) + n) % n;
      let diff = idx - curWrapped;
      if (diff > n / 2) diff -= n;
      if (diff < -n / 2) diff += n;
      return -(curStep + diff) * layout.spacing;
    };

    stateRef.current = {
      pos,
      target,
      spacing: layout.spacing,
      n,
      jumpTo: (idx: number) => {
        const clamped = ((idx % n) + n) % n;
        target = nearestTargetFor(clamped);
      },
      stepBy: (delta: number) => {
        const curIdx = Math.round(-target / layout.spacing);
        const nextIdx = curIdx + delta;
        if (n <= 3) {
          const clamped = Math.max(0, Math.min(n - 1, nextIdx));
          target = -clamped * layout.spacing;
        } else {
          target = -nextIdx * layout.spacing;
        }
      },
    };

    const onPointerDown = (e: PointerEvent) => {
      isDragging = true;
      startX = e.clientX;
      startPos = target;
      dragDistance = 0;
      host.setPointerCapture(e.pointerId);
    };

    const onPointerMove = (e: PointerEvent) => {
      const rect = host.getBoundingClientRect();
      const ndcX = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      const ndcY = -((e.clientY - rect.top) / rect.height) * 2 + 1;
      hoverNdcX = ndcX;
      hoverNdcY = ndcY;
      hasPointer = true;
      lightTargetX = ndcX * layout.slideW * 0.55;
      lightTargetY = ndcY * layout.slideH * 0.55;

      if (!isDragging) return;
      const dx = e.clientX - startX;
      dragDistance = Math.max(dragDistance, Math.abs(dx));
      target = startPos + dx * 1.35;
      if (n <= 3) {
        const minT = -(n - 1) * layout.spacing;
        target = Math.max(minT - 120, Math.min(120, target));
      }
    };

    const onPointerUp = (e: PointerEvent) => {
      if (!isDragging) return;
      isDragging = false;
      try {
        host.releasePointerCapture(e.pointerId);
      } catch {
        /* ignore */
      }
      if (n <= 3) {
        const minT = -(n - 1) * layout.spacing;
        target = Math.max(minT, Math.min(0, target));
      }
      // 吸附到最近的卡片中心
      const snapped = Math.round(target / layout.spacing) * layout.spacing;
      target = snapped;
      if (dragDistance > 6) {
        suppressClick = true;
        window.setTimeout(() => {
          suppressClick = false;
        }, 40);
      }
    };

    let snapTimer: number | null = null;
    const onWheel = (e: WheelEvent) => {
      if (pausedRef.current) return;
      e.preventDefault();
      const delta = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
      target -= delta * 0.85;
      if (n <= 3) {
        const minT = -(n - 1) * layout.spacing;
        target = Math.max(minT, Math.min(0, target));
      }
      if (snapTimer !== null) window.clearTimeout(snapTimer);
      snapTimer = window.setTimeout(() => {
        target = Math.round(target / layout.spacing) * layout.spacing;
      }, 180);
    };

    const onClick = (e: MouseEvent) => {
      if (suppressClick || pausedRef.current) return;
      const rect = host.getBoundingClientRect();
      pointerNdc.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      pointerNdc.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointerNdc, camera);
      const hits = raycaster.intersectObjects(meshes, false);
      if (!hits.length) return;
      const hitMesh = hits[0].object as THREE.Mesh;
      const idx = hitMesh.userData.index as number;
      const item = hitMesh.userData.item as CinemaSlideItem;
      const curCenterIdx = ((Math.round(-pos / layout.spacing) % n) + n) % n;
      if (idx === curCenterIdx) {
        callbacksRef.current.onSelect?.(item, idx);
      } else {
        target = nearestTargetFor(idx);
      }
    };

    const onResize = () => {
      if (!host) return;
      width = host.clientWidth || window.innerWidth;
      height = host.clientHeight || window.innerHeight;
      const oldSpacing = layout.spacing;
      const curUnits = pos / oldSpacing;
      const targetUnits = target / oldSpacing;

      layout = computeLayout(width, height);
      camDist = height / (2 * Math.tan(fovRad / 2));
      camera.aspect = width / height;
      camera.position.z = camDist;
      camera.updateProjectionMatrix();
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      renderer.setSize(width, height);

      pos = curUnits * layout.spacing;
      target = targetUnits * layout.spacing;
      prevPos = pos;

      // 窗口或容器尺寸变化时必须同步重建几何体，否则旧宽高卡片会与新 spacing 重叠
      const nextGeo = new THREE.PlaneGeometry(layout.slideW, layout.slideH, 32, 16);
      const prevGeo = activeGeo;
      activeGeo = nextGeo;

      meshes.forEach((m) => {
        m.geometry = nextGeo;
        m.material.uniforms.uPanelRadius.value = layout.radiusX;
        m.material.uniforms.uPanelOvalRatio.value = layout.ovalRatio;
        m.material.uniforms.uSlideSize.value.set(layout.slideW, layout.slideH);
        m.material.uniforms.uLightRadius.value = layout.lightRadius;
      });
      prevGeo.dispose();
    };

    const ro = new ResizeObserver(() => onResize());
    ro.observe(host);

    host.addEventListener("pointerdown", onPointerDown);
    host.addEventListener("pointermove", onPointerMove);
    host.addEventListener("pointerup", onPointerUp);
    host.addEventListener("pointercancel", onPointerUp);
    host.addEventListener("wheel", onWheel, { passive: false });
    host.addEventListener("click", onClick);

    let raf = 0;
    const tick = () => {
      if (destroyed) return;
      raf = requestAnimationFrame(tick);
      if (pausedRef.current || document.hidden) return;

      pos += (target - pos) * 0.1;
      const rawVel = pos - prevPos;
      prevPos = pos;
      smoothedVel += (rawVel - smoothedVel) * 0.16;

      lightCurX += (lightTargetX - lightCurX) * 0.1;
      lightCurY += (lightTargetY - lightCurY) * 0.1;
      lightPos.set(lightCurX, lightCurY, layout.lightDist);

      // 悬停 Raycast
      if (hasPointer && !isDragging) {
        pointerNdc.set(hoverNdcX, hoverNdcY);
        raycaster.setFromCamera(pointerNdc, camera);
        const hits = raycaster.intersectObjects(meshes, false);
        if (hits.length) {
          const hit = hits[0];
          hoveredIdx = (hit.object as THREE.Mesh).userData.index as number;
          if (hit.uv) {
            hoverOriginX[hoveredIdx] = hit.uv.x;
            hoverOriginY[hoveredIdx] = hit.uv.y;
          }
        } else {
          hoveredIdx = null;
        }
      } else {
        hoveredIdx = null;
      }

      meshes.forEach((mesh, i) => {
        const rawOffset = i * layout.spacing + pos;
        const offset = wrapOffset(rawOffset);
        const angle = offset / layout.radiusX;
        const x = Math.sin(angle) * layout.radiusX;
        const z = (Math.cos(angle) - 1) * layout.radiusZ;
        const normDist = Math.min(1.5, Math.abs(offset) / (layout.spacing * 1.4));

        // 侧边微缩与微暗，突出焦点画幅
        const scale = 1 - Math.min(0.14, normDist * 0.1);
        const dim = 1 - Math.min(0.45, normDist * 0.34);
        const alpha = Math.abs(angle) > Math.PI * 0.68 ? 0 : 1 - Math.max(0, (Math.abs(angle) - 0.85) * 1.5);

        mesh.position.set(x, 0, z);
        mesh.rotation.y = angle * layout.ovalRatio;
        mesh.scale.set(scale, scale, 1);

        const targetPull = hoveredIdx === i ? 24 : 0;
        hoverPull[i] += (targetPull - hoverPull[i]) * 0.14;

        const mat = mesh.material;
        const cleanVel = Math.abs(smoothedVel) < 0.25 ? 0 : smoothedVel * 0.11;
        mat.uniforms.uVelocity.value = THREE.MathUtils.clamp(cleanVel, -9, 9);
        mat.uniforms.uCurve.value = normDist * 0.9;
        mat.uniforms.uDim.value = dim;
        mat.uniforms.uAlpha.value = Math.max(0, Math.min(1, alpha));
        mat.uniforms.uPullAmount.value = hoverPull[i];
        mat.uniforms.uPullOrigin.value.set(hoverOriginX[i], hoverOriginY[i]);
        mesh.renderOrder = Math.round((1.5 - normDist) * 10);
        mesh.visible = alpha > 0.02;
      });

      renderer.render(scene, camera);

      const floatIdx = -pos / layout.spacing;
      const activeIdx = ((Math.round(floatIdx) % n) + n) % n;
      if (activeIdx !== lastReportedIdx) {
        lastReportedIdx = activeIdx;
      }
      callbacksRef.current.onActiveChange?.(activeIdx, floatIdx);
    };

    raf = requestAnimationFrame(tick);

    return () => {
      destroyed = true;
      activeImages.forEach((im) => {
        im.onload = null;
        im.onerror = null;
        im.src = "";
      });
      cancelAnimationFrame(raf);
      if (snapTimer !== null) window.clearTimeout(snapTimer);
      ro.disconnect();
      host.removeEventListener("pointerdown", onPointerDown);
      host.removeEventListener("pointermove", onPointerMove);
      host.removeEventListener("pointerup", onPointerUp);
      host.removeEventListener("pointercancel", onPointerUp);
      host.removeEventListener("wheel", onWheel);
      host.removeEventListener("click", onClick);

      meshes.forEach((m) => {
        scene.remove(m);
        m.material.dispose();
      });
      activeGeo.dispose();
      createdTextures.forEach((t) => t.dispose());
      renderer.dispose();
      if (renderer.domElement.parentNode === host) {
        host.removeChild(renderer.domElement);
      }
    };
  }, [items]);

  return (
    <div
      ref={hostRef}
      className="cinema-stage-canvas"
      aria-label="3D 柱面影院画廊（支持滚轮与拖拽浏览）"
    />
  );
});

export default CinemaCarousel;
