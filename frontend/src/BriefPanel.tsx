import type { Project } from "./api";
import type { EngineeringBrief, BriefInput, BriefLink } from "./generated";
import { InspectorSection } from "./components/InspectorSection";
import { Button, Input, SelectControl } from "./components/ui";

export type BriefResult = {
  schema_version: number;
  revision: number;
  state: string;
  valid: boolean;
  issues: {
    code: string;
    message: string;
    link_id?: string | null;
    input_id?: string | null;
  }[];
  links: {
    id: string;
    requirement_id: string;
    status: "passed" | "failed" | "unknown" | "stale";
    reason: string;
    value?: number | null;
    unit?: string | null;
    evidence?: { source: string; source_sha256: string } | null;
  }[];
};

export type BriefPanelProps = {
  project: Project;
  data: EngineeringBrief | null;
  result?: BriefResult | null;
  onChange: (brief: EngineeringBrief) => void;
  onEvaluate?: () => void;
  onBind?: () => void;
  disabled?: boolean;
};

const categories: BriefInput["kind"][] = [
  "payload",
  "envelope",
  "interface",
  "budget",
  "load",
  "assumption",
  "other",
];
const geometry = new Set(["max_mass", "max_dimension", "min_clearance"]);

/** Controlled editor. The host owns revision-checked saving and evidence requests. */
export function BriefPanel({
  project,
  data,
  result,
  onChange,
  onEvaluate,
  onBind,
  disabled = false,
}: BriefPanelProps) {
  const update = (fn: (draft: EngineeringBrief) => void) => {
    if (!data) return;
    const draft = structuredClone(data);
    fn(draft);
    onChange(draft);
  };
  const editInput = (id: string, change: Partial<BriefInput>) =>
    update((b) => {
      b.inputs = b.inputs.map((i) => (i.id === id ? { ...i, ...change } : i));
    });
  const editLink = (id: string, change: Partial<BriefLink>) =>
    update((b) => {
      b.links = b.links.map((i) => (i.id === id ? { ...i, ...change } : i));
    });
  const nextId = (prefix: string, existing: { id: string }[]) => {
    let n = 1;
    while (existing.some((item) => item.id === `${prefix}_${n}`)) n++;
    return `${prefix}_${n}`;
  };
  if (!data)
    return (
      <InspectorSection title="Engineering brief">
        <p>
          Describe the task and its inputs, then link intent to existing
          requirements.
        </p>
        <Button
          disabled={disabled}
          onClick={() =>
            onChange({
              schema_version: 1,
              title: "Engineering brief",
              task: "",
              inputs: [],
              links: [],
              joint_requirements: [],
              components: [],
            })
          }
        >
          Create engineering brief
        </Button>
      </InspectorSection>
    );
  return (
    <InspectorSection title="Engineering brief">
      <fieldset
        disabled={disabled}
        style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}
      >
        <label className="field">
          <span>Brief title</span>
          <Input
            aria-label="Brief title"
            value={data.title}
            onChange={(e) =>
              update((b) => {
                b.title = e.target.value;
              })
            }
          />
        </label>
        <label className="field">
          <span>Task and intended use</span>
          <textarea
            aria-label="Task and intended use"
            rows={4}
            value={data.task}
            onChange={(e) =>
              update((b) => {
                b.task = e.target.value;
              })
            }
          />
        </label>
        <p>
          Specify payload, envelope, interfaces, budget, loads and assumptions.
          Empty quantities remain unknown. A brief does not configure a
          simulation.
        </p>
        {data.inputs.map((item) => (
          <details key={item.id} open>
            <summary>
              {item.id} · {item.kind}
            </summary>
            <label className="field">
              <span>Input category</span>
              <SelectControl
                aria-label={`Category ${item.id}`}
                value={item.kind}
                onChange={(e) =>
                  editInput(item.id, {
                    kind: e.target.value as BriefInput["kind"],
                  })
                }
              >
                {categories.map((kind) => (
                  <option key={kind}>{kind}</option>
                ))}
              </SelectControl>
            </label>
            <label className="field">
              <span>Description</span>
              <Input
                aria-label={`Description ${item.id}`}
                value={item.description}
                onChange={(e) =>
                  editInput(item.id, { description: e.target.value })
                }
              />
            </label>
            <label className="field">
              <span>Quantity</span>
              <Input
                type="number"
                step="any"
                aria-label={`Quantity ${item.id}`}
                value={item.value ?? ""}
                placeholder="Unknown"
                onChange={(e) =>
                  editInput(item.id, {
                    value:
                      e.target.value === "" ? null : Number(e.target.value),
                  })
                }
              />
            </label>
            <label className="field">
              <span>Unit</span>
              <Input
                aria-label={`Unit ${item.id}`}
                value={item.unit ?? ""}
                placeholder="Explicit unit"
                onChange={(e) =>
                  editInput(item.id, { unit: e.target.value || null })
                }
              />
            </label>
            <label className="field">
              <span>Provenance / assumption basis</span>
              <Input
                aria-label={`Provenance ${item.id}`}
                value={item.provenance ?? ""}
                onChange={(e) =>
                  editInput(item.id, { provenance: e.target.value || null })
                }
              />
            </label>
            <label className="field">
              <span>Existing parameter</span>
              <SelectControl
                aria-label={`Parameter ${item.id}`}
                value={item.parameter ?? ""}
                onChange={(e) =>
                  editInput(item.id, { parameter: e.target.value || null })
                }
              >
                <option value="">Unlinked</option>
                {Object.entries(project.parameters).map(([name, p]) => (
                  <option key={name} value={name}>
                    {name} ({p.unit})
                  </option>
                ))}
              </SelectControl>
            </label>
            <Button
              onClick={() =>
                update((b) => {
                  b.inputs = b.inputs.filter((i) => i.id !== item.id);
                })
              }
            >
              Remove input {item.id}
            </Button>
          </details>
        ))}
        <Button
          onClick={() =>
            update((b) => {
              b.inputs.push({
                id: nextId("input", b.inputs),
                kind: "other",
                description: "Unspecified input",
                value: null,
                unit: null,
                provenance: null,
                parameter: null,
              });
            })
          }
        >
          Add brief input
        </Button>
        <h3>Requirement traceability</h3>
        {data.links.map((link) => {
          const requirement = project.requirements.find(
            (r) => r.id === link.requirement_id,
          );
          const row = result?.links.find((r) => r.id === link.id);
          return (
            <details key={link.id} open>
              <summary>
                {link.id} · {row?.status ?? "unknown"}
              </summary>
              <label className="field">
                <span>Intent</span>
                <Input
                  aria-label={`Intent ${link.id}`}
                  value={link.description}
                  onChange={(e) =>
                    editLink(link.id, { description: e.target.value })
                  }
                />
              </label>
              <label className="field">
                <span>Existing requirement</span>
                <SelectControl
                  aria-label={`Requirement ${link.id}`}
                  value={link.requirement_id}
                  onChange={(e) => {
                    const r = project.requirements.find(
                      (v) => v.id === e.target.value,
                    );
                    editLink(link.id, {
                      requirement_id: e.target.value,
                      study_id:
                        r && geometry.has(r.kind)
                          ? null
                          : (r?.study_id ?? "default"),
                    });
                  }}
                >
                  {!requirement && (
                    <option value={link.requirement_id}>
                      {link.requirement_id} (missing)
                    </option>
                  )}
                  {project.requirements.map((r, index) => (
                    <option key={`${r.id}-${index}`} value={r.id}>
                      {r.id} · {r.kind} {r.value}
                      {r.enabled ? "" : " (disabled)"}
                    </option>
                  ))}
                </SelectControl>
              </label>
              {requirement && !geometry.has(requirement.kind) && (
                <label className="field">
                  <span>Study</span>
                  <SelectControl
                    aria-label={`Study ${link.id}`}
                    value={link.study_id ?? requirement.study_id ?? ""}
                    onChange={(e) =>
                      editLink(link.id, { study_id: e.target.value || null })
                    }
                  >
                    <option value="">Select study</option>
                    {Object.entries({
                      default: project.study,
                      ...project.named_studies,
                    }).map(([id, study]) => (
                      <option key={id} value={id}>
                        {study.name} ({id})
                      </option>
                    ))}
                  </SelectControl>
                </label>
              )}
              <label className="field">
                <span>Parameters</span>
                <SelectControl
                  multiple
                  size={3}
                  aria-label={`Parameters ${link.id}`}
                  value={link.parameter_refs}
                  onChange={(e) =>
                    editLink(link.id, {
                      parameter_refs: Array.from(
                        e.target.selectedOptions,
                        (option) => option.value,
                      ),
                    })
                  }
                >
                  {[
                    ...new Set([
                      ...Object.keys(project.parameters),
                      ...link.parameter_refs,
                    ]),
                  ].map((name) => (
                    <option key={name} value={name}>
                      {name}
                      {name in project.parameters ? "" : " (missing)"}
                    </option>
                  ))}
                </SelectControl>
              </label>
              <label className="field">
                <span>Brief inputs</span>
                <SelectControl
                  multiple
                  size={3}
                  aria-label={`Inputs ${link.id}`}
                  value={link.input_refs}
                  onChange={(e) =>
                    editLink(link.id, {
                      input_refs: Array.from(
                        e.target.selectedOptions,
                        (option) => option.value,
                      ),
                    })
                  }
                >
                  {[
                    ...new Set([
                      ...data.inputs.map((i) => i.id),
                      ...link.input_refs,
                    ]),
                  ].map((id) => (
                    <option key={id} value={id}>
                      {id}
                      {data.inputs.some((i) => i.id === id) ? "" : " (missing)"}
                    </option>
                  ))}
                </SelectControl>
              </label>
              {row && (
                <div role="status">
                  <p>
                    {row.status}: {row.reason}
                  </p>
                  {row.value != null && (
                    <p>
                      Measured {row.value} {row.unit}
                    </p>
                  )}
                  {row.evidence && (
                    <small>
                      Evidence {row.evidence.source} ·{" "}
                      {row.evidence.source_sha256.slice(0, 12)}
                    </small>
                  )}
                </div>
              )}
              <Button
                onClick={() =>
                  update((b) => {
                    b.links = b.links.filter((i) => i.id !== link.id);
                  })
                }
              >
                Remove link {link.id}
              </Button>
            </details>
          );
        })}
        <Button
          disabled={!project.requirements.length}
          onClick={() =>
            update((b) => {
              const requirement = project.requirements[0];
              b.links.push({
                id: nextId("link", b.links),
                description: "",
                requirement_id: requirement.id,
                parameter_refs: [],
                input_refs: [],
                study_id: geometry.has(requirement.kind)
                  ? null
                  : (requirement.study_id ?? "default"),
                evidence: null,
              });
            })
          }
        >
          Link existing requirement
        </Button>
        {!project.requirements.length && (
          <p>
            Add a requirement to the engineering document before linking it.
          </p>
        )}
        <div className="two-fields">
          <Button disabled={!onEvaluate} onClick={onEvaluate}>
            Evaluate traceability
          </Button>
          <Button disabled={!onBind} onClick={onBind}>
            Bind current evidence
          </Button>
        </div>
        {result && (
          <p role="status">
            Traceability: {result.state} · project revision {result.revision}
          </p>
        )}
        {result?.issues.map((issue, i) => (
          <p key={i} role="alert">
            {issue.message}
          </p>
        ))}
      </fieldset>
    </InspectorSection>
  );
}
