import type { ComponentPropsWithRef } from "react";

type ButtonProps = ComponentPropsWithRef<"button"> & {
  variant?: "ghost" | "primary" | "danger";
};

export function Button({
  variant = "ghost",
  className = "",
  type = "button",
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      data-variant={variant}
      className={`ui-button ${className}`.trim()}
      {...props}
    />
  );
}

export function Input({
  className = "",
  ...props
}: ComponentPropsWithRef<"input">) {
  return <input className={`ui-input ${className}`.trim()} {...props} />;
}

export function SelectControl({
  className = "",
  ...props
}: ComponentPropsWithRef<"select">) {
  return <select className={`ui-select ${className}`.trim()} {...props} />;
}
