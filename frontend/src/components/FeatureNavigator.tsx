import { useState, type Ref } from "react";
import {
  Box,
  Circle,
  Layers3,
  Search,
  Settings2,
  Square,
  X,
} from "lucide-react";
import { Button, Input } from "./ui";
import type { Project } from "../api";

export function FeatureNavigator({
  features,
  active,
  onSelect,
  searchRef,
}: {
  features: Project["features"];
  active: string;
  onSelect: (id: string) => void;
  searchRef: Ref<HTMLInputElement>;
}) {
  const [query, setQuery] = useState("");
  const filtered = features.filter((f) =>
    `${f.name} ${f.id} ${f.type}`
      .toLowerCase()
      .includes(query.trim().toLowerCase()),
  );
  return (
    <>
      <div className="feature-search">
        <Search size={14} aria-hidden="true" />
        <Input
          ref={searchRef}
          type="search"
          aria-label="Filter features"
          placeholder="Find a feature…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              setQuery("");
              event.currentTarget.blur();
              event.stopPropagation();
            }
          }}
        />
        {query ? (
          <Button
            aria-label="Clear feature filter"
            onClick={() => setQuery("")}
          >
            <X size={12} />
          </Button>
        ) : (
          <kbd>/</kbd>
        )}
      </div>
      <div className="feature-list" aria-label="Model features">
        {filtered.map((feature) => {
          const Icon =
            feature.type === "profile"
              ? feature.shape === "circle"
                ? Circle
                : Square
              : feature.type === "extrude"
                ? Box
                : feature.type === "native"
                  ? Layers3
                  : Settings2;
          return (
            <Button
              key={feature.id}
              className={`tree-item ${active === feature.id ? "selected" : ""} ${feature.suppressed ? "suppressed" : ""}`}
              aria-pressed={active === feature.id}
              onClick={() => onSelect(feature.id)}
            >
              <Icon size={16} />
              <span>{feature.name || feature.id}</span>
            </Button>
          );
        })}
        {!filtered.length && (
          <div className="feature-empty">
            {query ? "No matching features" : "No features yet"}
            {query && (
              <Button onClick={() => setQuery("")}>Clear filter</Button>
            )}
          </div>
        )}
      </div>
    </>
  );
}
