"""Generates the publication-grade V2 & V3 fitness trajectory chart (SVG + PNG).

Reads all harvested scorecards from:
  - experiments/v2/generation_{1..6}_results/
  - experiments/v3_rsi/generation_{7..9}_results/

Produces a dual-series publication chart showing:
  1. Champion Peak Net Fitness (0-100)
  2. Cohort Median Net Fitness (0-100)
  3. Cohort Mean Ground-Truth Verification Score (%)
  4. First-Shot (Iteration 1) 100% Convergence Rate (%)
"""

import glob
import json
import os
import statistics
import subprocess
import xml.etree.ElementTree as ET

OUT_SVG = "experiments/v2/assets/v2_v3_evolutionary_trajectory.svg"
OUT_PNG = "experiments/v2/assets/v2_v3_evolutionary_trajectory.png"
ROOT_PNG = "assets/v2_v3_evolutionary_trajectory.png"
ARTIFACT_PNG = "/usr/local/google/home/chavoshi/.gemini/jetski/brain/53e6db8b-5a77-4488-b191-f70835cc8c31/v2_v3_evolutionary_trajectory.png"

W, H = 1260, 720
PAD_L, PAD_R, PAD_T, PAD_B = 92, 270, 142, 128


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def collect_generation_stats():
    configs = [
        (1, "experiments/v2/generation_1_results", "V2 Single-Pass", "artifacts.py"),
        (2, "experiments/v2/generation_2_results", "V2 Single-Pass", "artifacts.py"),
        (3, "experiments/v2/generation_3_results", "V2 Single-Pass", "artifacts.py"),
        (4, "experiments/v2/generation_4_results", "V2 Iterative (Iter=10)", "artifacts.py"),
        (5, "experiments/v2/generation_5_results", "V2 Iterative (Iter=10)", "artifacts.py"),
        (6, "experiments/v2/generation_6_results", "V2 Iterative (Iter=10)", "artifacts.py"),
        (7, "experiments/v3_rsi/generation_7_results", "V3 Level-3 RSI Seed", "morphogenesis.py"),
        (8, "experiments/v3_rsi/generation_8_results", "V3 Closed-Loop #1", "morphogenesis.py"),
        (9, "experiments/v3_rsi/generation_9_results", "V3 Closed-Loop #2", "morphogenesis.py"),
    ]
    rows = []
    for gen, folder, phase, module in configs:
        files = sorted(glob.glob(os.path.join(folder, "*_result.json")))
        if not files:
            continue
        nets, vers, iters, costs = [], [], [], []
        best_cid = ""
        best_net = -1.0
        for f in files:
            with open(f) as fh:
                d = json.load(fh)
            net = float(d.get("fitness_score", 0.0))
            ver = float(d.get("verification", {}).get("score", 0.0))
            it = int(d.get("iterations_used", 1))
            cost = float(d.get("opex", {}).get("estimated_cost_usd", 0.0))
            nets.append(net)
            vers.append(ver)
            iters.append(it)
            costs.append(cost)
            if net > best_net:
                best_net = net
                best_cid = d.get("company_id", "")
        iter1_rate = 100.0 * sum(1 for v, i in zip(vers, iters) if v >= 100.0 and i == 1) / len(files)
        pass_rate = 100.0 * sum(1 for v in vers if v >= 100.0) / len(files)
        rows.append({
            "gen": gen,
            "label": f"Gen {gen}",
            "phase": phase,
            "module": module,
            "peak_net": round(max(nets), 2),
            "median_net": round(statistics.median(nets), 2),
            "mean_net": round(statistics.mean(nets), 2),
            "mean_ver": round(statistics.mean(vers), 1),
            "pass_rate": round(pass_rate, 1),
            "iter1_rate": round(iter1_rate, 1),
            "mean_iters": round(statistics.mean(iters), 2),
            "total_cost": round(sum(costs), 2),
            "champion": best_cid,
        })
    return rows


def build_svg(rows):
    n = len(rows)
    plot_w = W - PAD_L - PAD_R
    plot_h = H - PAD_T - PAD_B

    def x_of(i):
        if n <= 1:
            return PAD_L + plot_w / 2.0
        return round(PAD_L + plot_w * i / (n - 1), 1)

    def y_of(v):
        v_clamped = max(0.0, min(105.0, float(v)))
        return round(PAD_T + plot_h * (1.0 - v_clamped / 105.0), 1)

    parts = []
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">')
    parts.append(f'<rect width="{W}" height="{H}" fill="#0d1117"/>')

    # Regime background bands
    def band_x(idx_left, idx_right):
        x0 = PAD_L if idx_left == 0 else (x_of(idx_left - 1) + x_of(idx_left)) / 2.0
        x1 = (PAD_L + plot_w) if idx_right >= n - 1 else (x_of(idx_right) + x_of(idx_right + 1)) / 2.0
        return x0, x1 - x0

    # Band 1: Gen 1-3 (V2 Single-Pass)
    bx, bw = band_x(0, min(2, n - 1))
    parts.append(f'<rect x="{bx:.1f}" y="{PAD_T}" width="{bw:.1f}" height="{plot_h}" fill="#1f6feb" fill-opacity="0.08"/>')
    parts.append(f'<rect x="{bx + 6:.1f}" y="{PAD_T - 44}" width="{bw - 12:.1f}" height="36" rx="6" fill="#161b22" stroke="#1f6feb" stroke-width="1.2"/>')
    parts.append(f'<text x="{bx + bw/2:.1f}" y="{PAD_T - 28}" fill="#79c0ff" font-family="sans-serif" font-size="11" font-weight="bold" text-anchor="middle">PHASE 1: V2 SINGLE-PASS (iter=1)</text>')
    parts.append(f'<text x="{bx + bw/2:.1f}" y="{PAD_T - 14}" fill="#8b949e" font-family="sans-serif" font-size="10" text-anchor="middle">Target: hae/evaluation/artifacts.py</text>')

    # Band 2: Gen 4-6 (V2 Iterative Self-Repair)
    if n > 3:
        bx, bw = band_x(3, min(5, n - 1))
        parts.append(f'<rect x="{bx:.1f}" y="{PAD_T}" width="{bw:.1f}" height="{plot_h}" fill="#238636" fill-opacity="0.09"/>')
        parts.append(f'<line x1="{bx:.1f}" y1="{PAD_T - 44}" x2="{bx:.1f}" y2="{PAD_T + plot_h}" stroke="#3fb950" stroke-dasharray="5,5" stroke-width="1.5"/>')
        parts.append(f'<rect x="{bx + 6:.1f}" y="{PAD_T - 44}" width="{bw - 12:.1f}" height="36" rx="6" fill="#161b22" stroke="#238636" stroke-width="1.2"/>')
        parts.append(f'<text x="{bx + bw/2:.1f}" y="{PAD_T - 28}" fill="#56d364" font-family="sans-serif" font-size="11" font-weight="bold" text-anchor="middle">PHASE 2: V2 SELF-REPAIR (iter=10)</text>')
        parts.append(f'<text x="{bx + bw/2:.1f}" y="{PAD_T - 14}" fill="#8b949e" font-family="sans-serif" font-size="10" text-anchor="middle">100% Execution Integrity Achieved</text>')

    # Band 3: Gen 7+ (V3 Level-3 Closed-Loop RSI)
    if n > 6:
        bx, bw = band_x(6, n - 1)
        parts.append(f'<rect x="{bx:.1f}" y="{PAD_T}" width="{bw:.1f}" height="{plot_h}" fill="#a371f7" fill-opacity="0.12"/>')
        parts.append(f'<line x1="{bx:.1f}" y1="{PAD_T - 44}" x2="{bx:.1f}" y2="{PAD_T + plot_h}" stroke="#d2a8ff" stroke-dasharray="5,5" stroke-width="1.5"/>')
        parts.append(f'<rect x="{bx + 6:.1f}" y="{PAD_T - 44}" width="{bw - 12:.1f}" height="36" rx="6" fill="#161b22" stroke="#a371f7" stroke-width="1.2"/>')
        parts.append(f'<text x="{bx + bw/2:.1f}" y="{PAD_T - 28}" fill="#d2a8ff" font-family="sans-serif" font-size="11" font-weight="bold" text-anchor="middle">PHASE 3: V3 CLOSED-LOOP RSI</text>')
        parts.append(f'<text x="{bx + bw/2:.1f}" y="{PAD_T - 14}" fill="#8b949e" font-family="sans-serif" font-size="10" text-anchor="middle">morphogenesis.py + Overlays</text>')

    # Title & Subtitle
    parts.append(f'<text x="{PAD_L}" y="42" fill="#f0f6fc" font-family="sans-serif" font-size="20" font-weight="bold">Hierarchical Agent Evolution — V2 &amp; V3 Ground-Truth Self-Hosting &amp; Closed-Loop RSI Trajectory</text>')
    parts.append(f'<text x="{PAD_L}" y="68" fill="#8b949e" font-family="sans-serif" font-size="13">Measured against held-out test suites in network-isolated sandboxes (unshare -rn) across {n} generations ({n*10} autonomous firms)</text>')

    # Horizontal Gridlines
    for tick in [0, 20, 40, 60, 80, 100]:
        yy = y_of(tick)
        parts.append(f'<line x1="{PAD_L}" y1="{yy}" x2="{PAD_L + plot_w}" y2="{yy}" stroke="#30363d" stroke-width="1" stroke-dasharray="3,3"/>')
        parts.append(f'<text x="{PAD_L - 12}" y="{yy + 4}" fill="#8b949e" font-family="sans-serif" font-size="11" text-anchor="end">{tick}</text>')

    # Axes
    parts.append(f'<line x1="{PAD_L}" y1="{PAD_T}" x2="{PAD_L}" y2="{PAD_T + plot_h}" stroke="#8b949e" stroke-width="1.5"/>')
    parts.append(f'<line x1="{PAD_L}" y1="{PAD_T + plot_h}" x2="{PAD_L + plot_w}" y2="{PAD_T + plot_h}" stroke="#8b949e" stroke-width="1.5"/>')

    # Series definitions
    series_cfg = [
        ("mean_ver", "#3fb950", "4,2", 2.6, "Ground-Truth Verification Mean (%)"),
        ("peak_net", "#58a6ff", None, 3.2, "Champion Net Fitness (Peak)"),
        ("median_net", "#d2a8ff", None, 2.5, "Population Median Net Fitness"),
        ("iter1_rate", "#f0883e", "6,4", 2.4, "Iteration-1 100% Convergence (%)"),
    ]

    for key, color, dash, width, _ in series_cfg:
        pts = " ".join(f"{x_of(i)},{y_of(r[key])}" for i, r in enumerate(rows))
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="{width}"{dash_attr} points="{pts}"/>')

    # Points & Labels
    for i, r in enumerate(rows):
        xx = x_of(i)
        # X-axis labels
        parts.append(f'<text x="{xx}" y="{PAD_T + plot_h + 24}" fill="#f0f6fc" font-family="sans-serif" font-size="12" font-weight="bold" text-anchor="middle">{esc(r["label"])}</text>')
        parts.append(f'<text x="{xx}" y="{PAD_T + plot_h + 42}" fill="#8b949e" font-family="sans-serif" font-size="10" text-anchor="middle">{esc(r["module"])}</text>')
        parts.append(f'<text x="{xx}" y="{PAD_T + plot_h + 57}" fill="#58a6ff" font-family="sans-serif" font-size="10" text-anchor="middle">iters: {r["mean_iters"]:.2f}</text>')
        parts.append(f'<text x="{xx}" y="{PAD_T + plot_h + 72}" fill="#8b949e" font-family="sans-serif" font-size="10" text-anchor="middle">${r["total_cost"]:.2f}</text>')

        # Peak Net circle + label
        ypk = y_of(r["peak_net"])
        parts.append(f'<circle cx="{xx}" cy="{ypk}" r="5.5" fill="#58a6ff" stroke="#0d1117" stroke-width="1.5"/>')
        parts.append(f'<text x="{xx}" y="{ypk - 10}" fill="#58a6ff" font-family="sans-serif" font-size="11" font-weight="bold" text-anchor="middle">{r["peak_net"]:.2f}</text>')

        # Median Net square + label
        ymd = y_of(r["median_net"])
        parts.append(f'<rect x="{xx - 4}" y="{ymd - 4}" width="8" height="8" fill="#d2a8ff" stroke="#0d1117" stroke-width="1.2"/>')
        offset_md = 16 if abs(ymd - ypk) < 18 else -9
        parts.append(f'<text x="{xx}" y="{ymd + offset_md}" fill="#d2a8ff" font-family="sans-serif" font-size="10" font-weight="bold" text-anchor="middle">{r["median_net"]:.1f}</text>')

        # Mean verification diamond
        yvr = y_of(r["mean_ver"])
        parts.append(f'<circle cx="{xx}" cy="{yvr}" r="4.5" fill="#3fb950" stroke="#0d1117" stroke-width="1.2"/>')

        # Iteration-1 convergence triangle + label
        yit = y_of(r["iter1_rate"])
        parts.append(f'<circle cx="{xx}" cy="{yit}" r="4.5" fill="#f0883e" stroke="#0d1117" stroke-width="1.2"/>')
        parts.append(f'<text x="{xx}" y="{yit + 15}" fill="#ffa657" font-family="sans-serif" font-size="10" font-weight="bold" text-anchor="middle">{r["iter1_rate"]:.0f}%</text>')

    # Legend on the right
    lx = PAD_L + plot_w + 22
    ly = PAD_T + 20
    parts.append(f'<rect x="{lx - 10}" y="{ly - 18}" width="238" height="210" rx="8" fill="#161b22" stroke="#30363d" stroke-width="1.2"/>')
    parts.append(f'<text x="{lx + 4}" y="{ly + 4}" fill="#f0f6fc" font-family="sans-serif" font-size="12" font-weight="bold">Trajectory Metrics</text>')

    for idx, (_, color, dash, _, label) in enumerate(series_cfg):
        cy = ly + 32 + idx * 36
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        parts.append(f'<line x1="{lx + 4}" y1="{cy}" x2="{lx + 34}" y2="{cy}" stroke="{color}" stroke-width="3"{dash_attr}/>')
        parts.append(f'<circle cx="{lx + 19}" cy="{cy}" r="4.5" fill="{color}"/>')
        parts.append(f'<text x="{lx + 44}" y="{cy + 4}" fill="#c9d1d9" font-family="sans-serif" font-size="11">{esc(label)}</text>')

    parts.append(f'<text x="{lx + 4}" y="{ly + 178}" fill="#8b949e" font-family="sans-serif" font-size="10">Bottom labels show module,</text>')
    parts.append(f'<text x="{lx + 4}" y="{ly + 192}" fill="#8b949e" font-family="sans-serif" font-size="10">mean iterations &amp; cohort spend.</text>')

    parts.append("</svg>")
    return "\n".join(parts)


def main():
    rows = collect_generation_stats()
    svg = build_svg(rows)
    os.makedirs(os.path.dirname(OUT_SVG), exist_ok=True)
    os.makedirs(os.path.dirname(ROOT_PNG), exist_ok=True)
    ET.fromstring(svg)
    with open(OUT_SVG, "w") as fh:
        fh.write(svg)
    print(f"[SUCCESS] Wrote SVG to {OUT_SVG} ({len(rows)} generations)")

    abs_svg = os.path.abspath(OUT_SVG)
    for out_png in [OUT_PNG, ROOT_PNG, ARTIFACT_PNG]:
        abs_png = os.path.abspath(out_png)
        cmds = [
            ["google-chrome", "--headless", "--disable-gpu", "--no-sandbox",
             "--hide-scrollbars", "--force-device-scale-factor=1.6",
             f"--window-size={W},{H}", f"--screenshot={abs_png}", f"file://{abs_svg}"],
            ["chromium", "--headless", "--disable-gpu", "--no-sandbox",
             "--hide-scrollbars", "--force-device-scale-factor=1.6",
             f"--window-size={W},{H}", f"--screenshot={abs_png}", f"file://{abs_svg}"],
            ["convert", "-density", "150", abs_svg, abs_png],
        ]
        for cmd in cmds:
            try:
                subprocess.run(cmd, check=True, capture_output=True, timeout=60)
                if os.path.exists(abs_png):
                    print(f"[SUCCESS] Rendered {out_png} via {cmd[0]}")
                    break
            except Exception:
                continue


if __name__ == "__main__":
    main()
