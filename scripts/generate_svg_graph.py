"""Generate publication-grade SVG chart and high-res PNG for Generational Fitness Trajectory (Gen 0 to Gen 4)."""

import os
import xml.etree.ElementTree as ET
import subprocess

SVG_CONTENT = '''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 560" width="960" height="560">
  <defs>
    <!-- Background Gradients -->
    <linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0b0f19" />
      <stop offset="100%" stop-color="#111827" />
    </linearGradient>
    
    <!-- Track A Gradients (Cyan to Sky) -->
    <linearGradient id="trackAGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#10b981" />
      <stop offset="35%" stop-color="#06b6d4" />
      <stop offset="100%" stop-color="#38bdf8" />
    </linearGradient>

    <!-- Track B Gradients (Fuchsia to Emerald to Cyan to Violet) -->
    <linearGradient id="trackBGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#ec4899" />
      <stop offset="30%" stop-color="#f59e0b" />
      <stop offset="60%" stop-color="#10b981" />
      <stop offset="85%" stop-color="#06b6d4" />
      <stop offset="100%" stop-color="#8b5cf6" />
    </linearGradient>
    <linearGradient id="trackBArea" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#8b5cf6" stop-opacity="0.25" />
      <stop offset="30%" stop-color="#06b6d4" stop-opacity="0.20" />
      <stop offset="60%" stop-color="#10b981" stop-opacity="0.15" />
      <stop offset="85%" stop-color="#ec4899" stop-opacity="0.10" />
      <stop offset="100%" stop-color="#0b0f19" stop-opacity="0.0" />
    </linearGradient>

    <!-- Card Gradients -->
    <linearGradient id="cardGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#1e293b" stop-opacity="0.85" />
      <stop offset="100%" stop-color="#0f172a" stop-opacity="0.95" />
    </linearGradient>
  </defs>

  <!-- Container Box -->
  <rect x="2" y="2" width="956" height="556" rx="16" fill="url(#bgGrad)" stroke="#334155" stroke-width="1.5" />

  <!-- Header -->
  <g transform="translate(48, 40)">
    <text x="0" y="0" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="20" font-weight="700" fill="#f8fafc" letter-spacing="-0.02em">
      Generational Fitness Trajectory &amp; Sandbox Convergence
    </text>
    <text x="0" y="22" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="12" font-weight="400" fill="#94a3b8">
      Empirical convergence between unconstrained reasoning, grounded sandbox execution, and OpEx unit economics across 5 generations
    </text>
  </g>

  <!-- Legend -->
  <g transform="translate(560, 34)">
    <!-- Track A Legend -->
    <line x1="0" y1="8" x2="24" y2="8" stroke="url(#trackAGrad)" stroke-width="2.5" stroke-dasharray="4 2" />
    <circle cx="12" cy="8" r="3.5" fill="#38bdf8" />
    <text x="32" y="12" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="11" font-weight="500" fill="#cbd5e1">
      Raw Semantic Frontier (Pre-Penalty)
    </text>

    <!-- Track B Legend -->
    <line x1="0" y1="28" x2="24" y2="28" stroke="url(#trackBGrad)" stroke-width="3.5" stroke-linecap="round" />
    <circle cx="12" cy="28" r="4.5" fill="#8b5cf6" />
    <text x="32" y="32" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="11" font-weight="600" fill="#f8fafc">
      Grounded Sandbox Score (Final Benchmark)
    </text>
  </g>

  <!-- Chart Grid Area (x: 60 to 880, y: 105 to 385) -->
  <g transform="translate(60, 105)">
    <!-- Horizontal Grid Lines & Y-Labels (280px total height, 7px per pt) -->
    <!-- 100 pts -->
    <line x1="0" y1="0" x2="820" y2="0" stroke="#1e293b" stroke-width="1" stroke-dasharray="4 4" />
    <text x="-14" y="4" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="11" font-weight="500" fill="#64748b" text-anchor="end">100</text>

    <!-- 90 pts (y=70) -->
    <line x1="0" y1="70" x2="820" y2="70" stroke="#1e293b" stroke-width="1" stroke-dasharray="4 4" />
    <text x="-14" y="74" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="11" font-weight="500" fill="#64748b" text-anchor="end">90</text>

    <!-- 80 pts (y=140) -->
    <line x1="0" y1="140" x2="820" y2="140" stroke="#1e293b" stroke-width="1" stroke-dasharray="4 4" />
    <text x="-14" y="144" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="11" font-weight="500" fill="#64748b" text-anchor="end">80</text>

    <!-- 70 pts (y=210) -->
    <line x1="0" y1="210" x2="820" y2="210" stroke="#1e293b" stroke-width="1" stroke-dasharray="4 4" />
    <text x="-14" y="214" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="11" font-weight="500" fill="#64748b" text-anchor="end">70</text>

    <!-- 60 pts (y=280) -->
    <line x1="0" y1="280" x2="820" y2="280" stroke="#1e293b" stroke-width="1" stroke-dasharray="4 4" />
    <text x="-14" y="284" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="11" font-weight="500" fill="#64748b" text-anchor="end">60</text>

    <!-- Vertical Milestone Markers -->
    <!-- Milestone 1: Gen 0 Baseline (x=40) -->
    <line x1="40" y1="0" x2="40" y2="280" stroke="#1e293b" stroke-width="1" />
    <text x="40" y="298" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="10" font-weight="600" fill="#cbd5e1" text-anchor="middle">Gen 0 Baseline</text>
    <text x="40" y="312" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="8.5" font-weight="400" fill="#64748b" text-anchor="middle">Seed (31)</text>

    <!-- Milestone 2: Gen 0 Parallel Tournament (x=180) -->
    <line x1="180" y1="0" x2="180" y2="280" stroke="#1e293b" stroke-width="1" />
    <text x="180" y="298" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="10" font-weight="600" fill="#cbd5e1" text-anchor="middle">Gen 0 Tourn.</text>
    <text x="180" y="312" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="8.5" font-weight="400" fill="#64748b" text-anchor="middle">10 Firms (310)</text>

    <!-- Milestone 3: Gen 1 Parallel Tournament (x=330) -->
    <line x1="330" y1="0" x2="330" y2="280" stroke="#1e293b" stroke-width="1" />
    <text x="330" y="298" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="10" font-weight="600" fill="#cbd5e1" text-anchor="middle">Gen 1 Evolved</text>
    <text x="330" y="312" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="8.5" font-weight="400" fill="#64748b" text-anchor="middle">Breeding (312)</text>

    <!-- Milestone 4: Gen 2 Persona Discretization (x=480) -->
    <line x1="480" y1="0" x2="480" y2="280" stroke="#10b981" stroke-width="1" stroke-dasharray="3 3" opacity="0.6" />
    <text x="480" y="298" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="10" font-weight="600" fill="#34d399" text-anchor="middle">Gen 2 Breakthrough</text>
    <text x="480" y="312" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="8.5" font-weight="400" fill="#10b981" text-anchor="middle">Trait Alleles (314)</text>

    <!-- Milestone 5: Gen 3 Consensus Peak (x=630) -->
    <line x1="630" y1="0" x2="630" y2="280" stroke="#06b6d4" stroke-width="1" stroke-dasharray="3 3" opacity="0.8" />
    <text x="630" y="298" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="10" font-weight="600" fill="#38bdf8" text-anchor="middle">Gen 3 Consensus</text>
    <text x="630" y="312" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="8.5" font-weight="400" fill="#06b6d4" text-anchor="middle">Consensus Mining (318)</text>

    <!-- Milestone 6: Gen 4 OpEx & Autonomous Sizing (x=770) -->
    <line x1="770" y1="0" x2="770" y2="280" stroke="#8b5cf6" stroke-width="1.5" stroke-dasharray="3 3" opacity="0.9" />
    <text x="770" y="298" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="10" font-weight="700" fill="#a78bfa" text-anchor="middle">Gen 4 OpEx Frontier</text>
    <text x="770" y="312" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="8.5" font-weight="500" fill="#c4b5fd" text-anchor="middle">Autonomous Sizing (320)</text>

    <!-- Penalty Disconnect Shading between x=180 and x=330 -->
    <path d="M 180 26.25 L 330 19.6 L 330 164.15 L 180 157.5 Z" fill="#e11d48" fill-opacity="0.10" />

    <!-- Track A: Raw Semantic Frontier (Dashed line) -->
    <path d="M 40 49 C 120 32, 150 27, 180 26.25 C 260 24, 300 20, 330 19.6 C 410 19, 450 35, 480 38.5 C 550 38, 590 23, 630 22.75 C 700 22.75, 740 22.75, 770 22.75" 
          fill="none" stroke="url(#trackAGrad)" stroke-width="2.5" stroke-dasharray="5 3" />

    <circle cx="40" cy="49" r="4" fill="#10b981" stroke="#0f172a" stroke-width="1.5" />
    <circle cx="180" cy="26.25" r="4" fill="#06b6d4" stroke="#0f172a" stroke-width="1.5" />
    <circle cx="330" cy="19.6" r="4" fill="#38bdf8" stroke="#0f172a" stroke-width="1.5" />

    <!-- Track B: Grounded Sandbox Execution Area & Curve -->
    <polygon points="180,157.5 330,164.15 480,38.5 630,22.75 770,22.75 770,280 180,280" fill="url(#trackBArea)" />
    <path d="M 180 157.5 C 250 160, 290 165, 330 164.15 C 410 162, 440 60, 480 38.5 C 540 25, 590 23, 630 22.75 C 690 22.75, 730 22.75, 770 22.75" 
          fill="none" stroke="url(#trackBGrad)" stroke-width="4" stroke-linecap="round" />

    <!-- Gen 0 Parallel Spread (x=180): Champion 77.50 -->
    <line x1="180" y1="157.5" x2="180" y2="200.4" stroke="#ec4899" stroke-width="2.5" stroke-linecap="round" />
    <circle cx="180" cy="200.4" r="3" fill="#ec4899" />
    <circle cx="180" cy="157.5" r="4.5" fill="#ec4899" stroke="#0f172a" stroke-width="2" />
    <rect x="115" y="146" width="60" height="18" rx="4" fill="#701a75" stroke="#c026d3" stroke-width="1" />
    <text x="145" y="159" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="8.5" font-weight="700" fill="#fdf4ff" text-anchor="middle">77.50 [G0]</text>

    <!-- Gen 1 Parallel Spread (x=330): Champion 76.55 -->
    <line x1="330" y1="164.15" x2="330" y2="212.8" stroke="#f59e0b" stroke-width="2.5" stroke-linecap="round" />
    <circle cx="330" cy="212.8" r="3" fill="#f59e0b" />
    <circle cx="330" cy="164.15" r="4.5" fill="#f59e0b" stroke="#0f172a" stroke-width="2" />
    <rect x="338" y="153" width="60" height="18" rx="4" fill="#78350f" stroke="#d97706" stroke-width="1" />
    <text x="368" y="166" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="8.5" font-weight="700" fill="#fef3c7" text-anchor="middle">76.55 [G1]</text>

    <!-- Gen 2 Breakthrough (x=480): Champion 94.50 -->
    <line x1="480" y1="38.5" x2="480" y2="129.36" stroke="#10b981" stroke-width="2.5" stroke-linecap="round" />
    <circle cx="480" cy="129.36" r="3" fill="#10b981" />
    <circle cx="480" cy="38.5" r="5" fill="#10b981" stroke="#0f172a" stroke-width="2" />
    <rect x="420" y="28" width="55" height="18" rx="4" fill="#064e3b" stroke="#10b981" stroke-width="1" />
    <text x="447" y="41" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="8.5" font-weight="700" fill="#a7f3d0" text-anchor="middle">94.50 [G2]</text>

    <!-- Gen 3 Consensus (x=630): Champion 96.75 -->
    <line x1="630" y1="22.75" x2="630" y2="94.71" stroke="#06b6d4" stroke-width="2.5" stroke-linecap="round" />
    <circle cx="630" cy="94.71" r="3" fill="#06b6d4" />
    <circle cx="630" cy="22.75" r="5" fill="#06b6d4" stroke="#0f172a" stroke-width="2" />
    <rect x="575" y="12" width="50" height="18" rx="4" fill="#083344" stroke="#06b6d4" stroke-width="1" />
    <text x="600" y="25" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="8.5" font-weight="700" fill="#cffafe" text-anchor="middle">96.75 [G3]</text>

    <!-- Gen 4 OpEx Frontier (x=770): Champion 96.75 + 10 Files + $0.0895 -->
    <line x1="770" y1="22.75" x2="770" y2="108.5" stroke="#8b5cf6" stroke-width="2.5" stroke-linecap="round" />
    <circle cx="770" cy="108.5" r="3.5" fill="#8b5cf6" />
    <circle cx="770" cy="22.75" r="7.5" fill="#8b5cf6" stroke="#ffffff" stroke-width="2.5" />
    <rect x="690" y="-14" width="160" height="26" rx="6" fill="#2e1065" stroke="#8b5cf6" stroke-width="1.5" />
    <text x="770" y="3" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="11.5" font-weight="800" fill="#ede9fe" text-anchor="middle">96.75 [10 Files, $0.0895]</text>

    <!-- Gen 4 Badge -->
    <rect x="695" y="44" width="150" height="28" rx="5" fill="#2e1065" stroke="#7c3aed" stroke-width="1" opacity="0.95" />
    <text x="770" y="56" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="9" font-weight="700" fill="#ddd6fe" text-anchor="middle">AUTONOMOUS SIZING</text>
    <text x="770" y="67" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="8" font-weight="500" fill="#c4b5fd" text-anchor="middle">Tiered Pro/Flash OpEx</text>
  </g>

  <!-- Metric Highlights Strip (Bottom 4 Cards) -->
  <g transform="translate(48, 432)">
    <!-- Card 1 -->
    <g transform="translate(0, 0)">
      <rect width="200" height="85" rx="10" fill="url(#cardGrad)" stroke="#334155" stroke-width="1" />
      <text x="16" y="26" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="10" font-weight="600" fill="#94a3b8" letter-spacing="0.05em">ALL-TIME RECORD</text>
      <text x="16" y="56" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="22" font-weight="800" fill="#38bdf8">96.75 pts</text>
      <text x="16" y="74" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="10.5" font-weight="500" fill="#64748b">Gen 3 &amp; Gen 4 Champions</text>
    </g>

    <!-- Card 2 -->
    <g transform="translate(220, 0)">
      <rect width="200" height="85" rx="10" fill="url(#cardGrad)" stroke="#334155" stroke-width="1" />
      <text x="16" y="26" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="10" font-weight="600" fill="#94a3b8" letter-spacing="0.05em">PACKAGE COMPLETENESS</text>
      <text x="16" y="56" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="22" font-weight="800" fill="#8b5cf6">10 Files Emitted</text>
      <text x="16" y="74" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="10.5" font-weight="500" fill="#64748b">Complete Python Library</text>
    </g>

    <!-- Card 3 -->
    <g transform="translate(440, 0)">
      <rect width="200" height="85" rx="10" fill="url(#cardGrad)" stroke="#334155" stroke-width="1" />
      <text x="16" y="26" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="10" font-weight="600" fill="#94a3b8" letter-spacing="0.05em">OPEX UNIT ECONOMICS</text>
      <text x="16" y="56" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="22" font-weight="800" fill="#10b981">$0.0895 / firm</text>
      <text x="16" y="74" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="10.5" font-weight="500" fill="#64748b">vs $0.45 Budget Envelope</text>
    </g>

    <!-- Card 4 -->
    <g transform="translate(660, 0)">
      <rect width="200" height="85" rx="10" fill="url(#cardGrad)" stroke="#334155" stroke-width="1" />
      <text x="16" y="26" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="10" font-weight="600" fill="#94a3b8" letter-spacing="0.05em">DETERMINISTIC GATES</text>
      <text x="16" y="56" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="22" font-weight="800" fill="#34d399">0.00 Penalty</text>
      <text x="16" y="74" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="10.5" font-weight="500" fill="#64748b">100% Pass Across 4 Gates</text>
    </g>
  </g>
</svg>
'''

target_svg = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "experiments", "assets", "fitness_trajectory.svg")
target_png = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "experiments", "assets", "fitness_trajectory.png")
os.makedirs(os.path.dirname(target_svg), exist_ok=True)

with open(target_svg, "w", encoding="utf-8") as f:
    f.write(SVG_CONTENT.strip() + "\n")

# Validate XML strictly
tree = ET.fromstring(SVG_CONTENT.strip())
print("[SUCCESS] XML is 100% VALID! Root tag:", tree.tag)

# Render to high-res PNG via headless chrome
html_wrapper = f"""<!DOCTYPE html>
<html>
<head><style>body {{ margin: 0; padding: 0; background: #0b0f19; overflow: hidden; }}</style></head>
<body>
{SVG_CONTENT}
</body>
</html>"""
temp_html = "/tmp/render_chart.html"
with open(temp_html, "w") as f:
    f.write(html_wrapper)

cmd = [
    "/usr/bin/google-chrome",
    "--headless",
    "--disable-gpu",
    "--hide-scrollbars",
    f"--screenshot={target_png}",
    "--window-size=960,560",
    temp_html
]
subprocess.run(cmd, check=True)
print(f"[SUCCESS] Rendered PNG to {target_png}")
