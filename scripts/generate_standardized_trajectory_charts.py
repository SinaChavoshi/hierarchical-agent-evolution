"""
Generates publication-grade SVG and PNG charts showing:
1. Standardized Grounded Usability Trajectory (Corrected for physical execution)
2. Legacy Unconstrained Synthetic Score vs Grounded Physical Usability Score
3. Physical Workspace Disk Artifact Growth (0 to 17 files, 12.3 cohort avg)
4. Empirical inclusion of Generation 8 & Generation 9
"""

import os
import subprocess
import xml.etree.ElementTree as ET

def score_to_y(s):
    return round(120 + (100.0 - s) * 5.0, 1)

SVG_CONTENT = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1100 640" width="1100" height="640">
  <defs>
    <linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#070b14" />
      <stop offset="50%" stop-color="#0b1120" />
      <stop offset="100%" stop-color="#0f172a" />
    </linearGradient>

    <!-- Legacy Track Gradient -->
    <linearGradient id="legacyGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#64748b" />
      <stop offset="50%" stop-color="#94a3b8" />
      <stop offset="100%" stop-color="#cbd5e1" />
    </linearGradient>

    <!-- Grounded Physical Track Gradient -->
    <linearGradient id="groundedGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#ec4899" />
      <stop offset="18%" stop-color="#f59e0b" />
      <stop offset="40%" stop-color="#10b981" />
      <stop offset="60%" stop-color="#06b6d4" />
      <stop offset="80%" stop-color="#3b82f6" />
      <stop offset="100%" stop-color="#a855f7" />
    </linearGradient>

    <linearGradient id="groundedArea" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#10b981" stop-opacity="0.25" />
      <stop offset="50%" stop-color="#06b6d4" stop-opacity="0.12" />
      <stop offset="100%" stop-color="#070b14" stop-opacity="0.0" />
    </linearGradient>
  </defs>

  <!-- Container Box -->
  <rect x="2" y="2" width="1096" height="636" rx="16" fill="url(#bgGrad)" stroke="#1e293b" stroke-width="1.5" />

  <!-- Header -->
  <g transform="translate(45, 42)">
    <text x="0" y="0" font-family="system-ui, -apple-system, sans-serif" font-size="21" font-weight="700" fill="#f8fafc" letter-spacing="-0.02em">
      Software Quality &amp; Usability Trajectory: Standardized Execution Verification
    </text>
    <text x="0" y="24" font-family="system-ui, -apple-system, sans-serif" font-size="12" font-weight="400" fill="#94a3b8">
      Retroactive correction penalizes unexecuted code: reveals true phase shift from synthetic prose to physical container scratchpads &amp; self-repair
    </text>
  </g>

  <!-- Legend -->
  <g transform="translate(670, 36)">
    <line x1="0" y1="8" x2="28" y2="8" stroke="#64748b" stroke-width="2.5" stroke-dasharray="4 3" />
    <circle cx="14" cy="8" r="3.5" fill="#94a3b8" />
    <text x="36" y="12" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="500" fill="#94a3b8">
      Legacy Synthetic Score (Unchecked Regex / Prose)
    </text>

    <line x1="0" y1="28" x2="28" y2="28" stroke="url(#groundedGrad)" stroke-width="3.5" stroke-linecap="round" />
    <circle cx="14" cy="28" r="4.5" fill="#a855f7" />
    <text x="36" y="32" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="600" fill="#38bdf8">
      Standardized Grounded Score (Physically Executable)
    </text>
  </g>

  <!-- Phase Shift Dividing Banner -->
  <rect x="505" y="90" width="555" height="470" rx="12" fill="#0f172a" fill-opacity="0.45" stroke="#06b6d4" stroke-opacity="0.25" stroke-dasharray="6 4" />
  <text x="782" y="112" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="700" fill="#38bdf8" text-anchor="middle" letter-spacing="0.05em">
    ⚡ PHASE SHIFT: CONTAINER SCRATCHPADS, TEST DISCOVERY, SELF-REPAIR &amp; MORPHOGENESIS
  </text>

  <!-- Grid & Y Axis -->
  <g transform="translate(50, 0)">
    <line x1="0" y1="120" x2="1015" y2="120" stroke="#1e293b" stroke-width="1" stroke-dasharray="4 4" />
    <text x="-10" y="124" font-family="system-ui, -apple-system, sans-serif" font-size="11" fill="#475569" text-anchor="end">100</text>

    <line x1="0" y1="170" x2="1015" y2="170" stroke="#1e293b" stroke-width="1" stroke-dasharray="4 4" />
    <text x="-10" y="174" font-family="system-ui, -apple-system, sans-serif" font-size="11" fill="#475569" text-anchor="end">90</text>

    <line x1="0" y1="220" x2="1015" y2="220" stroke="#1e293b" stroke-width="1" stroke-dasharray="4 4" />
    <text x="-10" y="224" font-family="system-ui, -apple-system, sans-serif" font-size="11" fill="#475569" text-anchor="end">80</text>

    <line x1="0" y1="270" x2="1015" y2="270" stroke="#1e293b" stroke-width="1" stroke-dasharray="4 4" />
    <text x="-10" y="274" font-family="system-ui, -apple-system, sans-serif" font-size="11" fill="#475569" text-anchor="end">70</text>

    <line x1="0" y1="320" x2="1015" y2="320" stroke="#1e293b" stroke-width="1" stroke-dasharray="4 4" />
    <text x="-10" y="324" font-family="system-ui, -apple-system, sans-serif" font-size="11" fill="#475569" text-anchor="end">60</text>

    <line x1="0" y1="370" x2="1015" y2="370" stroke="#1e293b" stroke-width="1" stroke-dasharray="4 4" />
    <text x="-10" y="374" font-family="system-ui, -apple-system, sans-serif" font-size="11" fill="#475569" text-anchor="end">50</text>

    <line x1="0" y1="420" x2="1015" y2="420" stroke="#1e293b" stroke-width="1" stroke-dasharray="4 4" />
    <text x="-10" y="424" font-family="system-ui, -apple-system, sans-serif" font-size="11" fill="#475569" text-anchor="end">40</text>

    <line x1="0" y1="470" x2="1015" y2="470" stroke="#1e293b" stroke-width="1" stroke-dasharray="4 4" />
    <text x="-10" y="474" font-family="system-ui, -apple-system, sans-serif" font-size="11" fill="#475569" text-anchor="end">30</text>

    <line x1="0" y1="520" x2="1015" y2="520" stroke="#1e293b" stroke-width="1" stroke-dasharray="4 4" />
    <text x="-10" y="524" font-family="system-ui, -apple-system, sans-serif" font-size="11" fill="#475569" text-anchor="end">20</text>
  </g>

  <!-- Area Fill under Grounded Track -->
  <polygon points="
    70,493.8
    165,237.2
    260,198.8
    355,191.2
    450,198.8
    550,160.4
    650,160.7
    750,206.5
    850,220.6
    945,181.6
    945,530
    70,530
  " fill="url(#groundedArea)" />

  <!-- Track A: Legacy Curve (Dashed Gray) -->
  <polyline points="
    70,368.8
    165,237.2
    260,147.5
    355,136.2
    450,136.2
    550,160.4
    650,160.7
    750,206.5
    850,146.0
    945,146.5
  " fill="none" stroke="url(#legacyGrad)" stroke-width="2.5" stroke-dasharray="5 3" />

  <!-- Track B: Grounded Curve (Bold Glowing Gradient) -->
  <polyline points="
    70,493.8
    165,237.2
    260,198.8
    355,191.2
    450,198.8
    550,160.4
    650,160.7
    750,206.5
    850,220.6
    945,181.6
  " fill="none" stroke="url(#groundedGrad)" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" />

  <!-- Projected Gen 10 (Federated Mesh & Self-Evolving Rubrics) -->
  <polyline points="945,181.6 1035,155.0" fill="none" stroke="#38bdf8" stroke-width="3" stroke-dasharray="4 4" stroke-linecap="round" />

  <!-- Generation Nodes & Data Badges -->
  <!-- Gen 0 -->
  <g transform="translate(70, 0)">
    <line x1="0" y1="120" x2="0" y2="530" stroke="#1e293b" stroke-width="1" />
    <circle cx="0" cy="368.8" r="4" fill="#64748b" />
    <text x="0" y="360" font-family="system-ui, -apple-system, sans-serif" font-size="9" fill="#64748b" text-anchor="middle">50.25*</text>

    <circle cx="0" cy="493.8" r="6" fill="#ec4899" stroke="#070b14" stroke-width="2" />
    <text x="0" y="512" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="700" fill="#f43f5e" text-anchor="middle">25.25</text>

    <text x="0" y="546" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="700" fill="#cbd5e1" text-anchor="middle">Gen 0</text>
    <text x="0" y="560" font-family="system-ui, -apple-system, sans-serif" font-size="8" fill="#64748b" text-anchor="middle">0 Files (Prose)</text>
    <text x="0" y="572" font-family="system-ui, -apple-system, sans-serif" font-size="8" fill="#ef4444" text-anchor="middle">0/4 Gates</text>
  </g>

  <!-- Gen 1 -->
  <g transform="translate(165, 0)">
    <line x1="0" y1="120" x2="0" y2="530" stroke="#1e293b" stroke-width="1" />
    <circle cx="0" cy="237.2" r="6" fill="#f59e0b" stroke="#070b14" stroke-width="2" />
    <text x="0" y="226" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="700" fill="#f59e0b" text-anchor="middle">76.55</text>

    <text x="0" y="546" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="700" fill="#cbd5e1" text-anchor="middle">Gen 1</text>
    <text x="0" y="560" font-family="system-ui, -apple-system, sans-serif" font-size="8" fill="#64748b" text-anchor="middle">0 Files (Org)</text>
    <text x="0" y="572" font-family="system-ui, -apple-system, sans-serif" font-size="8" fill="#ef4444" text-anchor="middle">0/4 Gates</text>
  </g>

  <!-- Gen 2 -->
  <g transform="translate(260, 0)">
    <line x1="0" y1="120" x2="0" y2="530" stroke="#1e293b" stroke-width="1" />
    <circle cx="0" cy="147.5" r="4" fill="#64748b" />
    <text x="0" y="140" font-family="system-ui, -apple-system, sans-serif" font-size="9" fill="#64748b" text-anchor="middle">94.50*</text>

    <circle cx="0" cy="198.8" r="6" fill="#eab308" stroke="#070b14" stroke-width="2" />
    <text x="0" y="190" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="700" fill="#eab308" text-anchor="middle">84.25</text>

    <text x="0" y="546" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="700" fill="#cbd5e1" text-anchor="middle">Gen 2</text>
    <text x="0" y="560" font-family="system-ui, -apple-system, sans-serif" font-size="8" fill="#64748b" text-anchor="middle">Markdown Only</text>
    <text x="0" y="572" font-family="system-ui, -apple-system, sans-serif" font-size="8" fill="#f59e0b" text-anchor="middle">1/4 Gates</text>
  </g>

  <!-- Gen 3 -->
  <g transform="translate(355, 0)">
    <line x1="0" y1="120" x2="0" y2="530" stroke="#1e293b" stroke-width="1" />
    <circle cx="0" cy="136.2" r="4" fill="#64748b" />
    <text x="0" y="128" font-family="system-ui, -apple-system, sans-serif" font-size="9" fill="#64748b" text-anchor="middle">96.75*</text>

    <circle cx="0" cy="191.2" r="6" fill="#10b981" stroke="#070b14" stroke-width="2" />
    <text x="0" y="182" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="700" fill="#10b981" text-anchor="middle">85.75</text>

    <text x="0" y="546" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="700" fill="#cbd5e1" text-anchor="middle">Gen 3</text>
    <text x="0" y="560" font-family="system-ui, -apple-system, sans-serif" font-size="8" fill="#64748b" text-anchor="middle">Markdown Only</text>
    <text x="0" y="572" font-family="system-ui, -apple-system, sans-serif" font-size="8" fill="#f59e0b" text-anchor="middle">2/4 Gates</text>
  </g>

  <!-- Gen 4 -->
  <g transform="translate(450, 0)">
    <line x1="0" y1="120" x2="0" y2="530" stroke="#1e293b" stroke-width="1" />
    <circle cx="0" cy="136.2" r="4" fill="#64748b" />
    <text x="0" y="128" font-family="system-ui, -apple-system, sans-serif" font-size="9" fill="#64748b" text-anchor="middle">96.75*</text>

    <circle cx="0" cy="198.8" r="6" fill="#14b8a6" stroke="#070b14" stroke-width="2" />
    <text x="0" y="190" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="700" fill="#14b8a6" text-anchor="middle">84.25</text>

    <text x="0" y="546" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="700" fill="#cbd5e1" text-anchor="middle">Gen 4</text>
    <text x="0" y="560" font-family="system-ui, -apple-system, sans-serif" font-size="8" fill="#64748b" text-anchor="middle">Consortiums (Text)</text>
    <text x="0" y="572" font-family="system-ui, -apple-system, sans-serif" font-size="8" fill="#f59e0b" text-anchor="middle">2/4 Gates</text>
  </g>

  <!-- Gen 5 -->
  <g transform="translate(550, 0)">
    <line x1="0" y1="120" x2="0" y2="530" stroke="#06b6d4" stroke-width="1.5" stroke-dasharray="2 2" />
    <circle cx="0" cy="160.4" r="7" fill="#06b6d4" stroke="#ffffff" stroke-width="2" />
    <text x="0" y="150" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="700" fill="#38bdf8" text-anchor="middle">91.93</text>

    <text x="0" y="546" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="700" fill="#38bdf8" text-anchor="middle">Gen 5</text>
    <text x="0" y="560" font-family="system-ui, -apple-system, sans-serif" font-size="8" font-weight="600" fill="#38bdf8" text-anchor="middle">14 Physical Files</text>
    <text x="0" y="572" font-family="system-ui, -apple-system, sans-serif" font-size="8" fill="#10b981" text-anchor="middle">Live Scratchpad</text>
  </g>

  <!-- Gen 6 -->
  <g transform="translate(650, 0)">
    <line x1="0" y1="120" x2="0" y2="530" stroke="#3b82f6" stroke-width="1.5" stroke-dasharray="2 2" />
    <circle cx="0" cy="160.7" r="7" fill="#3b82f6" stroke="#ffffff" stroke-width="2" />
    <text x="0" y="150" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="700" fill="#60a5fa" text-anchor="middle">91.87</text>

    <text x="0" y="546" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="700" fill="#60a5fa" text-anchor="middle">Gen 6</text>
    <text x="0" y="560" font-family="system-ui, -apple-system, sans-serif" font-size="8" font-weight="600" fill="#60a5fa" text-anchor="middle">17 Physical Files</text>
    <text x="0" y="572" font-family="system-ui, -apple-system, sans-serif" font-size="8" fill="#10b981" text-anchor="middle">Dist Build Hardening</text>
  </g>

  <!-- Gen 7 -->
  <g transform="translate(750, 0)">
    <line x1="0" y1="120" x2="0" y2="530" stroke="#8b5cf6" stroke-width="1.5" stroke-dasharray="2 2" />
    <circle cx="0" cy="206.5" r="7" fill="#8b5cf6" stroke="#ffffff" stroke-width="2" />
    <text x="0" y="196" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="700" fill="#c084fc" text-anchor="middle">82.70</text>

    <text x="0" y="546" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="700" fill="#c084fc" text-anchor="middle">Gen 7</text>
    <text x="0" y="560" font-family="system-ui, -apple-system, sans-serif" font-size="8" font-weight="600" fill="#c084fc" text-anchor="middle">13 Physical Files</text>
    <text x="0" y="572" font-family="system-ui, -apple-system, sans-serif" font-size="8" fill="#c084fc" text-anchor="middle">Multi-Platform Port</text>
  </g>

  <!-- Gen 8 -->
  <g transform="translate(850, 0)">
    <line x1="0" y1="120" x2="0" y2="530" stroke="#a855f7" stroke-width="1.5" stroke-dasharray="2 2" />
    <circle cx="0" cy="146.0" r="4" fill="#64748b" />
    <text x="0" y="138" font-family="system-ui, -apple-system, sans-serif" font-size="9" fill="#64748b" text-anchor="middle">94.80*</text>

    <circle cx="0" cy="220.6" r="7" fill="#a855f7" stroke="#ffffff" stroke-width="2" />
    <text x="0" y="240" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="700" fill="#d8b4fe" text-anchor="middle">79.89</text>

    <text x="0" y="546" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="700" fill="#d8b4fe" text-anchor="middle">Gen 8</text>
    <text x="0" y="560" font-family="system-ui, -apple-system, sans-serif" font-size="8" font-weight="600" fill="#d8b4fe" text-anchor="middle">16 Max Files</text>
    <text x="0" y="572" font-family="system-ui, -apple-system, sans-serif" font-size="8" fill="#10b981" text-anchor="middle">Tests: PASS Verified!</text>
  </g>

  <!-- Gen 9 (Completed) -->
  <g transform="translate(945, 0)">
    <line x1="0" y1="120" x2="0" y2="530" stroke="#ec4899" stroke-width="1.5" stroke-dasharray="2 2" />
    <circle cx="0" cy="146.5" r="4" fill="#64748b" />
    <text x="0" y="138" font-family="system-ui, -apple-system, sans-serif" font-size="9" fill="#64748b" text-anchor="middle">94.70*</text>

    <circle cx="0" cy="181.6" r="7" fill="#ec4899" stroke="#ffffff" stroke-width="2" />
    <text x="0" y="172" font-family="system-ui, -apple-system, sans-serif" font-size="12" font-weight="700" fill="#f472b6" text-anchor="middle">87.68</text>

    <text x="0" y="546" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="700" fill="#f472b6" text-anchor="middle">Gen 9</text>
    <text x="0" y="560" font-family="system-ui, -apple-system, sans-serif" font-size="8" font-weight="600" fill="#f472b6" text-anchor="middle">17 Max / 12.3 Avg</text>
    <text x="0" y="572" font-family="system-ui, -apple-system, sans-serif" font-size="8" fill="#10b981" text-anchor="middle">Morphic Topologies</text>
  </g>

  <!-- Gen 10 (Projected) -->
  <g transform="translate(1035, 0)">
    <line x1="0" y1="120" x2="0" y2="530" stroke="#38bdf8" stroke-width="1" stroke-dasharray="3 3" />
    <circle cx="0" cy="155.0" r="6" fill="#070b14" stroke="#38bdf8" stroke-width="2" stroke-dasharray="3 2" />
    <text x="0" y="145" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="700" fill="#38bdf8" text-anchor="middle">~93.0*</text>

    <text x="0" y="546" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="700" fill="#38bdf8" text-anchor="middle">Gen 10 (Active)</text>
    <text x="0" y="560" font-family="system-ui, -apple-system, sans-serif" font-size="8" font-weight="600" fill="#38bdf8" text-anchor="middle">Federated Mesh</text>
    <text x="0" y="572" font-family="system-ui, -apple-system, sans-serif" font-size="8" fill="#38bdf8" text-anchor="middle">&amp; Self-Evolving Rubrics</text>
  </g>

  <!-- Explanatory Callout Box -->
  <g transform="translate(45, 595)">
    <rect x="0" y="0" width="1010" height="32" rx="6" fill="#0f172a" stroke="#1e293b" />
    <text x="14" y="20" font-family="system-ui, -apple-system, sans-serif" font-size="10" fill="#94a3b8">
      <tspan font-weight="700" fill="#f8fafc">Empirical Grounding Note:</tspan>
      Dashed gray line (*) represents legacy unconstrained scores where tests were marked PASS via static markdown regex without live execution.
      Gen 9 achieved 87.68 Net Fitness with an all-time record 12.3 average physical files per firm (peak 17 files) across dynamic 3-to-6 pod topologies.
    </text>
  </g>
</svg>
'''

tree = ET.fromstring(SVG_CONTENT.strip())
print("[SUCCESS] XML is 100% VALID! Root tag:", tree.tag)

svg_path = "experiments/assets/standardized_fitness_trajectory.svg"
png_path = "experiments/assets/standardized_fitness_trajectory.png"

with open(svg_path, "w") as f:
    f.write(SVG_CONTENT.strip())
print(f"[SUCCESS] Saved SVG to {svg_path}")

html_wrapper = f"""<!DOCTYPE html>
<html>
<head><style>body {{ margin: 0; padding: 0; background: #070b14; overflow: hidden; }}</style></head>
<body>
{SVG_CONTENT}
</body>
</html>"""
temp_html = "/tmp/render_standardized_chart.html"
with open(temp_html, "w") as f:
    f.write(html_wrapper)

cmd = [
    "/usr/bin/google-chrome",
    "--headless",
    "--disable-gpu",
    "--hide-scrollbars",
    f"--screenshot={png_path}",
    "--window-size=1100,640",
    temp_html
]
subprocess.run(cmd, check=True)
print(f"[SUCCESS] Rendered PNG to {png_path}")
