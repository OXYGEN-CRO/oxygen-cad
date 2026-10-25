import { Button } from "./components/ui";
/** Controlled offline supplier workflow. Callbacks share the Store command API. */
export type SupplierCheck = {
  key: string;
  state: "pass" | "fail" | "unknown";
  reason: string;
  source_field?: string | null;
  unit?: string | null;
  sourced?: unknown;
  requested?: unknown;
};

export type SupplierResult = {
  state: string;
  preflight_state?: string;
  package_state?: string;
  quote_state?: string;
  checks?: SupplierCheck[];
  reasons?: string[];
  warnings?: string[];
  manifest?: string;
  artifact?: string;
  binding_sha256?: string;
  binding?: {
    revision: number;
    request_id: string;
    artifacts: Record<string, string>;
  };
  inputs?: { profile: unknown; request: unknown };
  selected?: {
    provider: string;
    process: string;
    material: string;
    quantity: number;
  };
};

export type SupplierPanelProps = {
  profileJson: string;
  requestJson: string;
  onProfileChange: (value: string) => void;
  onRequestChange: (value: string) => void;
  onEvaluate: () => void;
  onPackage: () => void;
  result?: SupplierResult | null;
  busy?: boolean;
  error?: string | null;
  artifactUrl?: (relative: string) => string;
};

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object") {
    return `{${Object.entries(value)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

export function SupplierPanel({
  profileJson,
  requestJson,
  onProfileChange,
  onRequestChange,
  onEvaluate,
  onPackage,
  result,
  busy = false,
  error,
  artifactUrl,
}: SupplierPanelProps) {
  // Inputs remain controlled. Editing invalidates visible readiness immediately,
  // including before an evaluation callback finishes or the host refreshes state.
  let parsed = true;
  let changed = false;
  try {
    const profile = JSON.parse(profileJson);
    const request = JSON.parse(requestJson);
    if (
      !profile ||
      !request ||
      typeof profile !== "object" ||
      typeof request !== "object" ||
      Array.isArray(profile) ||
      Array.isArray(request)
    )
      parsed = false;
    if (result?.inputs) {
      changed =
        canonical(profile) !== canonical(result.inputs.profile) ||
        canonical(request) !== canonical(result.inputs.request);
    }
    if (result?.selected) {
      changed ||= ["provider", "process", "material", "quantity"].some(
        (key) =>
          request[key] !==
          result.selected?.[
            key as keyof NonNullable<SupplierResult["selected"]>
          ],
      );
    }
    if (result?.binding) {
      changed ||=
        request.request_id !== result.binding.request_id ||
        JSON.stringify([...(request.artifacts ?? [])].sort()) !==
          JSON.stringify(Object.keys(result.binding.artifacts).sort());
    }
  } catch {
    parsed = false;
  }
  // Backend input snapshots bind every profile/request edit, including changes
  // to numeric rules, source hashes, artifact choices and exact units.
  const stale =
    changed ||
    !parsed ||
    result?.package_state === "stale" ||
    result?.state === "stale";
  const selected = result?.selected;
  return (
    <section
      aria-label="Supplier manufacturing preflight"
      className="supplier-panel"
    >
      <h3>Supplier manufacturing preflight</h3>
      <p>
        Select a supplied capability profile and an exact provider, process,
        material, quantity and current handoff export. These checks do not
        establish manufacturability or supplier acceptance.
      </p>
      <label htmlFor="supplier-profile">Capability profile JSON</label>
      <textarea
        id="supplier-profile"
        value={profileJson}
        onChange={(event) => onProfileChange(event.target.value)}
        rows={9}
        spellCheck={false}
        disabled={busy}
      />
      <label htmlFor="supplier-request">Quote request JSON</label>
      <textarea
        id="supplier-request"
        value={requestJson}
        onChange={(event) => onRequestChange(event.target.value)}
        rows={9}
        spellCheck={false}
        disabled={busy}
      />
      {!parsed && <p role="alert">Enter valid JSON objects for both inputs.</p>}
      <div>
        <Button type="button" disabled={busy || !parsed} onClick={onEvaluate}>
          Evaluate supplied rules
        </Button>{" "}
        <Button type="button" disabled={busy || !parsed} onClick={onPackage}>
          Create manual request package
        </Button>
      </div>
      {busy && (
        <p role="status">Checking current revision and artifact hashes…</p>
      )}
      {error && <p role="alert">{error}</p>}
      {result && (
        <div aria-live="polite">
          <p>
            <strong>
              {stale ? "Stale — evaluate current inputs" : result.state}
            </strong>
            {" · "}Sourced checks:{" "}
            {stale
              ? "require re-evaluation"
              : (result.preflight_state ?? "unknown")}
          </p>
          {selected && (
            <p>
              {selected.provider} · {selected.process} · {selected.material} ·
              Quantity {selected.quantity}
            </p>
          )}
          {result.binding && (
            <p>
              Revision {result.binding.revision} · Request{" "}
              {result.binding.request_id}
            </p>
          )}
          {result.checks && (
            <table>
              <caption>Supplied capability rules</caption>
              <thead>
                <tr>
                  <th scope="col">Rule</th>
                  <th scope="col">State</th>
                  <th scope="col">Evidence</th>
                </tr>
              </thead>
              <tbody>
                {result.checks.map((check, index) => (
                  <tr key={`${check.key}-${index}`}>
                    <th scope="row">{check.key}</th>
                    <td>{stale ? "stale" : check.state}</td>
                    <td>
                      {check.reason}
                      {check.source_field ? ` · ${check.source_field}` : ""}
                      {check.unit ? ` · ${check.unit}` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {!!result.reasons?.length && (
            <ul>
              {result.reasons.map((reason, index) => (
                <li key={index}>{reason}</li>
              ))}
            </ul>
          )}
          <p>
            Quote:{" "}
            {stale
              ? "unknown — stale inputs"
              : (result.quote_state ?? "unknown")}
            . Submission requires a manual action outside this application.
          </p>
          {result.artifact && artifactUrl && (
            <a href={artifactUrl(result.artifact)}>
              Download frozen request package
            </a>
          )}
          {result.manifest && (
            <p>
              Manifest: <code>{result.manifest}</code>
            </p>
          )}
          {result.binding_sha256 && (
            <p>
              Request binding: <code>{result.binding_sha256}</code>
            </p>
          )}
          <p>
            Physical fit, strength, complete process rules, current pricing and
            supplier acceptance require separate evidence.
          </p>
        </div>
      )}
    </section>
  );
}
