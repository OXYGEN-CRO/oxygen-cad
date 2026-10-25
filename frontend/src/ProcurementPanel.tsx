import { useRef, useState } from "react";
import { Button } from "./components/ui";

export type ProcurementReport = {
  bom_sha256: string;
  input_sha256: string;
  assessment_sha256: string;
  evaluated_at: string;
  clock: "explicit_snapshot" | "current_utc";
  evidence_mode: "supplied_snapshot";
  region: string | null;
  complete: boolean;
  excluded_line_ids: string[];
  lines: {
    id: string;
    name: string;
    classification: "purchased" | "custom" | "unknown";
    quantity: number | null;
    identity: {
      provider: string | null;
      sku: string | null;
      asset_id: string | null;
      source_sha256: string | null;
    } | null;
    state: string;
    reasons: string[];
    selected_offer_id: string | null;
    currency?: string;
    unit_price?: string;
    extended_price?: string;
    offers: {
      offer_id: string;
      state: string;
      reasons: string[];
      lead_time_state: string;
      lead_time_days: number | null;
      observed_at: string | null;
      expires_at: string | null;
    }[];
  }[];
  totals: { currency: string; amount: string; scope: string }[];
  alternatives: {
    id: string;
    line_id: string;
    state: string;
    reasons: string[];
  }[];
  limitations: string[];
};

export type ProcurementPanelProps = {
  value: string;
  onChange: (value: string) => void;
  result?: ProcurementReport | null;
  onEvaluate: (data: unknown) => void | Promise<void>;
  busy?: boolean;
  error?: string | null;
};

/** Controlled transient input; parent owns evaluation/result transport and persistence. */
export function ProcurementPanel({
  value,
  onChange,
  result,
  onEvaluate,
  busy = false,
  error,
}: ProcurementPanelProps) {
  const [localError, setLocalError] = useState<string | null>(null);
  const submitted = useRef<string | null>(null);
  const [edited, setEdited] = useState(false);
  const stale =
    edited || (submitted.current !== null && submitted.current !== value);
  const evaluate = async () => {
    setLocalError(null);
    try {
      const data: unknown = JSON.parse(value);
      if (!data || typeof data !== "object" || Array.isArray(data))
        throw new Error("Enter a procurement request object.");
      submitted.current = value;
      setEdited(false);
      await onEvaluate(data);
    } catch (e) {
      setEdited(true);
      setLocalError(e instanceof Error ? e.message : "Evaluation failed.");
    }
  };
  return (
    <section aria-label="Procurement" className="inspector-section">
      <div className="inspector-title">
        <h2>Procurement</h2>
      </div>
      <p className="muted">
        Inspect a BOM with supplied offer observations. Prices and availability
        are snapshots; offers stay separate from catalog metadata.
      </p>
      <label className="field">
        <span>BOM, offers and constraints (JSON)</span>
        <textarea
          aria-label="Procurement JSON"
          className="ui-input"
          rows={14}
          spellCheck={false}
          value={value}
          onChange={(event) => {
            setEdited(true);
            onChange(event.target.value);
          }}
        />
      </label>
      <Button variant="primary" disabled={busy} onClick={() => void evaluate()}>
        {busy ? "Evaluating…" : "Evaluate supplied offers"}
      </Button>
      {(localError || error) && (
        <p role="alert" className="study-warning">
          {localError || error}
        </p>
      )}
      {stale && result && (
        <p role="status" className="study-warning">
          Input changed. Evaluate again to refresh the assessment.
        </p>
      )}
      {result && !stale && !busy && (
        <>
          <p className="muted">
            Supplied snapshot · {result.evaluated_at} ·{" "}
            {result.clock === "explicit_snapshot"
              ? "Explicit evaluation clock"
              : "Current UTC clock"}{" "}
            · Region {result.region || "unknown"}
          </p>
          <table className="study-table" aria-label="Purchased and custom BOM">
            <thead>
              <tr>
                <th>Item / identity</th>
                <th>Kind</th>
                <th>Qty</th>
                <th>Offer state</th>
                <th>Amount</th>
              </tr>
            </thead>
            <tbody>
              {result.lines.map((line) => (
                <tr key={line.id}>
                  <td>
                    {line.name}
                    <br />
                    <small>
                      {line.identity?.provider || "Unknown provider"} /{" "}
                      {line.identity?.sku || "unknown SKU"}
                      <br />
                      {line.identity?.asset_id || "unknown asset"}
                    </small>
                  </td>
                  <td>{line.classification}</td>
                  <td>{line.quantity ?? "Unknown"}</td>
                  <td>
                    {line.state.replaceAll("_", " ")}
                    {line.reasons.map((reason) => (
                      <p className="muted" key={reason}>
                        {reason}
                      </p>
                    ))}
                  </td>
                  <td>
                    {line.extended_price !== undefined
                      ? `${line.currency} ${line.extended_price}`
                      : "Unknown"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {result.lines.flatMap((line) =>
            line.offers.map((offer) => (
              <p className="muted" key={`${line.id}/${offer.offer_id}`}>
                {line.name} · {offer.offer_id}:{" "}
                {offer.state.replaceAll("_", " ")} · observed{" "}
                {offer.observed_at || "unknown"} · expires{" "}
                {offer.expires_at || "unknown"} · lead time{" "}
                {offer.lead_time_state === "known"
                  ? `${offer.lead_time_days} days`
                  : offer.lead_time_state}
              </p>
            )),
          )}
          <div className="section-heading">
            {result.complete
              ? "SUPPLIED BOM SUBTOTALS"
              : "PARTIAL KNOWN SUBTOTALS"}
          </div>
          {result.totals.length ? (
            result.totals.map((total) => (
              <div className="result-metric" key={total.currency}>
                <span>{total.currency}</span>
                <strong>{total.amount}</strong>
              </div>
            ))
          ) : (
            <p className="empty-note">No eligible priced lines.</p>
          )}
          {!!result.excluded_line_ids.length && (
            <p className="muted">
              Excluded lines: {result.excluded_line_ids.join(", ")}
            </p>
          )}
          {!!result.alternatives.length && (
            <>
              <div className="section-heading">ALTERNATIVES</div>
              {result.alternatives.map((alternative) => (
                <p key={alternative.id}>
                  {alternative.id} → {alternative.line_id}:{" "}
                  {alternative.state.replaceAll("_", " ")}
                  <br />
                  <small>{alternative.reasons.join("; ")}</small>
                </p>
              ))}
            </>
          )}
          {result.limitations.map((limitation) => (
            <p className="muted" key={limitation}>
              {limitation}
            </p>
          ))}
        </>
      )}
    </section>
  );
}
