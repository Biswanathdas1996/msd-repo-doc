/**
 * Initial "fit" scale for large Mermaid SVGs.
 * Tall diagrams must not be shrunk to fit viewport height — that makes labels microscopic.
 * Prefer fitting width and scrolling vertically (parent has overflow-auto).
 */
export function computeReadableFitScale(
  containerW: number,
  containerH: number,
  svgW: number,
  svgH: number,
  padding = 0.92,
): number {
  if (svgW <= 0 || svgH <= 0 || containerW <= 0 || containerH <= 0) return 1;
  const scaleW = (containerW / svgW) * padding;
  const scaleH = (containerH / svgH) * padding;
  const isTall = svgH > svgW * 1.12;
  const fit = isTall ? scaleW : Math.min(scaleW, scaleH);
  return Math.max(0.08, Math.min(fit, 4));
}
