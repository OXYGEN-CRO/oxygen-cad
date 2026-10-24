import { ChevronRight } from "lucide-react";
import { Button } from "./ui";

type Crumb = {
  label: string;
  expanded: boolean;
  controls: string;
  onToggle: () => void;
};

export function Breadcrumbs({ items }: { items: Crumb[] }) {
  return (
    <nav className="breadcrumbs" aria-label="Breadcrumb">
      <ol>
        {items.map((item, index) => (
          <li key={item.controls}>
            {index > 0 && <ChevronRight size={12} aria-hidden="true" />}
            <Button
              aria-expanded={item.expanded}
              aria-controls={item.controls}
              onClick={item.onToggle}
              title={`${item.expanded ? "Collapse" : "Expand"} ${item.label}`}
            >
              {item.label}
            </Button>
          </li>
        ))}
      </ol>
    </nav>
  );
}
