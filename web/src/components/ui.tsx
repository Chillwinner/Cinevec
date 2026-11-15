import { animate, motion, useMotionValue, useTransform } from "motion/react";
import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Info, WarningCircle } from "@phosphor-icons/react";
import { EASE, spring } from "../motion";

export function Portal({ children }: { children: ReactNode }) {
  return createPortal(children, document.body);
}

export function AnimatedNumber({
  value,
  format,
}: {
  value: number;
  format?: (value: number) => string;
}) {
  const motionValue = useMotionValue(0);
  const text = useTransform(motionValue, (latest) =>
    format ? format(latest) : Math.round(latest).toLocaleString("zh-CN"),
  );

  useEffect(() => {
    const controls = animate(motionValue, value, { duration: 0.9, ease: EASE });
    return () => controls.stop();
  }, [motionValue, value]);

  return <motion.span className="num">{text}</motion.span>;
}

export function PageHead({
  eyebrow,
  title,
  desc,
  actions,
}: {
  eyebrow?: string;
  title: string;
  desc?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-head">
      <div className="page-head-copy">
        {eyebrow ? (
          <motion.p
            className="eyebrow"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, ease: EASE }}
          >
            {eyebrow}
          </motion.p>
        ) : null}
        <motion.h2
          className="page-title"
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease: EASE, delay: 0.04 }}
        >
          {title}
        </motion.h2>
        {desc ? (
          <motion.p
            className="page-desc"
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, ease: EASE, delay: 0.1 }}
          >
            {desc}
          </motion.p>
        ) : null}
      </div>
      {actions ? (
        <motion.div
          className="page-head-actions"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease: EASE, delay: 0.16 }}
        >
          {actions}
        </motion.div>
      ) : null}
    </div>
  );
}

export function Banner({
  kind = "info",
  title,
  children,
}: {
  kind?: "info" | "warn" | "danger";
  title: string;
  children?: ReactNode;
}) {
  const Icon = kind === "info" ? Info : WarningCircle;
  return (
    <motion.div
      className={`banner ${kind}`}
      role="status"
      initial={{ opacity: 0, y: -10, scale: 0.99 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.35, ease: EASE }}
    >
      <Icon className="banner-icon" size={17} />
      <div>
        <strong>{title}</strong>
        {children ? <div className="banner-body">{children}</div> : null}
      </div>
    </motion.div>
  );
}

export function Pill({ status, children }: { status?: string; children: ReactNode }) {
  const tone =
    status === "ok" || status === "completed" || status === "enabled"
      ? "ok"
      : status === "failed" || status === "danger"
        ? "danger"
        : status === "unconfigured" || status === "paused" || status === "blocked"
          ? "warn"
          : status === "running" || status === "waiting"
            ? "info"
            : status === "accent"
              ? "accent"
              : "";
  return <span className={`pill ${tone}`}>{children}</span>;
}

export function Empty({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <motion.div
      className="empty"
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.55, ease: EASE }}
    >
      <div className="empty-mark" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
      <h3>{title}</h3>
      <p>{children}</p>
      {action ? <div className="empty-action">{action}</div> : null}
    </motion.div>
  );
}

export function Segmented<T extends string>({
  id,
  value,
  onChange,
  options,
  ariaLabel,
}: {
  id: string;
  value: T;
  onChange: (value: T) => void;
  options: { value: T; label: ReactNode }[];
  ariaLabel?: string;
}) {
  return (
    <div className="seg" role="tablist" aria-label={ariaLabel}>
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="tab"
            aria-selected={active}
            className={active ? "on" : ""}
            onClick={() => onChange(option.value)}
          >
            {active ? (
              <motion.span
                className="seg-pill"
                layoutId={`seg-${id}`}
                transition={spring}
              />
            ) : null}
            <span className="seg-label">{option.label}</span>
          </button>
        );
      })}
    </div>
  );
}
