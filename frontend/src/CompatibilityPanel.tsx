import { InspectorSection } from "./components/InspectorSection";
import { Button, Input } from "./components/ui";
import type { Project } from "./generated";

export type ComponentInterfaceInput = {
  provider: string;
  sku: string;
  asset_sha256?: string | null;
  interface_id: string;
  kind?: "planar_through_holes";
  units?: "mm" | "in";
  frame?: {
    assembly_frame: string;
    origin?: [number, number, number];
    x_axis?: [number, number, number];
    y_axis?: [number, number, number];
  } | null;
  holes?: { label: string; center: [number, number]; diameter: number }[];
  tolerance?: { position: number; diameter: number } | null;
  evidence?: {
    kind: "source" | "measured";
    reference: string;
    source_field: string;
    sha256: string;
  } | null;
};

export type CompatibilityInputs = {
  a: string;
  b: string;
  fastenerDiameterMm: number | "";
  thicknessMm: number | "";
  edgeClearanceMm: number | "";
  webClearanceMm: number | "";
  machiningClearanceMm: number | "";
};

export type CompareRequest = {
  a: ComponentInterfaceInput;
  b: ComponentInterfaceInput;
  fastener_diameter_mm: number | null;
};
export type AdapterRequest = Omit<CompareRequest, "fastener_diameter_mm"> & {
  thickness_mm: number;
  edge_clearance_mm: number;
  web_clearance_mm: number;
  machining_clearance_mm: number;
};
type SourceSignature = {
  provider: string;
  sku: string;
  interface_id: string;
  asset_sha256: string | null;
  signature: string;
};
export type CompatibilityResult = {
  status: "compatible" | "incompatible" | "unknown";
  overall_status: "unknown";
  scope: "mechanical_mounting_registration_only";
  sources: SourceSignature[];
  reasons: {
    code: string;
    message: string;
    hole?: string | null;
    measured_mm?: number | null;
    limit_mm?: number | null;
  }[];
  limits: string[];
};
export type AdapterResult = {
  project: Project;
  sources: SourceSignature[];
  acceptance: {
    method: "independent_native_step_geometry";
    width_mm: number;
    height_mm: number;
    thickness_mm: number;
    expected_volume_mm3: number;
    measured_volume_mm3: number;
    native_geometry_signature: string;
    minimum_web_mm: number;
  };
  limits: string[];
};

export type CompatibilityPanelProps = {
  inputs: CompatibilityInputs;
  onInputsChange: (inputs: CompatibilityInputs) => void;
  result?: CompatibilityResult | null;
  proposal?: AdapterResult | null;
  busy?: boolean;
  error?: string | null;
  onCompare: (request: CompareRequest) => void;
  onGenerate: (request: AdapterRequest) => void;
};

function parseInterface(text: string): ComponentInterfaceInput {
  const parsed: unknown = JSON.parse(text);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
    throw new Error("Each interface must be a JSON object.");
  const item = parsed as Record<string, unknown>;
  if (
    typeof item.provider !== "string" ||
    typeof item.sku !== "string" ||
    typeof item.interface_id !== "string"
  )
    throw new Error(
      "Each interface requires provider, sku, and interface_id. The server validates geometry and evidence.",
    );
  return parsed as ComponentInterfaceInput;
}

function Provenance({ sources }: { sources: SourceSignature[] }) {
  return (
    <ul>
      {sources.map((s, index) => (
        <li key={`${index}:${s.signature}`}>
          <strong>
            {s.provider} / {s.sku} / {s.interface_id}
          </strong>
          <div>
            Asset SHA-256:{" "}
            <code style={{ overflowWrap: "anywhere" }}>
              {s.asset_sha256 ?? "missing"}
            </code>
          </div>
          <div>
            Interface signature:{" "}
            <code style={{ overflowWrap: "anywhere" }}>{s.signature}</code>
          </div>
        </li>
      ))}
    </ul>
  );
}

/** Controlled inputs and results: parent owns server calls, revision checks, and errors. */
export function CompatibilityPanel({
  inputs,
  onInputsChange,
  result,
  proposal,
  busy = false,
  error,
  onCompare,
  onGenerate,
}: CompatibilityPanelProps) {
  let pair:
    { a: ComponentInterfaceInput; b: ComponentInterfaceInput } | undefined;
  let parseError = "";
  try {
    pair = { a: parseInterface(inputs.a), b: parseInterface(inputs.b) };
  } catch (e) {
    parseError = e instanceof Error ? e.message : "Invalid interface JSON";
  }
  const validAdapter =
    [inputs.thicknessMm, inputs.edgeClearanceMm, inputs.webClearanceMm].every(
      (v) => typeof v === "number" && Number.isFinite(v) && v > 0,
    ) &&
    typeof inputs.machiningClearanceMm === "number" &&
    Number.isFinite(inputs.machiningClearanceMm) &&
    inputs.machiningClearanceMm >= 0;
  const fields: [keyof Omit<CompatibilityInputs, "a" | "b">, string][] = [
    ["fastenerDiameterMm", "Fastener diameter (mm)"],
    ["thicknessMm", "Plate thickness (mm)"],
    ["edgeClearanceMm", "Minimum edge clearance (mm)"],
    ["webClearanceMm", "Minimum web clearance (mm)"],
    ["machiningClearanceMm", "Added hole diameter clearance (mm)"],
  ];
  return (
    <InspectorSection title="Sourced mounting compatibility">
      <p>
        Compare explicit planar mounting interfaces. Overall physical,
        electrical, and functional fit remains unknown.
      </p>
      {(["a", "b"] as const).map((side) => (
        <label className="field" key={side}>
          <span>Interface {side.toUpperCase()} JSON</span>
          <textarea
            className="ui-input"
            aria-label={`Interface ${side.toUpperCase()} JSON`}
            rows={10}
            spellCheck={false}
            value={inputs[side]}
            onChange={(event) =>
              onInputsChange({ ...inputs, [side]: event.target.value })
            }
          />
        </label>
      ))}
      {pair && (
        <div>
          {[pair.a, pair.b].map((item, index) => (
            <p key={index}>
              {item.provider} / {item.sku}:{" "}
              {item.evidence?.kind ?? "missing evidence"} —{" "}
              {item.evidence?.reference ?? "Provide a dimension source"}
              {item.evidence && (
                <span>
                  {" "}
                  ({item.evidence.source_field}; SHA-256{" "}
                  <code style={{ overflowWrap: "anywhere" }}>
                    {item.evidence.sha256}
                  </code>
                  )
                </span>
              )}
            </p>
          ))}
        </div>
      )}
      {fields.map(([key, label]) => (
        <label className="field" key={key}>
          <span>{label}</span>
          <Input
            type="number"
            aria-label={label}
            step="any"
            min={0}
            value={inputs[key]}
            onChange={(event) =>
              onInputsChange({
                ...inputs,
                [key]:
                  event.target.value === "" ? "" : Number(event.target.value),
              })
            }
          />
        </label>
      ))}
      {parseError && <p role="status">{parseError}</p>}
      {error && <p role="alert">{error}</p>}
      <div className="button-row">
        <Button
          disabled={busy || !pair}
          onClick={() =>
            pair &&
            onCompare({
              ...pair,
              fastener_diameter_mm:
                inputs.fastenerDiameterMm === ""
                  ? null
                  : inputs.fastenerDiameterMm,
            })
          }
        >
          Compare mounting
        </Button>
        <Button
          disabled={busy || !pair || !validAdapter}
          onClick={() =>
            pair &&
            validAdapter &&
            onGenerate({
              ...pair,
              thickness_mm: Number(inputs.thicknessMm),
              edge_clearance_mm: Number(inputs.edgeClearanceMm),
              web_clearance_mm: Number(inputs.webClearanceMm),
              machining_clearance_mm: Number(inputs.machiningClearanceMm),
            })
          }
        >
          Generate editable adapter
        </Button>
      </div>
      {busy && <p role="status">Checking sourced interfaces…</p>}
      {result && (
        <section aria-label="Mounting comparison result" aria-live="polite">
          <p>
            <strong>Mounting registration: {result.status}</strong>. Overall
            fit: {result.overall_status}.
          </p>
          <ul>
            {result.reasons.map((reason, index) => (
              <li key={index}>
                {reason.hole && `${reason.hole}: `}
                {reason.message}
                {reason.measured_mm != null &&
                  ` (${reason.measured_mm.toFixed(4)} mm; limit ${reason.limit_mm?.toFixed(4)} mm)`}
              </li>
            ))}
          </ul>
          <Provenance sources={result.sources} />
          <ul>
            {result.limits.map((limit) => (
              <li key={limit}>{limit}</li>
            ))}
          </ul>
        </section>
      )}
      {proposal && (
        <section aria-label="Editable adapter proposal" aria-live="polite">
          <p>
            Editable plate: {proposal.acceptance.width_mm.toFixed(3)} ×{" "}
            {proposal.acceptance.height_mm.toFixed(3)} ×{" "}
            {proposal.acceptance.thickness_mm.toFixed(3)} mm. Native STEP
            dimensions checked; review the generated design before use.
          </p>
          <p>
            Measured plate volume:{" "}
            {proposal.acceptance.measured_volume_mm3.toFixed(3)} mm³.
            Electrical, assembly, and manufactured fit remain unknown.
          </p>
          <Provenance sources={proposal.sources} />
          <details>
            <summary>Editable project source</summary>
            <pre style={{ overflowX: "auto" }}>
              {JSON.stringify(proposal.project, null, 2)}
            </pre>
          </details>
          <ul>
            {proposal.limits.map((limit) => (
              <li key={limit}>{limit}</li>
            ))}
          </ul>
        </section>
      )}
    </InspectorSection>
  );
}
