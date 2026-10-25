import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { ErrorBoundary, reportError } from "./components/ErrorBoundary";
import "./index.css";

window.addEventListener("error", (e) => reportError(`未捕获错误：${e.message}`, e.error?.stack));
window.addEventListener("unhandledrejection", (e) => reportError(`未处理的 Promise 拒绝：${String(e.reason)}`, e.reason?.stack));

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
