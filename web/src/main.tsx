import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App.tsx";
import { store } from "./store/store.ts";
import { SseClient } from "./sync/EventSource.ts";
import "./styles/tokens.css";

const root = document.getElementById("root");
if (!root) {
  throw new Error("missing #root element");
}

// Bootstrap the live data pipeline: aiohttp /events → SseClient → in-memory
// store → component subscriptions. The cockpit auto-reconnects on drop; the
// sync dot derives status from event age (live/slow/lost/connecting).
new SseClient(store).start();

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
