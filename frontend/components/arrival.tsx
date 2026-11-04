"use client";
// Deep links from the AI summary: ?sec= names a panel (its heading or KPI label) and ?hl= the text of a row in it.
// Once the page's data has rendered, it scrolls to the row (or the panel) and highlights it for a moment.
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect } from "react";

const norm = (t: string | null | undefined) => (t ?? "").replace(/\s+/g, " ").trim().toLowerCase();

function findPanel(root: ParentNode, sec: string): HTMLElement | null {
  const want = norm(sec);
  for (const el of root.querySelectorAll<HTMLElement>(".card, .bp-sec")) {
    if (el.closest(".ais-body")) continue;
    const head = el.querySelector(":scope > h2, :scope > .card-head h2, :scope > .label, .bp-head h2");
    if (head && norm(head.textContent).includes(want)) return el;
  }
  return null;
}

function findRow(root: ParentNode, hl: string): HTMLElement | null {
  const want = norm(hl);
  const exact = [...root.querySelectorAll<HTMLElement>("tbody td")].find((td) => norm(td.textContent) === want);
  if (exact) return exact.closest("tr");
  return [...root.querySelectorAll<HTMLElement>("tbody tr")].find((tr) => norm(tr.textContent).includes(want)) ?? null;
}

function flash(el: HTMLElement) {
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.remove("arrive");
  void el.offsetWidth; // restart the animation when the same target is linked twice
  el.classList.add("arrive");
  window.setTimeout(() => el.classList.remove("arrive"), 3200);
}

export function Arrival() {
  const path = usePathname();
  const params = useSearchParams();
  const sec = params.get("sec");
  const hl = params.get("hl");
  useEffect(() => {
    if (!sec && !hl) return;
    let tries = 0;
    const timer = window.setInterval(() => {
      tries += 1;
      const root = document.querySelector(".content") ?? document;
      const panel = sec ? findPanel(root, sec) : null;
      const row = hl ? (panel && findRow(panel, hl)) || findRow(root, hl) : null;
      const target = row || (!hl || tries > 12 ? panel : null); // give rows a moment to load before settling for the panel
      if (target || tries > 40) {
        window.clearInterval(timer);
        if (target) flash(target);
      }
    }, 150);
    return () => window.clearInterval(timer);
  }, [path, sec, hl]);
  return null;
}
