import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App.tsx";
import "./styles/tokens.css";

const root = document.getElementById("root");
if (!root) {
  throw new Error("missing #root element");
}

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
