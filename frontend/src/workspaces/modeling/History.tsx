import { useEffect, useRef, useState } from "react";
import { Button, Input } from "../../components/ui";
import type { JsonObject } from "../contracts";
import { json, type Execute, type NativeFeature } from "./native";

const safe = (promise: Promise<unknown>) => {
  void promise.catch(() => {});
};
interface FeatureInspection extends JsonObject {
  definition: JsonObject;
  inputs?: { label: string; param: string; expression: string; unit: string }[];
  error?: string;
  warning?: string;
}
function leaves(object: JsonObject, prefix = ""): [string, unknown][] {
  return Object.entries(object).flatMap(([key, value]): [string, unknown][] => {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value !== null && typeof value === "object" && !Array.isArray(value))
      return leaves(value as JsonObject, path);
    return [[path, value]];
  });
}
export default function History({
  features,
  marker,
  execute,
  busy,
  focusedFeature,
}: {
  features: NativeFeature[];
  marker: number;
  execute: Execute;
  busy: boolean;
  focusedFeature?: number | null;
}) {
  const [selected, setSelected] = useState<NativeFeature | null>(null);
  const [inspection, setInspection] = useState<FeatureInspection | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [name, setName] = useState("");
  const [position, setPosition] = useState("0");
  const [dependents, setDependents] = useState<unknown>(null);
  const [group, setGroup] = useState("");
  const inspect = async (feature: NativeFeature) => {
    const result = (await execute("timeline.inspect", {
      feature: feature.id,
    })) as FeatureInspection;
    setSelected(feature);
    setName(feature.name);
    setInspection(result);
    setPosition(String(features.findIndex((f) => f.id === feature.id)));
    setEdits({});
    setDependents(null);
  };
  const inspectRef = useRef(inspect);
  inspectRef.current = inspect;
  useEffect(() => {
    const feature = features.find((f) => f.id === focusedFeature);
    if (feature && selected?.id !== feature.id)
      safe(inspectRef.current(feature));
  }, [focusedFeature, features.map((f) => f.id).join(",")]);
  return (
    <div className="modeling-history">
      <div className="modeling-row">
        <strong>Timeline · {features.length} features</strong>
        <Button
          disabled={busy}
          onClick={() => safe(execute("timeline.compute_all"))}
        >
          Compute all
        </Button>
        <Button
          disabled={busy || marker === features.length}
          onClick={() =>
            safe(execute("timeline.rollback", { position: "end" }))
          }
        >
          Roll to end
        </Button>
      </div>
      <ol className="modeling-timeline">
        {features.map((feature, index) => (
          <li
            key={feature.id}
            className={`${feature.suppressed || feature.rolled_back ? "is-inactive" : ""} ${feature.error ? "has-error" : ""}`}
          >
            {index === marker && (
              <span className="modeling-marker">History marker</span>
            )}
            <Button
              className="modeling-feature"
              aria-pressed={selected?.id === feature.id}
              disabled={busy}
              onClick={() => safe(inspect(feature))}
            >
              <span>{feature.name}</span>
              <small>
                {feature.type}
                {feature.suppressed ? " · suppressed" : ""}
                {feature.rolled_back ? " · rolled back" : ""}
              </small>
            </Button>
            {feature.error && (
              <p role="alert" className="modeling-danger">
                {feature.error}
              </p>
            )}
            {feature.warning && (
              <p className="modeling-warning">{feature.warning}</p>
            )}
          </li>
        ))}
      </ol>
      {!features.length && (
        <p className="modeling-muted">
          Create a sketch or solid to start the native timeline. Existing host
          features are migrated on the first native edit.
        </p>
      )}
      {selected && inspection && (
        <div className="modeling-form" key={selected.id}>
          <h4>Edit {selected.name}</h4>
          <form
            className="modeling-inline-form"
            onSubmit={(e) => {
              e.preventDefault();
              safe(execute("timeline.rename", { feature: selected.id, name }));
            }}
          >
            <label>
              Feature name
              <Input
                value={name}
                required
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <Button type="submit" disabled={busy}>
              Rename
            </Button>
          </form>
          <div className="modeling-row">
            <Button
              disabled={busy}
              onClick={() =>
                safe(execute("timeline.suppress", { feature: selected.id }))
              }
            >
              Suppress / unsuppress
            </Button>
            <Button
              disabled={busy}
              onClick={() =>
                safe(execute("timeline.roll_to", { feature: selected.id }))
              }
            >
              Roll after feature
            </Button>
            <Button
              disabled={busy}
              onClick={() => {
                safe(
                  execute("timeline.dependents", { feature: selected.id }).then(
                    setDependents,
                  ),
                );
              }}
            >
              Show dependents
            </Button>
          </div>
          <form
            className="modeling-inline-form"
            onSubmit={(e) => {
              e.preventDefault();
              safe(
                execute("timeline.reorder", {
                  feature: selected.id,
                  position: Number(position),
                }),
              );
            }}
          >
            <label>
              Timeline position
              <Input
                type="number"
                min="0"
                max={Math.max(0, features.length - 1)}
                value={position}
                required
                onChange={(e) => setPosition(e.target.value)}
              />
            </label>
            <Button type="submit" disabled={busy}>
              Move feature
            </Button>
          </form>
          <form
            className="modeling-inline-form"
            onSubmit={(e) => {
              e.preventDefault();
              safe(
                execute("timeline.group", {
                  name: group,
                  features: [selected.id],
                }),
              );
            }}
          >
            <label>
              Feature folder
              <Input
                value={group}
                required
                onChange={(e) => setGroup(e.target.value)}
              />
            </label>
            <Button type="submit" disabled={busy}>
              Group feature
            </Button>
          </form>
          {dependents !== null && (
            <pre aria-label="Feature dependents">{json(dependents)}</pre>
          )}
          {inspection.error && (
            <p className="modeling-danger" role="alert">
              {inspection.error}
            </p>
          )}
          <form
            className="modeling-form"
            onSubmit={(event) => {
              event.preventDefault();
              const set: JsonObject = {};
              for (const [key, original] of leaves(inspection.definition)) {
                if (!(key in edits)) continue;
                let value: unknown = edits[key];
                if (typeof original !== "string") {
                  try {
                    value = JSON.parse(edits[key]);
                  } catch {
                    /* Let the engine report an invalid typed definition. */
                  }
                }
                set[key] = value;
              }
              safe(
                execute("timeline.edit", { feature: selected.id, set }).then(
                  () => inspect(selected),
                ),
              );
            }}
          >
            {(inspection.inputs ?? []).map((input) => (
              <div key={input.param} className="modeling-muted">
                {input.label}: <code>{input.expression}</code> · {input.param}
                {input.unit ? ` (${input.unit})` : ""}
              </div>
            ))}
            <details open>
              <summary>Feature definition</summary>
              {leaves(inspection.definition).map(([key, value]) => (
                <label key={key}>
                  {key}
                  <Input
                    aria-label={`Feature ${key}`}
                    value={
                      edits[key] ??
                      (typeof value === "string"
                        ? value
                        : JSON.stringify(value))
                    }
                    onChange={(e) =>
                      setEdits({ ...edits, [key]: e.target.value })
                    }
                  />
                </label>
              ))}
            </details>
            <Button
              type="submit"
              variant="primary"
              disabled={busy || !Object.keys(edits).length}
            >
              Save feature definition
            </Button>
          </form>
        </div>
      )}
    </div>
  );
}
