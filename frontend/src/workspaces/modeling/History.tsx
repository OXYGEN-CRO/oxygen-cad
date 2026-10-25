import { useEffect, useRef, useState } from "react";
import { Button, Input, SelectControl } from "../../components/ui";
import type { JsonObject } from "../contracts";
import { json, type Execute, type NativeFeature } from "./native";

const safe = (promise: Promise<unknown>) => {
  void promise.catch(() => {});
};
interface FeatureInspection extends JsonObject {
  definition: JsonObject;
  inputs?: {
    label: string;
    param: string | null;
    expression: string;
    unit: string;
  }[];
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
const title = (key: string) =>
  key
    .split(/[._]/)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
const vector = (value: unknown): value is (string | number)[] =>
  Array.isArray(value) &&
  value.length === 3 &&
  value.every(
    (coordinate) =>
      typeof coordinate === "string" || typeof coordinate === "number",
  );
const vectorKeys = new Set([
  "position",
  "center",
  "corner",
  "base",
  "origin",
  "translate",
  "axis",
  "direction",
  "normal",
  "x_dir",
  "y_dir",
  "factors",
  "pull",
  "from",
  "to",
  "axis_origin",
  "axis_dir",
]);
const vectorField = (key: string, value: unknown) =>
  vectorKeys.has(key.split(".").at(-1)!) && vector(value);

export default function History({
  features,
  marker,
  execute,
  busy,
  focusedFeature,
  revision,
  onParameters,
  onEditSketch,
}: {
  features: NativeFeature[];
  marker: number;
  execute: Execute;
  busy: boolean;
  focusedFeature?: number | null;
  revision: number;
  onParameters?: () => void;
  onEditSketch?: () => void;
}) {
  const [selected, setSelected] = useState<NativeFeature | null>(null);
  const [inspection, setInspection] = useState<FeatureInspection | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [name, setName] = useState("");
  const [position, setPosition] = useState("0");
  const [dependents, setDependents] = useState<unknown>(null);
  const [group, setGroup] = useState("");
  const [draftNotice, setDraftNotice] = useState(false);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const editsRef = useRef(edits);
  editsRef.current = edits;
  const revisionRef = useRef(revision);
  revisionRef.current = revision;
  const inspectedRevision = useRef<number | null>(null);
  const generation = useRef(0);
  const requestedFeature = useRef<number | null>(null);
  const inspect = async (feature: NativeFeature, preserveDraft = false) => {
    if (preserveDraft && requestedFeature.current !== feature.id) return;
    if (!preserveDraft) requestedFeature.current = feature.id;
    const request = ++generation.current;
    const sourceRevision = revisionRef.current;
    const result = (await execute("timeline.inspect", {
      feature: feature.id,
    })) as FeatureInspection;
    if (request !== generation.current) return;
    const sameFeature = selectedRef.current?.id === feature.id;
    const previousName = selectedRef.current?.name;
    const keepDraft = preserveDraft && sameFeature;
    setSelected(feature);
    setInspection(result);
    inspectedRevision.current = sourceRevision;
    if (!keepDraft) {
      setEdits({});
      setName(feature.name);
      setPosition(
        String(features.findIndex((candidate) => candidate.id === feature.id)),
      );
      setDependents(null);
      setDraftNotice(false);
    } else {
      setDraftNotice(Object.keys(editsRef.current).length > 0);
      setName((draft) => (draft === previousName ? feature.name : draft));
    }
  };
  const inspectRef = useRef(inspect);
  inspectRef.current = inspect;
  const focused = features.find((feature) => feature.id === focusedFeature);
  useEffect(() => {
    if (focused && selectedRef.current?.id !== focused.id)
      safe(inspectRef.current(focused));
  }, [focusedFeature, Boolean(focused)]);
  const featureIds = features.map((feature) => feature.id).join(",");
  useEffect(() => {
    const feature = features.find(
      (candidate) => candidate.id === selectedRef.current?.id,
    );
    if (feature && inspectedRevision.current !== revision)
      safe(inspectRef.current(feature, true));
    else if (selectedRef.current && !feature) {
      generation.current += 1;
      requestedFeature.current = null;
      setSelected(null);
      setInspection(null);
      setEdits({});
    }
  }, [revision, selected?.id, featureIds]);

  const fields = inspection ? leaves(inspection.definition) : [];
  const simple = fields.filter(
    ([key, value]) =>
      typeof value === "string" ||
      typeof value === "number" ||
      vectorField(key, value),
  );
  const advanced = fields.filter(
    ([key, value]) =>
      !(
        typeof value === "string" ||
        typeof value === "number" ||
        vectorField(key, value)
      ),
  );
  const field = ([key, value]: [string, unknown]) => {
    if (vector(value) && vectorField(key, value)) {
      const coordinates =
        edits[key] === undefined
          ? value
          : (JSON.parse(edits[key]) as (string | number)[]);
      return (
        <fieldset className="modeling-vector" key={key}>
          <legend>{title(key)}</legend>
          <div className="modeling-vector-inputs">
            {["X", "Y", "Z"].map((axis, index) => (
              <label key={axis}>
                {axis}
                <Input
                  aria-label={`Feature ${key} ${axis}`}
                  value={coordinates[index]}
                  onChange={(event) => {
                    const coordinate = event.target.value;
                    setEdits((current) => {
                      const next = [
                        ...(current[key] === undefined
                          ? value
                          : (JSON.parse(current[key]) as (string | number)[])),
                      ];
                      next[index] = coordinate;
                      return { ...current, [key]: JSON.stringify(next) };
                    });
                  }}
                />
              </label>
            ))}
          </div>
        </fieldset>
      );
    }
    return (
      <label key={key}>
        {title(key)}
        <Input
          aria-label={`Feature ${key}`}
          value={
            edits[key] ??
            (typeof value === "string" ? value : JSON.stringify(value))
          }
          onChange={(event) => {
            const next = event.target.value;
            setEdits((current) => ({ ...current, [key]: next }));
          }}
        />
      </label>
    );
  };

  return (
    <div className="modeling-history">
      <div className="modeling-row">
        <label>
          Feature
          <SelectControl
            aria-label="Feature dimensions"
            value={selected?.id ?? ""}
            disabled={busy}
            onChange={(event) => {
              const feature = features.find(
                (candidate) => candidate.id === Number(event.target.value),
              );
              if (feature) safe(inspect(feature));
            }}
          >
            <option value="">Choose a feature</option>
            {features.map((feature) => (
              <option key={feature.id} value={feature.id}>
                {feature.name} · {feature.type}
              </option>
            ))}
          </SelectControl>
        </label>
        {onParameters && (
          <Button onClick={onParameters}>Named parameters</Button>
        )}
      </div>

      {selected && inspection && (
        <div className="modeling-form">
          <h4>Edit {selected.name}</h4>
          {inspection.error && (
            <p className="modeling-danger" role="alert">
              {inspection.error}
            </p>
          )}
          {draftNotice && (
            <p className="modeling-warning" role="status">
              Model updated; your unapplied edits are kept.
            </p>
          )}
          {selected.type.toLowerCase() === "sketch" && (
            <Button
              variant="primary"
              disabled={busy}
              onClick={() =>
                safe(
                  execute("sketch.edit", { sketch: selected.id }).then(() =>
                    onEditSketch?.(),
                  ),
                )
              }
            >
              Edit sketch
            </Button>
          )}
          <form
            className="modeling-form"
            aria-label="Edit feature dimensions"
            onSubmit={(event) => {
              event.preventDefault();
              const submitted = { ...edits };
              const savedFeature = selected;
              const set: JsonObject = {};
              for (const [key, original] of fields) {
                if (!(key in submitted)) continue;
                let value: unknown = submitted[key];
                if (typeof original !== "string") {
                  try {
                    value = JSON.parse(submitted[key]);
                  } catch {
                    /* Native validation reports invalid definitions. */
                  }
                }
                set[key] = value;
              }
              safe(
                execute("timeline.edit", {
                  feature: savedFeature.id,
                  set,
                }).then(() => {
                  if (selectedRef.current?.id !== savedFeature.id) return;
                  setEdits((current) =>
                    Object.fromEntries(
                      Object.entries(current).filter(
                        ([key, value]) => submitted[key] !== value,
                      ),
                    ),
                  );
                  if (requestedFeature.current === savedFeature.id)
                    return inspect(savedFeature, true);
                }),
              );
            }}
          >
            {simple.map(field)}
            {advanced.length > 0 && (
              <details>
                <summary>Other feature fields</summary>
                {advanced.map(field)}
              </details>
            )}
            <Button
              type="submit"
              variant="primary"
              disabled={busy || !Object.keys(edits).length}
            >
              Save feature definition
            </Button>
          </form>

          <details>
            <summary>Feature details and history</summary>
            <form
              className="modeling-inline-form"
              onSubmit={(event) => {
                event.preventDefault();
                safe(
                  execute("timeline.rename", { feature: selected.id, name }),
                );
              }}
            >
              <label>
                Feature name
                <Input
                  value={name}
                  required
                  onChange={(event) => setName(event.target.value)}
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
                onClick={() =>
                  safe(
                    execute("timeline.dependents", {
                      feature: selected.id,
                    }).then(setDependents),
                  )
                }
              >
                Show dependents
              </Button>
            </div>
            <form
              className="modeling-inline-form"
              onSubmit={(event) => {
                event.preventDefault();
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
                  onChange={(event) => setPosition(event.target.value)}
                />
              </label>
              <Button type="submit" disabled={busy}>
                Move feature
              </Button>
            </form>
            <form
              className="modeling-inline-form"
              onSubmit={(event) => {
                event.preventDefault();
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
                  onChange={(event) => setGroup(event.target.value)}
                />
              </label>
              <Button type="submit" disabled={busy}>
                Group feature
              </Button>
            </form>
            {dependents !== null && (
              <pre aria-label="Feature dependents">{json(dependents)}</pre>
            )}
            {(inspection.inputs ?? []).map((input, index) => (
              <p key={`${input.param}:${index}`} className="modeling-muted">
                {input.label}: {input.expression}
                {input.unit ? ` (${input.unit})` : ""}
                {input.param ? ` · ${input.param}` : ""}
              </p>
            ))}
          </details>
        </div>
      )}

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
          Create a sketch or solid to start the timeline.
        </p>
      )}
    </div>
  );
}
