import type { ReactNode } from "react";
import { ChevronDown } from "lucide-react";

export function InspectorSection({
  title,
  children,
  open = true,
}: {
  title: string;
  children: ReactNode;
  open?: boolean;
}) {
  return (
    <details className="inspector-section" open={open}>
      <summary>
        <span>{title}</span>
        <ChevronDown size={13} aria-hidden="true" />
      </summary>
      <div className="inspector-section-body">{children}</div>
    </details>
  );
}
