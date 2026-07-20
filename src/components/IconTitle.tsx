import type { ReactNode } from "react";


export function IconTitle({ icon, text }: { icon: ReactNode; text: string }) {
  return (
    <span className="iconTitle">
      {icon}
      <span>{text}</span>
    </span>
  );
}
