import type { Transition, Variants } from "motion/react";

export const EASE = [0.16, 1, 0.3, 1] as [number, number, number, number];

export const spring: Transition = { type: "spring", stiffness: 420, damping: 34, mass: 0.9 };
export const softSpring: Transition = { type: "spring", stiffness: 210, damping: 26, mass: 1 };

export const fadeUp: Variants = {
  hidden: { opacity: 0, y: 18 },
  show: { opacity: 1, y: 0, transition: { duration: 0.65, ease: EASE } },
};

export const fadeIn: Variants = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { duration: 0.5, ease: EASE } },
};

export const stagger = (step = 0.06, delay = 0): Variants => ({
  hidden: {},
  show: { transition: { staggerChildren: step, delayChildren: delay } },
});

export const item: Variants = {
  hidden: { opacity: 0, y: 16 },
  show: { opacity: 1, y: 0, transition: { duration: 0.55, ease: EASE } },
};

export const overlay: Variants = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { duration: 0.25 } },
  exit: { opacity: 0, transition: { duration: 0.2 } },
};

export const modal: Variants = {
  hidden: { opacity: 0, y: 18, scale: 0.97 },
  show: { opacity: 1, y: 0, scale: 1, transition: softSpring },
  exit: { opacity: 0, y: 12, scale: 0.98, transition: { duration: 0.18, ease: "easeIn" } },
};
