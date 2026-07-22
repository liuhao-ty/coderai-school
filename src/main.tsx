import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter } from "react-router-dom";
import App from "./App";
import { AppErrorBoundary } from "./components/AppErrorBoundary";
import "./styles.css";
import "reactflow/dist/style.css";
import { hydrateSecureAuth } from "./lib/api";

void hydrateSecureAuth().catch(() => undefined).finally(() => {
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <AppErrorBoundary>
        <HashRouter>
          <App />
        </HashRouter>
      </AppErrorBoundary>
    </React.StrictMode>
  );
});
