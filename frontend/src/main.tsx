import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { TooltipProvider } from "./components/ui";
import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import "./style.css";
import "./components/studio.css";
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <TooltipProvider delayDuration={450} skipDelayDuration={150}>
      <App />
    </TooltipProvider>
  </React.StrictMode>,
);
