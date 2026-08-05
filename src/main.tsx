import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";
import { setupTelegram } from "./lib/telegram";

// Развернуть окно и покрасить панель нужно до первого кадра:
// иначе карта успевает отрисоваться в половину высоты.
setupTelegram();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

