import { useEffect, useRef, useState } from "react";
import type { Project } from "./api";
import type { EngineeringBrief, JointRequirement, Quantity } from "./generated";
import { api, post } from "./api";
import { Button, Input, SelectControl } from "./components/ui";
import "./joint.css";

type Props = {
  project: Project;
  dirty: boolean;
  blocked: boolean;
  onEdit: (fn: (p: Project) => void) => void;
};
type Check = {
  code: string;
  status: string;
  reason: string;
  value?: number;
  unit?: string;
  required_nm?: number;
  limits_nm?: number[];
};
type Candidate = {
  component_id: string;
  mpn: string;
  status: string;
  checks: Check[];
  sources: { id: string; url: string; locator: string }[];
};
type Report = {
  state: string;
  revision: number;
  limits: string[];
  requirements: {
    requirement_id: string;
    status: string;
    demand: Check;
    candidates: Candidate[];
  }[];
};
type Status = {
  freshness: string;
  report: Report | null;
  previous_report?: Report | null;
};
const emptyBrief = (): EngineeringBrief => ({
  schema_version: 1,
  title: "Engineering brief",
  task: "",
  inputs: [],
  links: [],
  joint_requirements: [],
  components: [],
});
const blankTask = (id: string): JointRequirement => ({
  id,
  statement: "Static joint task",
  provenance: "User specification; complete operating conditions",
  state: "draft",
  subject: null,
  parameter_refs: [],
  bindings: {},
  payload_mass: null,
  payload_arm: null,
  link_mass: null,
  link_com: null,
  hold_duration: null,
  supply_min: null,
  supply_max: null,
  ambient: null,
  cooling: null,
  duty_cycle: null,
  bus: null,
  protocol: null,
  connector: null,
  pinout: {},
  mount: null,
  assumptions: [],
  static_model_confirmed: false,
  selected_component_id: null,
});
const fields: {
  key: keyof JointRequirement;
  label: string;
  unit: Quantity["unit"];
}[] = [
  { key: "payload_mass", label: "Payload mass", unit: "kg" },
  { key: "payload_arm", label: "Payload moment arm", unit: "mm" },
  { key: "link_mass", label: "Link mass", unit: "kg" },
  { key: "link_com", label: "Link center of mass arm", unit: "mm" },
  { key: "hold_duration", label: "Hold duration", unit: "s" },
  { key: "supply_min", label: "Minimum supply", unit: "V" },
  { key: "supply_max", label: "Maximum supply", unit: "V" },
  { key: "ambient", label: "Ambient temperature", unit: "degC" },
];
export function JointPanel({ project, dirty, blocked, onEdit }: Props) {
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [contracts, setContracts] = useState("");
  const generation = useRef(0);
  useEffect(() => {
    const token = ++generation.current;
    setStatus(null);
    api("joints/status")
      .then((r) => {
        if (token === generation.current) setStatus(r);
      })
      .catch((e) => {
        if (token === generation.current) setError(String(e));
      });
    setContracts(
      JSON.stringify(project.engineering_brief?.components ?? [], null, 2),
    );
    return () => {
      generation.current++;
    };
  }, [project.revision]);
  const brief = project.engineering_brief;
  function edit(fn: (b: EngineeringBrief) => void) {
    onEdit((p) => {
      p.engineering_brief ??= emptyBrief();
      fn(p.engineering_brief);
    });
  }
  function change(index: number, key: keyof JointRequirement, value: unknown) {
    edit((b) => {
      (b.joint_requirements[index] as any)[key] = value;
    });
  }
  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  const disabled = busy || blocked;
  const result = dirty ? null : status?.report;
  return (
    <section className="joint-panel" aria-label="Joint selection">
      <div className="section-heading">REQUIREMENTS & COMPONENTS</div>
      <p>
        Define a static joint task, compare exact actuator variants, and inspect
        the evidence gaps.
      </p>
      <div className="joint-actions">
        <Button
          disabled={disabled}
          onClick={() =>
            run(async () => {
              const data = await api("joints/example");
              edit((b) => {
                b.joint_requirements = data.joint_requirements;
                b.components = data.components;
              });
            })
          }
        >
          Load illustrative elbow
        </Button>
        <Button
          disabled={disabled}
          onClick={() =>
            run(async () => {
              const data = await api("joints/library");
              edit((b) => {
                const ids = new Set(data.components.map((c: any) => c.id));
                b.components = [
                  ...b.components.filter((c) => !ids.has(c.id)),
                  ...data.components,
                ];
              });
            })
          }
        >
          Load sourced actuators
        </Button>
        <Button
          disabled={disabled}
          onClick={() =>
            edit((b) => {
              let n = 1;
              while (b.joint_requirements.some((r) => r.id === `joint_${n}`))
                n++;
              b.joint_requirements.push(blankTask(`joint_${n}`));
            })
          }
        >
          Add joint requirement
        </Button>
      </div>
      <p role="status">
        {dirty
          ? "Unsaved changes · save before evaluating"
          : `Evidence ${status?.freshness ?? "loading"}`}
        {result ? ` · ${result.state}` : ""}
      </p>
      {!dirty && status?.freshness === "stale" && (
        <p>
          Earlier evidence belongs to different inputs. Re-evaluate the saved
          design.
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      <fieldset disabled={disabled} className="joint-fields">
        {(brief?.joint_requirements ?? []).map((task, i) => {
          const row = result?.requirements.find(
            (r) => r.requirement_id === task.id,
          );
          return (
            <details key={task.id} open className="joint-task">
              <summary>
                {task.id} · {task.state}
                {row ? ` · ${row.status}` : ""}
              </summary>
              <label className="field">
                <span>Requirement statement</span>
                <Input
                  aria-label="Requirement statement"
                  value={task.statement}
                  onChange={(e) => change(i, "statement", e.target.value)}
                />
              </label>
              <label className="field">
                <span>Requirement provenance</span>
                <Input
                  aria-label="Requirement provenance"
                  value={task.provenance}
                  onChange={(e) => change(i, "provenance", e.target.value)}
                />
              </label>
              <div className="joint-grid">
                {fields.map(({ key, label, unit }) => {
                  const boundName = (task.bindings as Record<string, string>)[
                    key
                  ];
                  const parameter = boundName
                    ? project.parameters[boundName]
                    : null;
                  const q = parameter
                    ? {
                        value: parameter.value,
                        unit: parameter.unit as Quantity["unit"],
                      }
                    : (task[key] as Quantity | null);
                  const choices =
                    unit === "kg"
                      ? ["kg", "g"]
                      : unit === "mm"
                        ? ["mm", "m"]
                        : unit === "s"
                          ? ["s", "min"]
                          : [unit];
                  return (
                    <label className="field" key={key}>
                      <span>{label}</span>
                      <div className="joint-quantity">
                        <Input
                          aria-label={label}
                          readOnly={Boolean(boundName)}
                          type="number"
                          step="any"
                          value={q?.value ?? ""}
                          onChange={(e) =>
                            change(
                              i,
                              key,
                              e.target.value === ""
                                ? null
                                : {
                                    value: Number(e.target.value),
                                    unit: q?.unit ?? unit,
                                  },
                            )
                          }
                        />
                        <SelectControl
                          aria-label={`${label} unit`}
                          disabled={Boolean(boundName)}
                          value={q?.unit ?? unit}
                          onChange={(e) =>
                            change(
                              i,
                              key,
                              q
                                ? {
                                    value:
                                      (q.value *
                                        ((
                                          {
                                            kg: 1,
                                            g: 0.001,
                                            m: 1,
                                            mm: 0.001,
                                            s: 1,
                                            min: 60,
                                            V: 1,
                                            degC: 1,
                                          } as Record<string, number>
                                        )[q.unit] ?? 1)) /
                                      ((
                                        {
                                          kg: 1,
                                          g: 0.001,
                                          m: 1,
                                          mm: 0.001,
                                          s: 1,
                                          min: 60,
                                          V: 1,
                                          degC: 1,
                                        } as Record<string, number>
                                      )[e.target.value] ?? 1),
                                    unit: e.target.value,
                                  }
                                : null,
                            )
                          }
                        >
                          {choices.map((u) => (
                            <option key={u}>{u}</option>
                          ))}
                        </SelectControl>
                      </div>
                    </label>
                  );
                })}
              </div>
              <label className="field">
                <span>Physical bus</span>
                <SelectControl
                  aria-label="Physical bus"
                  value={task.bus ?? ""}
                  onChange={(e) => change(i, "bus", e.target.value || null)}
                >
                  {["", "TTL", "RS485", "CAN"].map((v) => (
                    <option key={v} value={v}>
                      {v || "Unknown"}
                    </option>
                  ))}
                </SelectControl>
              </label>
              {(
                [
                  ["protocol", "Control protocol"],
                  ["connector", "Connector"],
                  ["cooling", "Cooling condition"],
                ] as const
              ).map(([key, label]) => (
                <label className="field" key={key}>
                  <span>{label}</span>
                  <Input
                    aria-label={label}
                    value={task[key] ?? ""}
                    onChange={(e) => change(i, key, e.target.value || null)}
                  />
                </label>
              ))}
              <label className="field">
                <span>Duty cycle (0–1)</span>
                <Input
                  aria-label="Duty cycle"
                  type="number"
                  min="0"
                  max="1"
                  step="any"
                  value={task.duty_cycle ?? ""}
                  onChange={(e) =>
                    change(
                      i,
                      "duty_cycle",
                      e.target.value === "" ? null : Number(e.target.value),
                    )
                  }
                />
              </label>
              <label className="field">
                <span>Assumptions (one per line)</span>
                <textarea
                  aria-label="Joint assumptions"
                  rows={3}
                  value={task.assumptions.join("\n")}
                  onChange={(e) =>
                    change(
                      i,
                      "assumptions",
                      e.target.value.split("\n").filter(Boolean),
                    )
                  }
                />
              </label>
              <label className="checkbox">
                <Input
                  type="checkbox"
                  aria-label="Confirm static model"
                  checked={task.static_model_confirmed}
                  onChange={(e) =>
                    change(i, "static_model_confirmed", e.target.checked)
                  }
                />
                Horizontal, static payload + one link is the intended model
              </label>
              <label className="checkbox">
                <Input
                  type="checkbox"
                  aria-label="Accept joint requirement"
                  checked={task.state === "accepted"}
                  onChange={(e) =>
                    change(i, "state", e.target.checked ? "accepted" : "draft")
                  }
                />
                Accept this requirement (agents must preserve its conditions)
              </label>
              <label className="field">
                <span>Selected actuator</span>
                <SelectControl
                  aria-label="Selected actuator"
                  value={task.selected_component_id ?? ""}
                  onChange={(e) =>
                    change(i, "selected_component_id", e.target.value || null)
                  }
                >
                  <option value="">Unselected</option>
                  {brief?.components.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.mpn}
                    </option>
                  ))}
                </SelectControl>
              </label>
              <details>
                <summary>Interfaces and project bindings</summary>
                <p>
                  Pinout, nominal mounting contract, subject and parameter
                  references are editable in the shared project source. Missing
                  interface evidence remains unresolved.
                </p>
                <pre>
                  {JSON.stringify(
                    {
                      pinout: task.pinout,
                      mount: task.mount,
                      subject: task.subject,
                      parameter_refs: task.parameter_refs,
                      bindings: task.bindings,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
              {row && (
                <div className="joint-evidence">
                  <p>
                    Static demand:{" "}
                    {row.demand.value == null
                      ? row.demand.reason
                      : `${row.demand.value.toFixed(4)} ${row.demand.unit}`}
                  </p>
                  {row.candidates.map((candidate) => (
                    <details
                      key={candidate.component_id}
                      open={
                        candidate.component_id === task.selected_component_id
                      }
                      className={`joint-candidate status-${candidate.status}`}
                    >
                      <summary>
                        {candidate.mpn} · {candidate.status}
                      </summary>
                      {candidate.checks.map((check, k) => (
                        <div key={k} className="joint-check">
                          <strong>
                            {check.code} · {check.status}
                          </strong>
                          <p>{check.reason}</p>
                          {check.required_nm != null && (
                            <small>
                              Required {check.required_nm.toFixed(4)} N·m;
                              declared limits {check.limits_nm?.join(", ")} N·m
                            </small>
                          )}
                        </div>
                      ))}
                      {candidate.sources.map((s) => (
                        <a
                          key={s.id}
                          href={s.url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {s.locator}
                        </a>
                      ))}
                    </details>
                  ))}
                </div>
              )}
            </details>
          );
        })}
      </fieldset>
      <Button
        disabled={
          disabled ||
          dirty ||
          !brief?.joint_requirements.length ||
          !brief.components.length
        }
        onClick={() =>
          run(async () => {
            const token = generation.current;
            const value = await post("joints/evaluate", {
              expected_revision: project.revision,
            });
            if (token === generation.current) setStatus(value);
          })
        }
      >
        Evaluate joint selection
      </Button>
      <details className="joint-contracts">
        <summary>Component contracts · {brief?.components.length ?? 0}</summary>
        <p>
          Sourced actuator snapshots include stall and interface facts.
          Continuous limits, physical fit and stock remain separate evidence.
        </p>
        {(brief?.components ?? []).map((c) => (
          <div key={c.id}>
            <strong>{c.mpn}</strong>
            <small>
              {" "}
              · {c.bus ?? "unknown bus"} · {c.revision}
            </small>
            <p>{c.scope}</p>
          </div>
        ))}
        <label className="field">
          <span>Component contracts JSON</span>
          <textarea
            aria-label="Component contracts JSON"
            rows={12}
            value={contracts}
            onChange={(e) => setContracts(e.target.value)}
          />
        </label>
        <Button
          disabled={disabled}
          onClick={() => {
            try {
              const data = JSON.parse(contracts);
              if (!Array.isArray(data))
                throw new Error("Expected a component array");
              edit((b) => {
                b.components = data;
              });
              setError("");
            } catch (e) {
              setError(String(e));
            }
          }}
        >
          Apply component contracts
        </Button>
      </details>
      {(
        result?.limits ?? [
          "Static sizing only. Sustained performance, mounting tolerances, firmware behavior and physical qualification need their own evidence.",
        ]
      ).map((line) => (
        <p className="joint-limit" key={line}>
          {line}
        </p>
      ))}
    </section>
  );
}
