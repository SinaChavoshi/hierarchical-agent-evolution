"""Generates the publication-grade fitness trajectory chart (SVG + PNG).

This generator is fully data-driven: it reads
`experiments/standardized_execution_fitness.json` (produced by
`scripts/recalculate_standardized_fitness.py`) and derives every coordinate,
axis bound, and label from that file.

Earlier revisions hardcoded SVG path coordinates, which required hand-editing
the chart on every new generation and had already drifted out of sync with the
measured data (Generation 10 was still drawn as a dashed projection after it had
completed). Adding a generation now requires no changes to this file.

Usage:
    PYTHONPATH=. python3 scripts/generate_standardized_trajectory_charts.py
"""

import json
import os
import subprocess
import xml.etree.ElementTree as ET

DATA_PATH = "experiments/standardized_execution_fitness.json"
OUT_SVG = "experiments/assets/standardized_fitness_trajectory.svg"
OUT_PNG = "experiments/assets/standardized_fitness_trajectory.png"

# Canvas geometry
W, H = 1180, 660
PAD_L, PAD_R, PAD_T, PAD_B = 90, 240, 96, 118

# Score axis bounds
Y_MIN, Y_MAX = 0.0, 100.0

SERIES_GRADIENT = [
    (0, "#ec4899"), (18, "#f59e0b"), (40, "#10b981"),
    (60, "#06b6d4"), (80, "#3b82f6"), (100, "#a855f7"),
]


def esc(s):
    """Escapes text for safe inclusion in XML character data."""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def load_generations():
    with open(DATA_PATH) as fh:
        rows = json.load(fh)
    return rows


def build_svg(rows):
    n = len(rows)
    plot_w = W - PAD_L - PAD_R
    plot_h = H - PAD_T - PAD_B

    def x_of(i):
        if n == 1:
            return PAD_L + plot_w / 2.0
        return round(PAD_L + plot_w * i / (n - 1), 1)

    def y_of(score):
        frac = (score - Y_MIN) / (Y_MAX - Y_MIN)
        return round(PAD_T + plot_h * (1.0 - frac), 1)

    legacy = [(x_of(i), y_of(r["raw_champion"])) for i, r in enumerate(rows)]
    grounded = [(x_of(i), y_of(r["corrected_champion"])) for i, r in enumerate(rows)]

    # The legacy and grounded tracks only diverge before live sandboxing existed.
    divergent = [i for i, r in enumerate(rows) if not r["physical_sandbox"]]
    first_physical = next((i for i, r in enumerate(rows) if r["physical_sandbox"]), n)

    p = []
    a = p.append

    a('<?xml version="1.0" encoding="UTF-8"?>')
    a(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">')

    # ---- defs ----
    a("  <defs>")
    a('    <linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%">')
    a('      <stop offset="0%" stop-color="#070b14" />')
    a('      <stop offset="50%" stop-color="#0b1120" />')
    a('      <stop offset="100%" stop-color="#0f172a" />')
    a("    </linearGradient>")
    a('    <linearGradient id="legacyGrad" x1="0%" y1="0%" x2="100%" y2="0%">')
    a('      <stop offset="0%" stop-color="#64748b" />')
    a('      <stop offset="100%" stop-color="#cbd5e1" />')
    a("    </linearGradient>")
    a('    <linearGradient id="groundedGrad" x1="0%" y1="0%" x2="100%" y2="0%">')
    for off, col in SERIES_GRADIENT:
        a(f'      <stop offset="{off}%" stop-color="{col}" />')
    a("    </linearGradient>")
    a('    <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">')
    a('      <feGaussianBlur stdDeviation="4" result="b" />')
    a('      <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>')
    a("    </filter>")
    a("  </defs>")

    a(f'  <rect width="{W}" height="{H}" fill="url(#bgGrad)" />')

    # ---- titles ----
    a(f'  <text x="{PAD_L}" y="42" fill="#f8fafc" font-family="Helvetica,Arial,sans-serif" '
      f'font-size="25" font-weight="700">Hierarchical Agent Evolution &#8212; Champion Fitness Trajectory</text>')
    a(f'  <text x="{PAD_L}" y="68" fill="#94a3b8" font-family="Helvetica,Arial,sans-serif" '
      f'font-size="14">Legacy unchecked score vs. standardized grounded score under the Physical Execution Standard</text>')

    # ---- y gridlines ----
    for score in range(int(Y_MIN), int(Y_MAX) + 1, 10):
        y = y_of(score)
        a(f'  <line x1="{PAD_L}" y1="{y}" x2="{W - PAD_R}" y2="{y}" stroke="#1e293b" stroke-width="1" />')
        a(f'  <text x="{PAD_L - 12}" y="{y + 4}" fill="#64748b" text-anchor="end" '
          f'font-family="Helvetica,Arial,sans-serif" font-size="12">{score}</text>')

    # ---- axes ----
    a(f'  <line x1="{PAD_L}" y1="{PAD_T}" x2="{PAD_L}" y2="{H - PAD_B}" stroke="#334155" stroke-width="2" />')
    a(f'  <line x1="{PAD_L}" y1="{H - PAD_B}" x2="{W - PAD_R}" y2="{H - PAD_B}" stroke="#334155" stroke-width="2" />')

    # ---- physical-execution era shading ----
    if first_physical < n:
        xs = x_of(first_physical)
        a(f'  <rect x="{xs}" y="{PAD_T}" width="{W - PAD_R - xs}" height="{plot_h}" '
          f'fill="#10b981" opacity="0.055" />')
        a(f'  <line x1="{xs}" y1="{PAD_T}" x2="{xs}" y2="{H - PAD_B}" stroke="#10b981" '
          f'stroke-width="1.5" stroke-dasharray="5,4" opacity="0.65" />')
        a(f'  <text x="{xs + 10}" y="{PAD_T + 18}" fill="#34d399" '
          f'font-family="Helvetica,Arial,sans-serif" font-size="12" font-weight="600">'
          f'Live sandbox execution begins</text>')

    # ---- legacy track (only meaningful where it diverges) ----
    if divergent:
        seg = [legacy[i] for i in range(0, min(first_physical + 1, n))]
        pts = " ".join(f"{x},{y}" for x, y in seg)
        a(f'  <polyline points="{pts}" fill="none" stroke="url(#legacyGrad)" stroke-width="3" '
          f'stroke-dasharray="7,5" stroke-linecap="round" />')
        for i in divergent:
            x, y = legacy[i]
            a(f'  <circle cx="{x}" cy="{y}" r="5" fill="#0b1120" stroke="#94a3b8" stroke-width="2" />')
            a(f'  <text x="{x}" y="{y - 13}" fill="#94a3b8" text-anchor="middle" '
              f'font-family="Helvetica,Arial,sans-serif" font-size="11">'
              f'{rows[i]["raw_champion"]:.1f}</text>')

    # ---- grounded track ----
    pts = " ".join(f"{x},{y}" for x, y in grounded)
    a(f'  <polyline points="{pts}" fill="none" stroke="url(#groundedGrad)" stroke-width="4" '
      f'stroke-linecap="round" stroke-linejoin="round" filter="url(#glow)" />')

    best = max(range(n), key=lambda i: rows[i]["corrected_champion"])
    for i, (x, y) in enumerate(grounded):
        r = rows[i]
        is_best = (i == best)
        a(f'  <circle cx="{x}" cy="{y}" r="{7 if is_best else 5.5}" fill="#0b1120" '
          f'stroke="{"#fbbf24" if is_best else "#e2e8f0"}" stroke-width="{3 if is_best else 2}" />')
        a(f'  <text x="{x}" y="{y + 26}" fill="#f1f5f9" text-anchor="middle" '
          f'font-family="Helvetica,Arial,sans-serif" font-size="12" font-weight="700">'
          f'{r["corrected_champion"]:.1f}</text>')

    # ---- x labels ----
    for i, r in enumerate(rows):
        x = x_of(i)
        a(f'  <text x="{x}" y="{H - PAD_B + 24}" fill="#cbd5e1" text-anchor="middle" '
          f'font-family="Helvetica,Arial,sans-serif" font-size="13" font-weight="600">'
          f'{esc(r["generation"])}</text>')
        a(f'  <text x="{x}" y="{H - PAD_B + 42}" fill="#64748b" text-anchor="middle" '
          f'font-family="Helvetica,Arial,sans-serif" font-size="11">'
          f'{r["avg_files"]:.1f} files</text>')

    a(f'  <text x="{PAD_L + plot_w / 2}" y="{H - 24}" fill="#94a3b8" text-anchor="middle" '
      f'font-family="Helvetica,Arial,sans-serif" font-size="13" font-weight="600">'
      f'Generation (cohort mean physical files authored on disk)</text>')
    a(f'  <text x="26" y="{PAD_T + plot_h / 2}" fill="#94a3b8" text-anchor="middle" '
      f'font-family="Helvetica,Arial,sans-serif" font-size="13" font-weight="600" '
      f'transform="rotate(-90 26 {PAD_T + plot_h / 2})">Champion Net Fitness</text>')

    # ---- legend ----
    lx = W - PAD_R + 26
    ly = PAD_T + 8
    a(f'  <text x="{lx}" y="{ly}" fill="#f8fafc" font-family="Helvetica,Arial,sans-serif" '
      f'font-size="13" font-weight="700">Tracks</text>')
    a(f'  <line x1="{lx}" y1="{ly + 20}" x2="{lx + 30}" y2="{ly + 20}" stroke="#94a3b8" '
      f'stroke-width="3" stroke-dasharray="7,5" />')
    a(f'  <text x="{lx + 38}" y="{ly + 24}" fill="#cbd5e1" '
      f'font-family="Helvetica,Arial,sans-serif" font-size="12">Legacy (unchecked)</text>')
    a(f'  <line x1="{lx}" y1="{ly + 44}" x2="{lx + 30}" y2="{ly + 44}" stroke="url(#groundedGrad)" '
      f'stroke-width="4" />')
    a(f'  <text x="{lx + 38}" y="{ly + 48}" fill="#cbd5e1" '
      f'font-family="Helvetica,Arial,sans-serif" font-size="12">Grounded (standardized)</text>')

    champ = rows[best]
    a(f'  <text x="{lx}" y="{ly + 88}" fill="#fbbf24" font-family="Helvetica,Arial,sans-serif" '
      f'font-size="13" font-weight="700">Peak: {esc(champ["generation"])}</text>')
    a(f'  <text x="{lx}" y="{ly + 108}" fill="#94a3b8" font-family="Helvetica,Arial,sans-serif" '
      f'font-size="11">{champ["corrected_champion"]:.2f} net</text>')
    a(f'  <text x="{lx}" y="{ly + 124}" fill="#94a3b8" font-family="Helvetica,Arial,sans-serif" '
      f'font-size="11">{champ["max_files"]} files (peak firm)</text>')

    # ---- validity caveat ----
    a(f'  <text x="{PAD_L}" y="{H - 6}" fill="#64748b" '
      f'font-family="Helvetica,Arial,sans-serif" font-size="10.5">'
      f'Caveat: of four verification gates, only Tests physically executes code. '
      f'Build / Smoke / Telemetry are heuristic proxies pending the execution harness.</text>')

    a("</svg>")
    return "\n".join(p)


def main():
    rows = load_generations()
    svg = build_svg(rows)

    os.makedirs(os.path.dirname(OUT_SVG), exist_ok=True)

    # Fail loudly rather than emitting a corrupt asset.
    root = ET.fromstring(svg)
    print(f"[SUCCESS] XML is 100% VALID! Root tag: {root.tag}")

    with open(OUT_SVG, "w") as fh:
        fh.write(svg)
    print(f"[SUCCESS] Saved SVG to {OUT_SVG} ({len(rows)} generations)")

    # Rasterizer preference matters here: ImageMagick's built-in SVG renderer
    # silently drops gradient-stroked polylines and gridlines, yielding a chart
    # of disconnected dots. Headless Chrome uses a real SVG engine.
    abs_svg = os.path.abspath(OUT_SVG)
    abs_png = os.path.abspath(OUT_PNG)
    candidates = [
        ["google-chrome", "--headless", "--disable-gpu", "--no-sandbox",
         "--hide-scrollbars", "--force-device-scale-factor=1.6",
         f"--window-size={W},{H}", f"--screenshot={abs_png}", f"file://{abs_svg}"],
        ["chromium", "--headless", "--disable-gpu", "--no-sandbox",
         "--hide-scrollbars", "--force-device-scale-factor=1.6",
         f"--window-size={W},{H}", f"--screenshot={abs_png}", f"file://{abs_svg}"],
        ["rsvg-convert", "-w", str(W * 2), abs_svg, "-o", abs_png],
        ["inkscape", abs_svg, "--export-type=png",
         f"--export-filename={abs_png}", "-w", str(W * 2)],
        ["convert", "-density", "150", abs_svg, abs_png],
    ]
    for cmd in candidates:
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue
        if not os.path.exists(abs_png):
            continue
        if cmd[0] == "convert":
            print(f"[WARN] Rendered PNG via ImageMagick; gradient strokes may be missing.")
        else:
            print(f"[SUCCESS] Rendered PNG to {OUT_PNG} via {cmd[0]}")
        return
    print(f"[WARN] No SVG rasterizer available; {OUT_SVG} written without a PNG companion.")


if __name__ == "__main__":
    main()
