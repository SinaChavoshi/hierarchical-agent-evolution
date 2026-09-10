"""Generate publication-grade SVG chart and high-res PNG for Generational Fitness Trajectory (Gen 0 to Gen 6)."""

import os
import xml.etree.ElementTree as ET
import subprocess

SVG_CONTENT = '''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1060 590" width="1060" height="590">
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

    <!-- Track B Gradients (Fuchsia to Emerald to Cyan to Violet to Rose to Amber) -->
    <linearGradient id="trackBGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#ec4899" />
      <stop offset="20%" stop-color="#f59e0b" />
      <stop offset="40%" stop-color="#10b981" />
      <stop offset="60%" stop-color="#06b6d4" />
      <stop offset="75%" stop-color="#8b5cf6" />
      <stop offset="90%" stop-color="#f43f5e" />
      <stop offset="100%" stop-color="#3b82f6" />
    </linearGradient>
    <linearGradient id="trackBArea" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#3b82f6" stop-opacity="0.25" />
      <stop offset="25%" stop-color="#f43f5e" stop-opacity="0.20" />
      <stop offset="50%" stop-color="#8b5cf6" stop-opacity="0.15" />
      <stop offset="75%" stop-color="#10b981" stop-opacity="0.10" />
      <stop offset="100%" stop-color="#0b0f19" stop-opacity="0.0" />
    </linearGradient>

    <!-- Card Gradients -->
    <linearGradient id="cardGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#1e293b" stop-opacity="0.85" />
      <stop offset="100%" stop-color="#0f172a" stop-opacity="0.95" />
    </linearGradient>
  </defs>

  <!-- Container Box -->
  <rect x="2" y="2" width="1056" height="586" rx="16" fill="url(#bgGrad)" stroke="#334155" stroke-width="1.5" />

  <!-- Header -->
  <g transform="translate(48, 40)">
    <text x="0" y="0" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="20" font-weight="700" fill="#f8fafc" letter-spacing="-0.02em">
      Generational Fitness Trajectory &amp; Sandbox Convergence
    </text>
    <text x="0" y="22" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="12" font-weight="400" fill="#94a3b8">
      Empirical convergence between unconstrained reasoning, grounded sandbox execution, active tool runtimes, and consortiums across 7 generations
    </text>
  </g>

  <!-- Legend -->
  <g transform="translate(660, 34)">
    <!-- Track A Legend -->
    <line x1="0" y1="8" x2="24" y2="8" stroke="url(#trackAGrad)" stroke-width="2.5" stroke-dasharray="4 2" />
    <circle cx="12" cy="8" r="3.5" fill="#38bdf8" />
    <text x="32" y="12" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="11" font-weight="500" fill="#cbd5e1">
      Raw Semantic Frontier (Pre-Penalty)
    </text>

    <!-- Track B Legend -->
    <line x1="0" y1="28" x2="24" y2="28" stroke="url(#trackBGrad)" stroke-width="3.5" stroke-linecap="round" />
    <circle cx="12" cy="28" r="4.5" fill="#3b82f6" />
    <text x="32" y="32" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="11" font-weight="600" fill="#f8fafc">
      Grounded Sandbox Score (Final Benchmark)
    </text>
  </g>

  <!-- Chart Grid Area -->
  <g transform="translate(60, 105)">
    <!-- Horizontal Grid Lines & Y-Labels -->
    <line x1="0" y1="0" x2="940" y2="0" stroke="#1e293b" stroke-width="1" stroke-dasharray="4 4" />
    <text x="-14" y="4" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="11" font-weight="500" fill="#64748b" text-anchor="end">100</text>

    <line x1="0" y1="70" x2="940" y2="70" stroke="#1e293b" stroke-width="1" stroke-dasharray="4 4" />
    <text x="-14" y="74" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="11" font-weight="500" fill="#64748b" text-anchor="end">90</text>

    <line x1="0" y1="140" x2="940" y2="140" stroke="#1e293b" stroke-width="1" stroke-dasharray="4 4" />
    <text x="-14" y="144" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="11" font-weight="500" fill="#64748b" text-anchor="end">80</text>

    <line x1="0" y1="210" x2="940" y2="210" stroke="#1e293b" stroke-width="1" stroke-dasharray="4 4" />
    <text x="-14" y="214" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="11" font-weight="500" fill="#64748b" text-anchor="end">70</text>

    <line x1="0" y1="280" x2="940" y2="280" stroke="#1e293b" stroke-width="1" stroke-dasharray="4 4" />
    <text x="-14" y="284" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="11" font-weight="500" fill="#64748b" text-anchor="end">60</text>

    <!-- Vertical Milestone Markers -->
    <!-- Gen 0 Baseline (x=25) -->
    <line x1="25" y1="0" x2="25" y2="280" stroke="#1e293b" stroke-width="1" />
    <text x="25" y="298" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="9" font-weight="600" fill="#cbd5e1" text-anchor="middle">Gen 0 Baseline</text>
    <text x="25" y="312" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="8" font-weight="400" fill="#64748b" text-anchor="middle">Seed (31)</text>

    <!-- Gen 0 Parallel Tournament (x=145) -->
    <line x1="145" y1="0" x2="145" y2="280" stroke="#1e293b" stroke-width="1" />
    <text x="145" y="298" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="9" font-weight="600" fill="#cbd5e1" text-anchor="middle">Gen 0 Tourn.</text>
    <text x="145" y="312" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="8" font-weight="400" fill="#64748b" text-anchor="middle">10 Firms (310)</text>

    <!-- Gen 1 Parallel Tournament (x=265) -->
    <line x1="265" y1="0" x2="265" y2="280" stroke="#1e293b" stroke-width="1" />
    <text x="265" y="298" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="9" font-weight="600" fill="#cbd5e1" text-anchor="middle">Gen 1 Evolved</text>
    <text x="265" y="312" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="8" font-weight="400" fill="#64748b" text-anchor="middle">Breeding (312)</text>

    <!-- Gen 2 Persona Discretization (x=385) -->
    <line x1="385" y1="0" x2="385" y2="280" stroke="#10b981" stroke-width="1" stroke-dasharray="3 3" opacity="0.6" />
    <text x="385" y="298" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="9" font-weight="600" fill="#34d399" text-anchor="middle">Gen 2 Breakth.</text>
    <text x="385" y="312" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="8" font-weight="400" fill="#10b981" text-anchor="middle">Traits (314)</text>

    <!-- Gen 3 Consensus Peak (x=505) -->
    <line x1="505" y1="0" x2="505" y2="280" stroke="#06b6d4" stroke-width="1" stroke-dasharray="3 3" opacity="0.8" />
    <text x="505" y="298" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="9" font-weight="600" fill="#38bdf8" text-anchor="middle">Gen 3 Consensus</text>
    <text x="505" y="312" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="8" font-weight="400" fill="#06b6d4" text-anchor="middle">Mining (318)</text>

    <!-- Gen 4 OpEx & Autonomous Sizing (x=630) -->
    <line x1="630" y1="0" x2="630" y2="280" stroke="#8b5cf6" stroke-width="1.5" stroke-dasharray="3 3" opacity="0.9" />
    <text x="630" y="298" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="9" font-weight="700" fill="#a78bfa" text-anchor="middle">Gen 4 OpEx</text>
    <text x="630" y="312" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="8" font-weight="500" fill="#c4b5fd" text-anchor="middle">Sizing (320)</text>

    <!-- Gen 5 Active Tool Sandboxing (x=755) -->
    <line x1="755" y1="0" x2="755" y2="280" stroke="#f43f5e" stroke-width="1.5" stroke-dasharray="3 3" opacity="0.9" />
    <text x="755" y="298" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="9" font-weight="700" fill="#fb7185" text-anchor="middle">Gen 5 Tools</text>
    <text x="755" y="312" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="8" font-weight="500" fill="#fda4af" text-anchor="middle">IP Market (325)</text>

    <!-- Gen 6 Consortiums & Multi-Domain (x=880) -->
    <line x1="880" y1="0" x2="880" y2="280" stroke="#3b82f6" stroke-width="1.5" stroke-dasharray="3 3" opacity="0.9" />
    <text x="880" y="298" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="9" font-weight="700" fill="#60a5fa" text-anchor="middle">Gen 6 Consortium</text>
    <text x="880" y="312" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="8" font-weight="500" fill="#93c5fd" text-anchor="middle">Multi-Domain (327)</text>

    <!-- Track A: Raw Semantic Frontier -->
    <path d="M 25 49 C 90 32, 120 27, 145 26.25 C 210 24, 240 20, 265 19.6 C 330 19, 360 35, 385 38.5 C 450 38, 480 23, 505 22.75 C 570 22.75, 600 22.75, 630 22.75 C 690 20, 725 14, 755 14 C 815 14, 850 17.5, 880 17.5" 
          fill="none" stroke="url(#trackAGrad)" stroke-width="2.5" stroke-dasharray="5 3" />

    <circle cx="25" cy="49" r="4" fill="#10b981" stroke="#0f172a" stroke-width="1.5" />
    <circle cx="145" cy="26.25" r="4" fill="#06b6d4" stroke="#0f172a" stroke-width="1.5" />
    <circle cx="265" cy="19.6" r="4" fill="#38bdf8" stroke="#0f172a" stroke-width="1.5" />

    <!-- Track B: Grounded Sandbox Execution Area & Curve -->
    <polygon points="145,157.5 265,164.15 385,38.5 505,22.75 630,22.75 755,56.49 880,56.91 880,280 145,280" fill="url(#trackBArea)" />
    <path d="M 145 157.5 C 210 160, 240 165, 265 164.15 C 330 162, 360 60, 385 38.5 C 445 25, 475 23, 505 22.75 C 570 22.75, 600 22.75, 630 22.75 C 690 25, 725 50, 755 56.49 C 815 62, 850 57, 880 56.91" 
          fill="none" stroke="url(#trackBGrad)" stroke-width="4" stroke-linecap="round" />

    <!-- Gen 0 (x=145): 77.50 -->
    <circle cx="145" cy="157.5" r="4.5" fill="#ec4899" stroke="#0f172a" stroke-width="2" />
    <rect x="85" y="146" width="55" height="18" rx="4" fill="#701a75" stroke="#c026d3" stroke-width="1" />
    <text x="112" y="159" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="8" font-weight="700" fill="#fdf4ff" text-anchor="middle">77.50 [G0]</text>

    <!-- Gen 1 (x=265): 76.55 -->
    <circle cx="265" cy="164.15" r="4.5" fill="#f59e0b" stroke="#0f172a" stroke-width="2" />
    <rect x="272" y="153" width="55" height="18" rx="4" fill="#78350f" stroke="#d97706" stroke-width="1" />
    <text x="299" y="166" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="8" font-weight="700" fill="#fef3c7" text-anchor="middle">76.55 [G1]</text>

    <!-- Gen 2 (x=385): 94.50 -->
    <circle cx="385" cy="38.5" r="5" fill="#10b981" stroke="#0f172a" stroke-width="2" />
    <rect x="330" y="28" width="50" height="18" rx="4" fill="#064e3b" stroke="#10b981" stroke-width="1" />
    <text x="355" y="41" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="8" font-weight="700" fill="#a7f3d0" text-anchor="middle">94.50 [G2]</text>

    <!-- Gen 3 (x=505): 96.75 -->
    <circle cx="505" cy="22.75" r="5" fill="#06b6d4" stroke="#0f172a" stroke-width="2" />
    <rect x="455" y="12" width="45" height="18" rx="4" fill="#083344" stroke="#06b6d4" stroke-width="1" />
    <text x="477" y="25" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="8" font-weight="700" fill="#cffafe" text-anchor="middle">96.75 [G3]</text>

    <!-- Gen 4 (x=630): 96.75 -->
    <circle cx="630" cy="22.75" r="6" fill="#8b5cf6" stroke="#ffffff" stroke-width="2" />
    <rect x="580" y="-10" width="100" height="22" rx="5" fill="#2e1065" stroke="#8b5cf6" stroke-width="1.5" />
    <text x="630" y="5" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="9.5" font-weight="800" fill="#ede9fe" text-anchor="middle">96.75 [G4 10 Files]</text>

    <!-- Gen 5 (x=755): 91.93 -->
    <circle cx="755" cy="56.49" r="6.5" fill="#f43f5e" stroke="#ffffff" stroke-width="2" />
    <rect x="695" y="44" width="115" height="22" rx="5" fill="#881337" stroke="#f43f5e" stroke-width="1.5" />
    <text x="752" y="59" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="9.5" font-weight="800" fill="#ffe4e6" text-anchor="middle">91.93 [G5 12 Files]</text>

    <!-- Gen 6 (x=880): 91.87 -->
    <circle cx="880" cy="56.91" r="7.5" fill="#3b82f6" stroke="#ffffff" stroke-width="2.5" />
    <rect x="815" y="44" width="130" height="24" rx="6" fill="#1e3a8a" stroke="#3b82f6" stroke-width="1.5" />
    <text x="880" y="60" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="10.5" font-weight="800" fill="#dbeafe" text-anchor="middle">91.87 [G6 15 Files]</text>
  </g>

  <!-- Metric Highlights Strip (Bottom 4 Cards) -->
  <g transform="translate(48, 460)">
    <!-- Card 1 -->
    <g transform="translate(0, 0)">
      <rect width="225" height="85" rx="10" fill="url(#cardGrad)" stroke="#334155" stroke-width="1" />
      <text x="16" y="26" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="10" font-weight="600" fill="#94a3b8" letter-spacing="0.05em">GEN 6 CHAMPION</text>
      <text x="16" y="56" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="22" font-weight="800" fill="#60a5fa">91.87 pts</text>
      <text x="16" y="74" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="10" font-weight="500" fill="#64748b">gen_6_elite_1 (33 Agents)</text>
    </g>

    <!-- Card 2 -->
    <g transform="translate(245, 0)">
      <rect width="225" height="85" rx="10" fill="url(#cardGrad)" stroke="#334155" stroke-width="1" />
      <text x="16" y="26" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="10" font-weight="600" fill="#94a3b8" letter-spacing="0.05em">PACKAGE PRODUCTION</text>
      <text x="16" y="56" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="22" font-weight="800" fill="#8b5cf6">Up to 17 Files</text>
      <text x="16" y="74" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="10" font-weight="500" fill="#64748b">Built Egg-Info &amp; Pytest Run</text>
    </g>

    <!-- Card 3 -->
    <g transform="translate(490, 0)">
      <rect width="225" height="85" rx="10" fill="url(#cardGrad)" stroke="#334155" stroke-width="1" />
      <text x="16" y="26" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="10" font-weight="600" fill="#94a3b8" letter-spacing="0.05em">OPEX DISCIPLINE</text>
      <text x="16" y="56" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="22" font-weight="800" fill="#10b981">$0.3387 / firm</text>
      <text x="16" y="74" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="10" font-weight="500" fill="#64748b">Under $0.45 Budget Envelope</text>
    </g>

    <!-- Card 4 -->
    <g transform="translate(735, 0)">
      <rect width="225" height="85" rx="10" fill="url(#cardGrad)" stroke="#334155" stroke-width="1" />
      <text x="16" y="26" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="10" font-weight="600" fill="#94a3b8" letter-spacing="0.05em">MULTI-DOMAIN HARNESS</text>
      <text x="16" y="56" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="22" font-weight="800" fill="#38bdf8">Consortium Ready</text>
      <text x="16" y="74" font-family="system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="10" font-weight="500" fill="#64748b">Software, Finance, Compliance</text>
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
    "--window-size=1060,590",
    temp_html
]
subprocess.run(cmd, check=True)
print(f"[SUCCESS] Rendered PNG to {target_png}")
