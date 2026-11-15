import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";

const SESSION_PRELOADED_KEY = "pica.cinema.preloaded";

function DigitColumn({ targetDigit, delayMs }: { targetDigit: number; delayMs: number }) {
  return (
    <span className="preloader-digit-col" aria-hidden="true">
      <span className="preloader-digit-spacer">0</span>
      <span
        className="preloader-digit-track"
        style={{
          transform: `translateY(-${targetDigit * 10}%)`,
          transitionDelay: `${delayMs}ms`,
        }}
      >
        {[0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((d) => (
          <span key={d} className="preloader-digit-cell">
            {d}
          </span>
        ))}
      </span>
    </span>
  );
}

export default function Preloader({
  ready,
  onDone,
}: {
  ready: boolean;
  onDone?: () => void;
}) {
  const [alreadySeen] = useState(() => {
    try {
      return sessionStorage.getItem(SESSION_PRELOADED_KEY) === "1";
    } catch {
      return false;
    }
  });
  const [progress, setProgress] = useState(alreadySeen ? 100 : 0);
  const [visible, setVisible] = useState(!alreadySeen);
  const readyRef = useRef(ready);
  readyRef.current = ready;

  useEffect(() => {
    if (alreadySeen) {
      onDone?.();
      return;
    }
    const start = performance.now();
    let raf = 0;
    let finished = false;

    const step = (now: number) => {
      if (finished) return;
      const elapsed = now - start;
      // 最多等待 1.3s 即放行至 100，避免慢探针阻塞 3D 舞台首屏进入
      const canFinish = readyRef.current || elapsed >= 1300;
      const cap = canFinish ? 100 : 88;
      const target = Math.min(cap, Math.round((elapsed / 900) * 100));
      setProgress((prev) => Math.max(prev, target));
      if (target < 100) {
        raf = requestAnimationFrame(step);
      } else {
        finished = true;
        window.setTimeout(() => {
          setVisible(false);
          try {
            sessionStorage.setItem(SESSION_PRELOADED_KEY, "1");
          } catch {
            /* ignore */
          }
          onDone?.();
        }, 260);
      }
    };

    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [alreadySeen, onDone]);

  const padded = String(Math.min(100, Math.max(0, progress))).padStart(3, "0");
  const d0 = Number(padded[0]);
  const d1 = Number(padded[1]);
  const d2 = Number(padded[2]);
  // 弧长约 111px
  const arcTotal = 111;
  const arcOffset = arcTotal * (1 - progress / 100);

  return (
    <AnimatePresence>
      {visible ? (
        <motion.div
          className="cinema-preloader"
          role="progressbar"
          aria-label="正在加载 Pica 影院档案"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progress}
          initial={{ opacity: 1 }}
          exit={{ opacity: 0, transition: { duration: 0.65, ease: [0.16, 1, 0.3, 1] } }}
        >
          <div className="preloader-arc-wrap" aria-hidden="true">
            <svg viewBox="0 0 108 22" width="108" height="22" fill="none">
              <path
                d="M 4 19 Q 54 -6 104 19"
                stroke="rgba(247,247,247,0.18)"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
              <path
                d="M 4 19 Q 54 -6 104 19"
                stroke="#f7f7f7"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeDasharray={arcTotal}
                strokeDashoffset={arcOffset}
                style={{ transition: "stroke-dashoffset 140ms linear" }}
              />
            </svg>
          </div>

          <div className="preloader-counter" aria-hidden="true">
            <DigitColumn targetDigit={d0} delayMs={0} />
            <DigitColumn targetDigit={d1} delayMs={40} />
            <DigitColumn targetDigit={d2} delayMs={80} />
          </div>

          <p className="preloader-sub">PICA · PRIVATE CINEMA ARCHIVE</p>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
