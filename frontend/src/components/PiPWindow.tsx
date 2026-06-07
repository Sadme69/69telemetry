"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

interface Props {
  children: React.ReactNode;
  onClose: () => void;
  width?: number;
  height?: number;
}

export default function PiPWindow({
  children,
  onClose,
  width = 480,
  height = 720,
}: Props) {
  const [pipWindow, setPipWindow] = useState<Window | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const closedRef = useRef(false);

  useEffect(() => {
    let pipWin: Window | null = null;
    closedRef.current = false;

    pipWin = window.open("", "_blank", `width=${width},height=${height},popup=yes`);
    if (!pipWin) {
      onClose();
      return;
    }

    pipWin.document.title = "F1 Replay — PiP";
    pipWin.document.body.style.margin = "0";
    pipWin.document.body.style.padding = "0";
    pipWin.document.body.style.backgroundColor = "#15151e";
    pipWin.document.body.style.color = "#e5e7eb";
    pipWin.document.body.style.overflow = "hidden";

    // Copy stylesheets
    for (const sheet of document.styleSheets) {
      try {
        if (sheet.href) {
          const link = pipWin.document.createElement("link");
          link.rel = "stylesheet";
          link.href = sheet.href;
          pipWin.document.head.appendChild(link);
        } else if (sheet.cssRules) {
          const style = pipWin.document.createElement("style");
          for (const rule of sheet.cssRules) {
            style.textContent += rule.cssText + "\n";
          }
          pipWin.document.head.appendChild(style);
        }
      } catch {
        // Cross-origin stylesheet, skip
      }
    }

    // Copy CSS custom properties from main document root
    const mainStyles = getComputedStyle(document.documentElement);
    const cssVars = ["--f1-muted"];
    for (const v of cssVars) {
      pipWin.document.documentElement.style.setProperty(v, mainStyles.getPropertyValue(v));
    }
    // Copy high-contrast class if active
    if (document.documentElement.classList.contains("high-contrast")) {
      pipWin.document.documentElement.classList.add("high-contrast");
    }

    const mount = pipWin.document.createElement("div");
    mount.id = "pip-root";
    mount.style.width = "100%";
    mount.style.height = "100vh";
    mount.style.display = "flex";
    mount.style.flexDirection = "column";
    pipWin.document.body.appendChild(mount);
    containerRef.current = mount;

    pipWin.addEventListener("beforeunload", () => {
      if (!closedRef.current) {
        closedRef.current = true;
        onClose();
      }
    });

    setPipWindow(pipWin);

    return () => {
      closedRef.current = true;
      if (pipWin && !pipWin.closed) {
        pipWin.close();
      }
      setPipWindow(null);
      containerRef.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (!pipWindow || !containerRef.current) return null;

  return createPortal(children, containerRef.current);
}
