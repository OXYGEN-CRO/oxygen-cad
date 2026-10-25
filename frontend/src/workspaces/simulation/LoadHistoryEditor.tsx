import { useEffect, useState } from "react";
import { Button } from "../../components/ui";

export default function LoadHistoryEditor(props: {
  label: string;
  value: number[][];
  onApply: (history: number[][]) => void;
  onError: (message: string) => void;
}) {
  const serialized = props.value.map((point) => point.join(", ")).join("\n");
  const [text, setText] = useState(serialized);
  useEffect(() => setText(serialized), [serialized]);
  return (
    <div>
      <label>
        {props.label}
        <textarea
          aria-label={props.label}
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={"0, 0\n1, 1"}
        />
      </label>
      <Button
        aria-label={`Apply ${props.label}`}
        onClick={() => {
          try {
            const history = text
              .split("\n")
              .filter((line) => line.trim())
              .map((line) => {
                const parts = line.split(",");
                if (parts.length !== 2 || parts.some((part) => !part.trim()))
                  throw new Error("Each history row needs time, multiplier.");
                const values = parts.map(Number);
                if (!values.every(Number.isFinite))
                  throw new Error("History values must be finite.");
                return values;
              });
            if (
              history.length &&
              (history.length < 2 ||
                history[0][0] !== 0 ||
                history.some(
                  (row, index) => index > 0 && row[0] <= history[index - 1][0],
                ))
            )
              throw new Error(
                "History needs at least two points, starts at zero and has increasing times.",
              );
            props.onApply(history);
          } catch (error) {
            props.onError(String(error));
          }
        }}
      >
        Apply history
      </Button>
    </div>
  );
}
