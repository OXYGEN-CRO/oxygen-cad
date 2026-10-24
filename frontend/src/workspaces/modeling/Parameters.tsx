import { useState } from "react";
import { Button, Input, SelectControl } from "../../components/ui";
import { json, type Execute, type NativeParameter } from "./native";

const safe = (promise: Promise<unknown>) => {
  void promise.catch(() => {});
};
function ParameterRow({
  parameter,
  execute,
  busy,
  locked,
  onLock,
}: {
  parameter: NativeParameter;
  execute: Execute;
  busy: boolean;
  locked: boolean;
  onLock: (name: string, locked: boolean) => Promise<void>;
}) {
  const [expression, setExpression] = useState(parameter.expression);
  const [name, setName] = useState(parameter.name);
  const [users, setUsers] = useState<unknown>(null);
  return (
    <div className="modeling-parameter">
      <form
        className="modeling-form"
        onSubmit={(e) => {
          e.preventDefault();
          safe(
            execute("parameters.change", {
              name: parameter.name,
              expression,
              ...(name !== parameter.name ? { new_name: name } : {}),
            }),
          );
        }}
      >
        <div className="modeling-row">
          <strong>{parameter.name}</strong>
          <Button
            aria-label={`${locked ? "Unlock" : "Lock"} parameter ${parameter.name}`}
            disabled={busy}
            onClick={() => safe(onLock(parameter.name, !locked))}
          >
            {locked ? "Locked · unlock" : "Lock"}
          </Button>
          <span className="modeling-muted">
            {parameter.value ?? "—"} {parameter.unit}
            {parameter.model ? " · model" : ""}
          </span>
        </div>
        <label>
          Name
          <Input
            aria-label={`Parameter name ${parameter.name}`}
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </label>
        <label>
          Expression
          <Input
            aria-label={`Expression ${parameter.name}`}
            value={expression}
            onChange={(e) => setExpression(e.target.value)}
            required
          />
        </label>
        {parameter.error && (
          <p className="modeling-danger" role="alert">
            {parameter.error}
          </p>
        )}
        <div className="modeling-row">
          <Button type="submit" disabled={busy}>
            Update parameter
          </Button>
          <Button
            disabled={busy}
            onClick={() =>
              safe(
                execute("parameters.users", { name: parameter.name }).then(
                  setUsers,
                ),
              )
            }
          >
            Dependencies
          </Button>
          <Button
            disabled={busy}
            variant="danger"
            onClick={() =>
              safe(execute("parameters.delete", { name: parameter.name }))
            }
          >
            Delete
          </Button>
        </div>
      </form>
      {users !== null && (
        <pre aria-label={`Dependencies ${parameter.name}`}>{json(users)}</pre>
      )}
    </div>
  );
}
export default function Parameters({
  parameters,
  execute,
  busy,
  locks = {},
  onLock,
}: {
  parameters: NativeParameter[];
  execute: Execute;
  busy: boolean;
  locks?: Record<string, boolean>;
  onLock: (name: string, locked: boolean) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [expression, setExpression] = useState("10 mm");
  const [unit, setUnit] = useState("mm");
  const [showModel, setShowModel] = useState(false);
  const [transfer, setTransfer] = useState("");
  const [graph, setGraph] = useState<unknown>(null);
  return (
    <div className="modeling-parameters">
      <p className="modeling-muted">
        Native parameters support equations and units. Cycles, incompatible
        units and used-parameter deletion are checked by the engine.
      </p>
      <form
        className="modeling-form"
        onSubmit={(e) => {
          e.preventDefault();
          safe(
            execute("parameters.add", { name, expression, unit }).then(() =>
              setName(""),
            ),
          );
        }}
      >
        <label>
          New parameter name
          <Input
            aria-label="New parameter name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </label>
        <label>
          Expression
          <Input
            aria-label="New parameter expression"
            value={expression}
            onChange={(e) => setExpression(e.target.value)}
            required
          />
        </label>
        <label>
          Unit
          <SelectControl value={unit} onChange={(e) => setUnit(e.target.value)}>
            {["mm", "cm", "m", "in", "ft", "deg", "rad", ""].map((u) => (
              <option key={u} value={u}>
                {u || "unitless"}
              </option>
            ))}
          </SelectControl>
        </label>
        <Button type="submit" disabled={busy} variant="primary">
          Add parameter
        </Button>
      </form>
      <label className="modeling-check">
        <input
          type="checkbox"
          checked={showModel}
          onChange={(e) => setShowModel(e.target.checked)}
        />
        Show model parameters
      </label>
      {parameters
        .filter((p) => showModel || !p.model)
        .map((p) => (
          <ParameterRow
            key={`${p.name}:${p.expression}`}
            parameter={p}
            execute={execute}
            busy={busy}
            locked={locks[p.name] === true}
            onLock={onLock}
          />
        ))}
      <Button
        disabled={busy}
        onClick={() => safe(execute("parameters.graph").then(setGraph))}
      >
        Inspect dependency graph
      </Button>
      {graph !== null && (
        <pre aria-label="Parameter dependency graph">{json(graph)}</pre>
      )}
      <details>
        <summary>Import / export parameter table</summary>
        <label>
          Parameter CSV or JSON
          <textarea
            aria-label="Parameter table"
            value={transfer}
            onChange={(e) => setTransfer(e.target.value)}
          />
        </label>
        <div className="modeling-row">
          <Button
            disabled={busy || !transfer.trim()}
            onClick={() =>
              safe(execute("parameters.import", { text: transfer }))
            }
          >
            Import table
          </Button>
          <Button
            disabled={busy}
            onClick={() =>
              safe(
                execute("parameters.export", { format: "csv" }).then((r) =>
                  setTransfer(String((r as { text?: string }).text ?? "")),
                ),
              )
            }
          >
            Export CSV
          </Button>
        </div>
      </details>
    </div>
  );
}
