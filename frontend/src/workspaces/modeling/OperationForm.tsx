import { useEffect, useState } from "react";
import { Button, Input, SelectControl } from "../../components/ui";
import type { Selection } from "../../api";
import type { JsonObject as ObjectData } from "../contracts";
import { errorText, type Execute, type NativeCommand } from "./native";
import { operationParams, type Operation } from "./operations";

export default function OperationForm({
  operation,
  execute,
  busy,
  command,
  initial = {},
  selection,
}: {
  operation: Operation;
  execute: Execute;
  busy: boolean;
  command?: NativeCommand;
  initial?: ObjectData;
  selection: Selection | null;
}) {
  const defaults = () =>
    Object.fromEntries(
      operation.fields.map((f) => {
        const value = initial[f.key];
        return [
          f.key,
          value === undefined
            ? (f.value ?? "")
            : f.kind === "json"
              ? JSON.stringify(value)
              : f.kind === "names" && Array.isArray(value)
                ? value.join(", ")
                : String(value),
        ];
      }),
    );
  const [values, setValues] = useState<Record<string, string>>(defaults);
  const [error, setError] = useState("");
  const [help, setHelp] = useState("");
  useEffect(() => {
    setHelp(command?.params ?? "");
  }, [command?.params]);
  const useSelection = () => {
    if (!selection) return;
    const point = JSON.stringify(selection.point);
    setValues((previous) => {
      const next = { ...previous };
      for (const f of operation.fields) {
        if (["position", "face"].includes(f.key)) next[f.key] = point;
        if (f.key === "faces") next[f.key] = `[${point}]`;
      }
      return next;
    });
  };
  const unavailable =
    !command ||
    command.host_allowed === false ||
    !!command.planned ||
    !command.enabled;
  return (
    <form
      className="modeling-form modeling-operation"
      aria-label={`${operation.label} operation`}
      onSubmit={(event) => {
        event.preventDefault();
        setError("");
        try {
          void execute(
            operation.command,
            operationParams(operation, values),
          ).catch((e) => setError(errorText(e)));
        } catch (e) {
          setError(errorText(e));
        }
      }}
    >
      <h4>{operation.label}</h4>
      {operation.fields.map((field) => (
        <label
          key={field.key}
          className={field.kind === "boolean" ? "modeling-check" : ""}
        >
          {field.kind === "boolean" ? (
            <>
              <input
                type="checkbox"
                checked={values[field.key] === "true"}
                onChange={(e) =>
                  setValues({
                    ...values,
                    [field.key]: String(e.target.checked),
                  })
                }
              />
              {field.label}
            </>
          ) : (
            <>
              {field.label}
              {field.optional ? <small>optional</small> : null}
              {field.options ? (
                <SelectControl
                  aria-label={field.label}
                  value={values[field.key] ?? ""}
                  onChange={(e) =>
                    setValues({ ...values, [field.key]: e.target.value })
                  }
                >
                  {field.options.map((option) => (
                    <option key={option}>{option}</option>
                  ))}
                </SelectControl>
              ) : (
                <Input
                  aria-label={field.label}
                  value={values[field.key] ?? ""}
                  required={!field.optional}
                  onChange={(e) =>
                    setValues({ ...values, [field.key]: e.target.value })
                  }
                  placeholder={
                    field.kind === "names" ? "Name1, Name2" : field.value
                  }
                />
              )}
            </>
          )}
        </label>
      ))}
      {selection &&
        operation.fields.some((f) =>
          ["position", "faces", "face"].includes(f.key),
        ) && <Button onClick={useSelection}>Use selected face point</Button>}
      <p className="modeling-muted">
        Expressions accept native parameters and units, for example{" "}
        <code>width / 2 + 1 mm</code>. Geometry is evaluated when applied.
      </p>
      {unavailable && (
        <p className="modeling-warning">
          {command?.planned ||
            command?.disabled_reason ||
            "This operation is unavailable in the current engine context."}
        </p>
      )}
      {error && (
        <p className="modeling-danger" role="alert">
          {error}
        </p>
      )}
      <Button type="submit" variant="primary" disabled={busy || unavailable}>
        Apply {operation.label.toLowerCase()}
      </Button>
      <details>
        <summary>Native operation details</summary>
        <p className="modeling-muted">{help}</p>
        <code>{operation.command}</code>
        <Button
          disabled={busy}
          onClick={() => {
            void execute("engine.describe", { command: operation.command })
              .then((result) =>
                setHelp(String((result as ObjectData).params ?? "")),
              )
              .catch(() => {});
          }}
        >
          Inspect command contract
        </Button>
      </details>
    </form>
  );
}
