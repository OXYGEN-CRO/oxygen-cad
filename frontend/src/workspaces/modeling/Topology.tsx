import { useState } from "react";
import { Button, SelectControl } from "../../components/ui";
import type { JsonObject } from "../contracts";
import { type Execute, json } from "./native";

type TopologyItem = JsonObject & {
  index: number;
  name?: string;
  point?: unknown;
  mid?: unknown;
  area?: number;
  length?: number;
};
export default function Topology({
  bodies,
  execute,
  busy,
  onSelection,
}: {
  bodies: string[];
  execute: Execute;
  busy: boolean;
  onSelection: (data: JsonObject) => void;
}) {
  const [body, setBody] = useState(bodies[0] ?? "");
  const [kind, setKind] = useState<"faces" | "edges">("faces");
  const [items, setItems] = useState<TopologyItem[]>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [measures, setMeasures] = useState<unknown>(null);
  const load = async () => {
    const result = (await execute(`model.${kind}`, { body })) as JsonObject;
    setItems(result[kind] as TopologyItem[]);
    setSelected([]);
  };
  const choose = async (index: number, checked: boolean) => {
    const next = checked
      ? [...selected, index]
      : selected.filter((i) => i !== index);
    setSelected(next);
    const chosen = items.filter((item) => next.includes(item.index));
    await execute("select.set", {
      items: chosen.map((item) => ({
        type: kind === "faces" ? "face" : "edge",
        body,
        index: item.index,
        point: item.point ?? item.mid,
      })),
    });
    onSelection(
      kind === "faces"
        ? {
            body,
            faces: chosen.map((item) => item.name ?? item.point),
            ...(chosen[0]
              ? { face: chosen[0].point, position: chosen[0].point }
              : {}),
          }
        : { body, edges: chosen.map((item) => ({ body, index: item.index })) },
    );
  };
  return (
    <details className="modeling-topology">
      <summary>Native bodies, faces and edges</summary>
      <div className="modeling-row">
        <label>
          Body
          <SelectControl
            aria-label="Native body"
            value={body}
            onChange={(e) => {
              setBody(e.target.value);
              setItems([]);
            }}
          >
            <option value="">Choose body</option>
            {bodies.map((name) => (
              <option key={name}>{name}</option>
            ))}
          </SelectControl>
        </label>
        <label>
          Selection filter
          <SelectControl
            value={kind}
            onChange={(e) => {
              setKind(e.target.value as "faces" | "edges");
              setItems([]);
            }}
          >
            {["faces", "edges"].map((k) => (
              <option key={k}>{k}</option>
            ))}
          </SelectControl>
        </label>
        <Button
          disabled={busy || !body}
          onClick={() => void load().catch(() => {})}
        >
          List topology
        </Button>
      </div>
      <div className="modeling-topology-list">
        {items.map((item) => (
          <label key={item.index} className="modeling-check">
            <input
              type="checkbox"
              checked={selected.includes(item.index)}
              disabled={busy}
              onChange={(e) =>
                void choose(item.index, e.target.checked).catch(() => {})
              }
            />
            <span>
              {kind === "faces" ? "Face" : "Edge"} {item.index}
              <small>{item.name ?? "Uncertified name"}</small>
              <small>
                {item.area !== undefined
                  ? `${item.area.toFixed(3)} mm²`
                  : item.length !== undefined
                    ? `${item.length.toFixed(3)} mm`
                    : ""}
              </small>
            </span>
          </label>
        ))}
      </div>
      <Button
        disabled={busy || !body}
        onClick={() =>
          void execute("inspect.measure", { bodies: [body] })
            .then(setMeasures)
            .catch(() => {})
        }
      >
        Measure body
      </Button>
      {measures !== null && (
        <pre aria-label="Native body measurements">{json(measures)}</pre>
      )}
      <p className="modeling-muted">
        Native topology selections feed the operation form. A refused or lost
        reference remains an error; choose an explicit replacement.
      </p>
    </details>
  );
}
