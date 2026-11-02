import type { Metadata } from "next";
import "@fontsource-variable/inter";
import "./globals.css";

export const metadata: Metadata = {
  title: "CostIQ · The Prudence Engine for electric utilities",
  description: "Every cloud, SaaS and AI dollar in your rate case: provable, recoverable or eliminated. Computed inside your boundary.",
};

const THEME_SCRIPT = `try{var t=localStorage.getItem("costiq-theme");if(t==="dark"||t==="light")document.documentElement.dataset.theme=t}catch(e){}`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // light by default; a saved choice is applied before first paint so the page never flashes the wrong theme
    <html lang="en" data-theme="light" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
