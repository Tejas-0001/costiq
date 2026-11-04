import Image from "next/image";
import logoNavy from "./brand/logo-navy.png";
import markNavy from "./brand/mark-navy.png";
import markWhite from "./brand/mark-white.png";

/** Miracle Minds mark (two figures forming an "M"), cut from the supplied logo artwork. `size` is the width in pixels;
 * `tone="light"` is the white version for dark backgrounds such as the sidebar. */
export function MMMark({ size = 34, tone = "navy" }: { size?: number; tone?: "navy" | "light" }) {
  const src = tone === "light" ? markWhite : markNavy;
  return <Image src={src} alt="Miracle Minds" width={size} height={Math.round((size * src.height) / src.width)} unoptimized priority />;
}

/** Miracle Minds lockup: the full logo (mark and wordmark), a divider, then CostIQ. Used in the site header. */
export function MMLockup({ sub }: { sub?: string }) {
  const h = 50;
  return (
    <span className="mm-lockup">
      <Image src={logoNavy} alt="Miracle Minds" height={h} width={Math.round((h * logoNavy.width) / logoNavy.height)} unoptimized priority />
      <span className="mm-div" />
      <span className="ciq"><span className="ciq-name">Cost<em>IQ</em></span>{sub && <span className="ciq-sub">{sub}</span>}</span>
    </span>
  );
}
