import { lazy, Suspense, type ComponentType } from "react";
import type { DomainId, DomainPanelProps } from "./contracts";

const modules = import.meta.glob<{ default: ComponentType<DomainPanelProps> }>("./*/Panel.tsx");
export const workspaceNames: Record<DomainId, string> = {
  modeling: "Model", assemblies: "Assembly", drawings: "Drawing",
  manufacturing: "Manufacture", simulation: "Simulate", electronics: "Electronics",
  visualization: "Render", interop: "Import and repair", platform: "Project",
};
const panels = Object.fromEntries(Object.entries(modules).map(([path, loader]) =>
  [path.split("/")[1], lazy(loader)]));

export function isWorkspace(value: string): value is DomainId {
  return value in workspaceNames;
}
export function WorkspacePanel({ domain, ...props }: DomainPanelProps & { domain: DomainId }) {
  const Panel = panels[domain];
  return Panel ? <Suspense fallback={<p role="status">Opening {workspaceNames[domain].toLowerCase()}…</p>}>
    <Panel {...props} />
  </Suspense> : <p role="status">This workspace is not installed in this build.</p>;
}
