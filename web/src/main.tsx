import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { MotionConfig } from "motion/react";
import App from "./App";
import { I18nProvider } from "./i18n";
import "./styles.css";
import "./cinema/cinema.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <I18nProvider>
      <MotionConfig reducedMotion="user">
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </MotionConfig>
    </I18nProvider>
  </StrictMode>,
);
