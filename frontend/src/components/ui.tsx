import type { ComponentPropsWithRef } from "react";
import { DropdownMenu } from "radix-ui";

type ButtonProps = ComponentPropsWithRef<"button"> & {
  variant?: "ghost" | "primary" | "danger" | "glass";
  size?: "sm" | "md" | "icon";
};

export function Button({
  variant = "ghost",
  size = "sm",
  className = "",
  type = "button",
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      data-slot="button"
      data-variant={variant}
      data-size={size}
      className={`ui-button ${className}`.trim()}
      {...props}
    />
  );
}

export function Input({
  className = "",
  ...props
}: ComponentPropsWithRef<"input">) {
  return (
    <input
      data-slot="input"
      className={`ui-input ${className}`.trim()}
      {...props}
    />
  );
}

export function SelectControl({
  className = "",
  ...props
}: ComponentPropsWithRef<"select">) {
  return (
    <select
      data-slot="select"
      className={`ui-select ${className}`.trim()}
      {...props}
    />
  );
}

export const Menu = DropdownMenu.Root;
export const MenuTrigger = DropdownMenu.Trigger;
export const MenuItem = DropdownMenu.Item;
export const MenuSeparator = DropdownMenu.Separator;

export function MenuContent(
  props: ComponentPropsWithRef<typeof DropdownMenu.Content>,
) {
  return (
    <DropdownMenu.Portal>
      <DropdownMenu.Content
        align="end"
        sideOffset={8}
        collisionPadding={12}
        {...props}
      />
    </DropdownMenu.Portal>
  );
}
