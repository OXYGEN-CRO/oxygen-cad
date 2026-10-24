import type { State } from "../api";

const quantities = [
  ["max_displacement_mm", "Maximum displacement", "mm"],
  ["stress_probe_mpa", "Stress probe", "MPa"],
  ["max_temperature_c", "Maximum temperature", "°C"],
  ["min_temperature_c", "Minimum temperature", "°C"],
  ["frequencies_hz", "Frequencies", "Hz"],
  ["buckling_factors", "Buckling factors", "×"],
];

export function StudyComparison({ studies }: { studies: State["studies"] }) {
  const records = Object.values(studies).filter((s) => s.result);
  if (records.length < 2) return null;
  return (
    <details className="study-comparison">
      <summary>Compare studies</summary>
      <p className="muted">
        Current results only. Compare loads, supports and assumptions before
        choosing a design.
      </p>
      <div className="study-table-scroll">
        <table className="study-table" aria-label="Study comparison">
          <thead>
            <tr>
              <th>Quantity</th>
              {records.map((s) => (
                <th key={s.id}>
                  {s.name}
                  <small>{s.state.replaceAll("_", " ")}</small>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {quantities
              .filter(
                ([metric]) =>
                  records.filter((s) => s.result[metric] != null).length >= 2,
              )
              .map(([metric, label, unit]) => (
                <tr key={metric}>
                  <td>
                    {label} <small>{unit}</small>
                  </td>
                  {records.map((s) => (
                    <td key={s.id}>
                      {s.result[metric] == null
                        ? "—"
                        : Array.isArray(s.result[metric])
                          ? s.result[metric]
                              .map((v: number) => v.toPrecision(4))
                              .join(", ")
                          : s.result[metric].toPrecision(4)}
                    </td>
                  ))}
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
