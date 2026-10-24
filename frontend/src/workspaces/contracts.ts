import type { Project, State, Selection } from "../api";

export type DomainId = "modeling" | "assemblies" | "drawings" | "manufacturing" | "simulation" | "electronics" | "visualization" | "interop" | "platform";
export type JsonObject = Record<string, unknown>;
export interface NativeReply { project: Project; result: unknown; }
export interface DomainPanelProps {
  project: Project;
  state: State;
  selection: Selection | null;
  domainData: JsonObject;
  native: (command: string, params?: JsonObject) => Promise<NativeReply>;
  inspectNative: () => Promise<JsonObject>;
  saveDomain: (domain: DomainId, data: JsonObject) => Promise<void>;
  runDomain: (domain: DomainId, operation: string, input?: JsonObject) => Promise<JsonObject>;
  refresh: () => Promise<void>;
  setError: (message: string) => void;
}
