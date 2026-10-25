import { useEffect, useRef, useState } from "react";
import type { Project } from "./api";
import type { BriefLink } from "./generated";
import { api, post, artifact } from "./api";
import { BriefPanel, type BriefResult } from "./BriefPanel";
import {
  CompatibilityPanel,
  type CompatibilityInputs,
  type CompatibilityResult,
  type AdapterResult,
} from "./CompatibilityPanel";
import { ProcurementPanel, type ProcurementReport } from "./ProcurementPanel";
import { SupplierPanel, type SupplierResult } from "./SupplierPanel";
import { Button, Input } from "./components/ui";
import "./engineering.css";

type Props = {
  project: Project;
  dirty: boolean;
  blocked: boolean;
  onEdit: (fn: (project: Project) => void) => void;
  onBind: () => Promise<BriefResult>;
};
const initialInterfaces: CompatibilityInputs = {
  a: "{}",
  b: "{}",
  fastenerDiameterMm: "",
  thicknessMm: "",
  edgeClearanceMm: "",
  webClearanceMm: "",
  machiningClearanceMm: "",
};
const pretty = (data: unknown) => JSON.stringify(data, null, 2);

/** Drafts and offers stay in memory. Only the explicit brief save edits the project. */
export function EngineeringPanel({
  project,
  dirty,
  blocked,
  onEdit,
  onBind,
}: Props) {
  const [tab, setTab] = useState("brief");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [briefResult, setBriefResult] = useState<BriefResult | null>(null);
  const [interfaces, setInterfaces] = useState(initialInterfaces);
  const [comparison, setComparison] = useState<CompatibilityResult | null>(
    null,
  );
  const [adapter, setAdapter] = useState<AdapterResult | null>(null);
  const [bom, setBom] = useState("{}");
  const [procurement, setProcurement] = useState<ProcurementReport | null>(
    null,
  );
  const [profile, setProfile] = useState("{}");
  const [request, setRequest] = useState("{}");
  const [sectionZ, setSectionZ] = useState("");
  const [overhang, setOverhang] = useState("");
  const [sourceText, setSourceText] = useState("");
  const [supplier, setSupplier] = useState<SupplierResult | null>(null);
  const generation = useRef(0);
  const revision = useRef(project.revision);
  revision.current = project.revision;
  const invalidate = () => {
    generation.current++;
    setError("");
  };
  useEffect(() => {
    generation.current++;
    setBriefResult((value) =>
      value?.revision === project.revision ? value : null,
    );
    setComparison(null);
    setAdapter(null);
    setProcurement(null);
    setSupplier(null);
  }, [project.revision]);
  async function run(work: () => Promise<void>) {
    setError("");
    setBusy(true);
    try {
      await work();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  async function evaluate(
    path: string,
    data: unknown,
    receive: (result: any) => void,
  ) {
    const token = generation.current,
      sourceRevision = revision.current;
    const result = await post(`engineering/${path}`, data);
    if (token === generation.current && sourceRevision === revision.current)
      receive(result);
  }
  async function loadExample() {
    const result = await api(`engineering/examples/${tab}`);
    invalidate();
    if (tab === "brief")
      onEdit((p) => {
        const existing = p.engineering_brief;
        p.engineering_brief = {
          ...result,
          // Authored examples may omit schema-defaulted reference arrays. The
          // controlled editor needs those arrays before the first Store save.
          links: (result.links ?? []).map((link: BriefLink) => ({
            ...link,
            parameter_refs: link.parameter_refs ?? [],
            input_refs: link.input_refs ?? [],
          })),
          joint_requirements:
            existing?.joint_requirements ?? result.joint_requirements ?? [],
          components: existing?.components ?? result.components ?? [],
        };
      });
    else if (tab === "interfaces") {
      const input = result.adapter_request ?? result.adapter ?? result;
      setInterfaces({
        a: pretty(input.a),
        b: pretty(input.b),
        fastenerDiameterMm:
          result.compare_request?.fastener_diameter_mm ??
          result.fastener_diameter_mm ??
          "",
        thicknessMm: input.thickness_mm ?? "",
        edgeClearanceMm: input.edge_clearance_mm ?? "",
        webClearanceMm: input.web_clearance_mm ?? "",
        machiningClearanceMm: input.machining_clearance_mm ?? "",
      });
      setComparison(null);
      setAdapter(null);
    } else if (tab === "procurement") {
      setBom(pretty(result));
      setProcurement(null);
    } else {
      setProfile(pretty(result.profile));
      setRequest(pretty(result.request));
      setSupplier(null);
    }
  }
  function downloadAdapter() {
    if (!adapter) return;
    const url = URL.createObjectURL(
      new Blob([pretty(adapter.project)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = "adapter-proposal.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <section className="engineering-workflow" aria-label="Engineering workflow">
      <div className="engineering-tabs" aria-label="Engineering tools">
        {[
          ["brief", "Brief"],
          ["interfaces", "Interfaces"],
          ["procurement", "Purchasing"],
          ["supplier", "Manufacturing"],
        ].map(([id, label]) => (
          <Button key={id} aria-pressed={tab === id} onClick={() => setTab(id)}>
            {label}
          </Button>
        ))}
      </div>
      <div className="engineering-example">
        <Button disabled={busy || blocked} onClick={() => run(loadExample)}>
          Load example
        </Button>
        <small>Synthetic inputs for exploring this tool.</small>
      </div>
      {dirty && (
        <p role="status">
          Save project changes before evaluating or binding evidence.
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      {tab === "brief" && (
        <BriefPanel
          project={project}
          data={project.engineering_brief}
          result={
            !dirty && briefResult?.revision === project.revision
              ? briefResult
              : null
          }
          disabled={busy || blocked}
          onChange={(value) => {
            invalidate();
            setBriefResult(null);
            onEdit((p) => {
              p.engineering_brief = value;
            });
          }}
          onEvaluate={
            dirty
              ? undefined
              : () => run(() => evaluate("brief/inspect", {}, setBriefResult))
          }
          onBind={
            dirty
              ? undefined
              : () =>
                  run(async () => {
                    const result = await onBind();
                    setBriefResult(result);
                  })
          }
        />
      )}
      {tab === "interfaces" && (
        <>
          <CompatibilityPanel
            inputs={interfaces}
            onInputsChange={(value) => {
              invalidate();
              setInterfaces(value);
              setComparison(null);
              setAdapter(null);
            }}
            result={comparison}
            proposal={adapter}
            busy={busy || blocked || dirty}
            onCompare={(data) =>
              run(() => evaluate("compatibility/evaluate", data, setComparison))
            }
            onGenerate={(data) =>
              run(() => evaluate("compatibility/adapter", data, setAdapter))
            }
          />
          {adapter && (
            <Button onClick={downloadAdapter}>
              Download editable adapter proposal
            </Button>
          )}
        </>
      )}
      {tab === "procurement" && (
        <ProcurementPanel
          value={bom}
          onChange={(value) => {
            invalidate();
            setBom(value);
            setProcurement(null);
          }}
          result={procurement}
          busy={busy || blocked || dirty}
          onEvaluate={(data) =>
            run(() => evaluate("procurement/evaluate", data, setProcurement))
          }
        />
      )}
      {tab === "supplier" && (
        <>
          <label className="field">
            <span>Section height (mm)</span>
            <Input
              aria-label="Handoff section height (mm)"
              type="number"
              value={sectionZ}
              onChange={(e) => setSectionZ(e.target.value)}
            />
          </label>
          <label className="field">
            <span>Overhang screening angle (degrees)</span>
            <Input
              aria-label="Handoff overhang angle (degrees)"
              type="number"
              value={overhang}
              onChange={(e) => setOverhang(e.target.value)}
            />
          </label>
          <Button
            disabled={
              busy || blocked || dirty || !sectionZ.trim() || !overhang.trim()
            }
            onClick={() =>
              run(async () => {
                const result = await post("engineering/supplier/prepare", {
                  section_z_mm: Number(sectionZ),
                  overhang_degrees: Number(overhang),
                  expected_revision: project.revision,
                  actor: "human",
                });
                const value = JSON.parse(request);
                value.handoff_manifest = result.manifest;
                invalidate();
                setRequest(pretty(value));
                setSupplier(null);
              })
            }
          >
            Create design handoff
          </Button>
          <label className="field">
            <span>Supplier source excerpt</span>
            <textarea
              aria-label="Supplier source excerpt"
              rows={4}
              value={sourceText}
              onChange={(e) => setSourceText(e.target.value)}
            />
          </label>
          <Button
            disabled={busy || blocked || !sourceText.trim()}
            onClick={() =>
              run(async () => {
                const attached = await post("engineering/supplier/source", {
                  content: sourceText,
                });
                const value = JSON.parse(profile);
                value.source = {
                  ...value.source,
                  capture: attached.capture,
                  sha256: attached.sha256,
                };
                invalidate();
                setProfile(pretty(value));
                setSupplier(null);
              })
            }
          >
            Attach source evidence
          </Button>
          <p>
            Attach the text used for the selected supplier rules. Supplied text
            remains unverified.
          </p>
          <SupplierPanel
            profileJson={profile}
            requestJson={request}
            onProfileChange={(value) => {
              invalidate();
              setProfile(value);
              setSupplier(null);
            }}
            onRequestChange={(value) => {
              invalidate();
              setRequest(value);
              setSupplier(null);
            }}
            result={dirty ? null : supplier}
            busy={busy || blocked || dirty}
            artifactUrl={artifact}
            onEvaluate={() =>
              run(() =>
                evaluate(
                  "supplier/evaluate",
                  {
                    profile: JSON.parse(profile),
                    request: JSON.parse(request),
                  },
                  setSupplier,
                ),
              )
            }
            onPackage={() =>
              run(() =>
                evaluate(
                  "supplier/package",
                  {
                    profile: JSON.parse(profile),
                    request: JSON.parse(request),
                    expected_revision: project.revision,
                    actor: "human",
                  },
                  setSupplier,
                ),
              )
            }
          />
        </>
      )}
    </section>
  );
}
