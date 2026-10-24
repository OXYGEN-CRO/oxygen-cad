import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import type { Project, State } from "../../src/api";
import type { JsonObject } from "../../src/workspaces/contracts";
import Panel from "../../src/workspaces/modeling/Panel";
import "../../src/tokens.css";
import "../../src/components/ui.css";
import "../../src/style.css";

async function api(path: string, data?: unknown, method = "POST") {
  const response = await fetch(
    `/api/${path}`,
    data === undefined
      ? undefined
      : {
          method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        },
  );
  const result = await response.json();
  if (!response.ok)
    throw new Error(
      typeof result.detail === "string"
        ? result.detail
        : JSON.stringify(result.detail),
    );
  return result;
}
function Fixture() {
  const [state, setState] = useState<State | null>(null);
  const current = useRef<Project | null>(null);
  const [error, setError] = useState("");
  const refresh = async () => {
    const next = await api("state");
    current.current = next.project;
    setState(next);
  };
  useEffect(() => {
    void refresh();
  }, []);
  return (
    <main style={{ maxWidth: 440, padding: 12, margin: "auto" }}>
      <button
        onClick={() => {
          document.documentElement.dataset.theme =
            document.documentElement.dataset.theme === "light"
              ? "dark"
              : "light";
        }}
      >
        Toggle fixture theme
      </button>
      {error && <p role="alert">{error}</p>}
      {state && (
        <Panel
          project={state.project}
          state={state}
          selection={null}
          domainData={((state.project as unknown as { domains?: Record<string, JsonObject> }).domains ?? {}).modeling ?? {}}
          native={async (command, params = {}) => {
            const reply = await api("native/execute", {
              expected_revision: current.current!.revision,
              actor: "human",
              command,
              params,
            });
            current.current = reply.project;
            setState((s) => (s ? { ...s, project: reply.project } : s));
            return reply;
          }}
          inspectNative={() => api("native/inspect")}
          saveDomain={async (domain, data) => {
            const reply = await api(`domains/${domain}`, { expected_revision: current.current!.revision, actor: "human", data: { schema_version: 1, ...data } }, "PUT");
            current.current = reply.project; setState((s) => s ? { ...s, project: reply.project } : s);
          }}
          runDomain={async () => {
            throw new Error("No domain workers in this modeling fixture");
          }}
          refresh={refresh}
          setError={setError}
        />
      )}
    </main>
  );
}
createRoot(document.getElementById("root")!).render(<Fixture />);
