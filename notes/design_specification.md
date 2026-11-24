# OmniHarness / Control AI Design Specification
**Source**: Figma Node `3:2` ("Control AI · Policy platform", 1600x1200px)
**Visual Reference**: `figma_reference.png`
**Target File**: `harness/dashboard.html`

---

## 1. Canvas Architecture & Cosmic Obsidian Atmosphere
- **Canvas Viewport**: Fixed 1600px × 1200px responsive scalable container (or fluid viewport pinned to `min-w-[1400px]`, `min-h-[900px]`).
- **Base Background**: Deep Obsidian Void `#040a0e` (`rgb(4, 10, 14)`).
- **Secondary Surfaces**:
  - Deep Panel Background: `#040b0f` (`rgb(4, 11, 15)`)
  - Glass Card Base: `#081c20` / `rgba(6, 17, 21, 0.45)`
  - Active Card Surface: `#0e1a1f` / `#103137`
  - Toolbar Glass Fill: `#103137` with `border: 4px solid #061215`
  - Toolbar Button Backgrounds: `#131716`, `#151b19`, `#181b19`, active state `#020708`
- **Atmospheric Glowing Radial Gradients**:
  1. *Central Policy Intelligence Orb Aura* (left graph region, x~180px, y~530px):
     ```css
     background: radial-gradient(circle at 180px 530px, rgba(20, 60, 65, 0.5) 0%, rgba(10, 35, 40, 0.25) 45%, rgba(4, 10, 14, 0) 75%);
     ```
  2. *Active Hub Focal Glow / Optical Flare* (right edge of Hub #3, x=582px, y=537px):
     ```css
     background: radial-gradient(ellipse at 582px 537px, rgba(200, 255, 250, 0.85) 0%, rgba(84, 155, 159, 0.45) 30%, rgba(4, 10, 14, 0) 70%);
     ```
  3. *Right Drawer Ambient Glow* (top right glassmorphic drawer, x=510px, y=213px):
     ```css
     background: radial-gradient(circle at 510px 213px, rgba(52, 85, 88, 0.4) 0%, rgba(32, 59, 62, 0.2) 70%, rgba(6, 17, 22, 0) 100%), #040a0e;
     ```
  4. *Sentiment Rate Amber/Burgundy Glow* (drawer sentiment card):
     ```css
     background: radial-gradient(circle at 265px 80px, rgba(100, 49, 47, 0.62) 0%, rgba(59, 37, 37, 0.31) 50%, rgba(17, 25, 26, 0) 100%), #040b0f;
     ```

---

## 2. Color Tokens & Palette Matrix

| Semantic Token | HEX / RGBA Value | Figma Node Usage |
| :--- | :--- | :--- |
| `color-obsidian-base` | `#040a0e` | Canvas background, outer perimeter |
| `color-obsidian-card` | `#040b0f` / `#081c20` | Policy detail panel, 2x2 metric cards |
| `color-glass-border` | `rgba(87, 114, 115, 0.35)` | Inactive cards, drawer separators |
| `color-glass-border-strong` | `rgba(87, 114, 115, 0.8)` | Drawer status section border |
| `color-gold-accent` | `#d9bd86` | Logo symbol, active card title, selected node ring |
| `color-gold-border` | `#c5a56e` / `#b49c6c` | Active hub border, "+ New Simulation" border |
| `color-gold-badge` | `#c5a56e` | Notification badge count (5, 2) |
| `color-teal-glow` | `rgba(84, 155, 159, 0.85)` | Primary optic-fiber fan curves, active node dot |
| `color-teal-dark` | `#103137` / `#183e42` | Toolbar border, Send question button |
| `color-active-pill-bg` | `#173a32` | "Active" status badge fill |
| `color-active-pill-border` | `#a0d9bc` | "Active" status badge border |
| `color-moderate-pill-bg` | `#282a23` | "Moderate" sentiment badge fill |
| `color-moderate-pill-border`| `#c6ae7a` | "Moderate" sentiment badge border |
| `color-risk-red` | `#d9534f` / `#c0392b` | High risk tick (level 1) |
| `color-risk-amber` | `#c5a56e` / `#e67e22` | Moderate risk tick (level 2) |
| `color-risk-teal` | `#27ae60` / `#549b9f` | Low risk tick (level 3) |
| `color-text-hero` | `#ffffff` / `#fafaf7` | Big metrics ("45%", "24", "18"), active title |
| `color-text-cream` | `#f2eadd` / `#f2ebda` | Drawer "Healthcare" header, "Sentiment Rate" |
| `color-text-muted` | `#9fafac` / `#a3928d` | Sub-labels, metadata dates, gap percentages |

---

## 3. Typography Hierarchy (Inter)

All text uses **Inter** (`Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif`).

1. **44px (Regular, weight 400)**:
   - Sentiment Rate big number: `"45%"` (`text-white`)
2. **29px (Semi-Bold / Bold, weight 600-700)**:
   - Category Hub numbers: `"18"`, `"3"`, `"24"`, `"32"`, `"12"`
   - Drawer 2x2 metric numbers: `"12"`, `"2"`, `"18"`, `"12"`
3. **24px (Medium, weight 500, line-height 38px)**:
   - Drawer Law Title: `"Federal Decree-Law No. (42) of 2022 Promulgating the Civil Procedure Code"` (`#f4f7f6`)
4. **18px (Regular, weight 400)**:
   - Drawer Category: `"Healthcare"` (`#f2eadd`)
5. **17px (Regular, weight 400)**:
   - Drawer Subtitle: `"Sentiment Rate"` (`#f2ebda`)
6. **16px (Semi-Bold, weight 600)**:
   - Branding: `"Control AI"` (`#d9bd86`)
   - Drawer Section Header: `"RI Analysis"` (`#f4f7f6`)
7. **14px (Regular / Medium, weight 400-500)**:
   - Top Buttons: `"+ New Simulation"` (`#f3f3f0`)
   - Action Buttons: `"Explore details"` (`#fafaf7`)
   - Drawer Card Header: `"Propose De-regulation"`
   - Central Source Law Title: `"Federal Decree-Law No. 45 of 2021"`
8. **13px (Regular, weight 400)**:
   - Platform Subtitle: `"Policy Platform"` (`#e9eceb`)
   - Category Hub labels: `"Services"`, `"Entities"`, `"Related Federal Laws"`, `"KPIs"`
   - Leaf Node Titles: `"Federal Decree..."`, `"Bank of New Yo..."`, `"Emirates Citigro..."`
   - Metadata: `"The last update on this law was listed on listed on 01 Oct 2024"`
   - Trend subtext: `"-4% Last month"`
   - Drawer Metric labels: `"Public Complains & Recommendations"`
   - Chat agent selector: `"My agent"`
9. **12px (Regular / Semi-Bold)**:
   - Status Badge: `"Active"` (`#f2f6f2`)
   - Top Overview Pill: `"KPI overview UAE law 42/2022 public complaints and AI analysis"`
   - Gap Pills: `"GAPS 12 %"`
10. **11px (Regular)**:
    - Sentiment Badge: `"Moderate"` (`#fff9eb`)
11. **10px (Regular)**:
    - Applied Notification Badges: `"5"`, `"2"` (`text-white`)
    - Orb Gap Tag: `"GAPS 12"`

---

## 4. Mathematical Formula for the Organic Optic-Fiber Fan Curves

### A. Graph Coordinate Blueprint
- **Root Active Hub (Card #3 - "Related Federal Laws")**:
  - Card bounding box: `x = 402px`, `y = 504px`, `width = 180px`, `height = 64px`
  - Right-edge connection anchor `P0`:
    $$\mathbf{P_0} = (x_0, y_0) = (582, 537)$$
- **Selected Connection Optical Flare**:
  - Elliptical focal glow centered at `(582, 537)`:
    $$\text{radius}_x = 38\text{px}, \quad \text{radius}_y = 20\text{px}$$
    Gradient: `#ffffff` $\to$ `#a0d9bc` $\to$ `rgba(84, 155, 159, 0.4)` $\to$ `transparent`.

### B. Vertical Arc Leaf Node Distribution
There are exactly **19 leaf nodes** ($i = 1, \dots, 19$) positioned along an organic vertical arc spanning from $y_1 = 96\text{px}$ to $y_{19} = 936\text{px}$:
- Vertical step $\Delta y \approx 46.67\text{px}$ ($44\text{px} - 50\text{px}$ in Figma):
  $$y_i = 96 + (i - 1) \times 46.67$$
- Horizontal projection $x_i$ follows an elliptical/parabolic bow peaking at the equator ($i = 10$, $y_{10} = 514\text{px}$, $x_{10} = 914\text{px}$):
  $$x_i = x_{\text{apex}} - A \times \left( \frac{y_i - y_{\text{mid}}}{y_{\text{span}} / 2} \right)^2$$
  where:
  - $x_{\text{apex}} = 914\text{px}$
  - $y_{\text{mid}} = 514\text{px}$, $y_{\text{span}} = 840\text{px}$
  - For top half ($y_i \le y_{\text{mid}}$): $A_{\text{top}} = 264\text{px} \implies x_1 = 650\text{px}$
  - For bottom half ($y_i > y_{\text{mid}}$): $A_{\text{bottom}} = 183\text{px} \implies x_{19} = 731\text{px}$

Exact discrete node dot coordinates extracted from Figma:
1. `(650, 96)` - "Federal Decree..." (Gaps 12%)
2. `(745, 141)` - "Bank of New Yo..." (Gaps 12%)
3. `(796, 185)` - "Federal Law No..." (Gaps 12%)
4. `(832, 235)` - "Bank of New Yo..." (Gaps 25%)
5. `(860, 279)` - "Bank of New Yo..." (Gaps 12%)
6. `(878, 326)` - "Emirates Citigro..." (Gaps 8%)
7. `(895, 373)` - "Bank of New Yo..." (Gaps 19%)
8. `(903, 420)` - "Capital One Emi..." (Gaps 8%)
9. `(912, 467)` - "Bank of New Yo..." (Gaps 16%)
10. `(914, 514)` - "Bank of New Yo..." (Gaps 7%) [APEX]
11. `(910, 561)` - "East West Emir..." (Gaps 11%)
12. `(906, 608)` - "Emirates Citigro..." (Gaps 12%)
13. `(900, 655)` - "Bank of New Yo..." (Gaps 14%)
14. `(887, 702)` - "Bank of New Yo..." (Gaps 12%)
15. `(871, 749)` - "Capital One Emi..." (Gaps 12%)
16. `(847, 796)` - "Bank of New Yo..." (Gaps 14%)
17. `(817, 843)` - "East West Emir..." (Gaps 12%)
18. `(773, 890)` - "Federal Decree..." (Gaps 12%)
19. `(731, 936)` - "Bank of New Yo..." (Gaps 12%)

### C. Cubic Bezier Optical Fiber Equation
For any leaf node target $\mathbf{P_3} = (x_i, y_i)$:
$$\mathbf{C}(t) = (1-t)^3 \mathbf{P_0} + 3(1-t)^2 t \mathbf{P_1} + 3(1-t) t^2 \mathbf{P_2} + t^3 \mathbf{P_3}, \quad t \in [0, 1]$$
where the control points enforce smooth horizontal tangency:
$$\mathbf{P_0} = (582, 537)$$
$$\mathbf{P_1} = (582 + 0.45 \times (x_i - 582), 537)$$
$$\mathbf{P_2} = (x_i - 0.35 \times (x_i - 582), y_i)$$
$$\mathbf{P_3} = (x_i, y_i)$$

In SVG path notation:
```javascript
const dx = xi - 582;
const pathD = `M 582 537 C ${582 + 0.45 * dx} 537, ${xi - 0.35 * dx} ${yi}, ${xi} ${yi}`;
```

### D. Depth-of-Field & Atmospheric Luster
To replicate the optic-fiber glow and depth-of-field:
- **Central Equator Lines** ($i = 8 \dots 12$):
  - Stroke: `rgba(160, 230, 220, 0.9)`
  - Width: `1.5px`
  - SVG Glow Filter: `feGaussianBlur stdDeviation="2.5"`
- **Intermediate Lines** ($i = 4 \dots 7$, $13 \dots 16$):
  - Stroke: `rgba(84, 155, 159, 0.55)`
  - Width: `1.2px`
- **Outer Peripheral Lines** ($i = 1 \dots 3$, $17 \dots 19$):
  - Stroke: `rgba(84, 155, 159, 0.22)`
  - Width: `1.0px`
  - Corresponding leaf node text opacity: `0.35 - 0.5`

---

## 5. Dot-Matrix Sentiment Histogram Equalizer
Inside the Sentiment Rate card ($460\text{px} \times 142\text{px}$):
- **Matrix Geometry**:
  - 35 vertical columns spaced by $\Delta x = 6.0\text{px}$ (from $x = 230\text{px}$ to $434\text{px}$).
  - Dot diameter: $2.4\text{px}$ circular beads (`border-radius: 50%`).
  - Vertical dot pitch: $\Delta y = 6.0\text{px}$ ($y = 109, 103, 97, 91, 85, 79, 73, 67\text{px}$).
- **Column Heights (Dots per column)**:
  ```javascript
  const histogramHeights = [
    2, 4, 2, 4, 5, 3, 5, 6, 4, 6, 7, 5, 7, 8, 6, 7, 8, 6, 7, 8, 6, 7, 8, 5, 6, 7, 4, 5, 6, 3, 4, 5, 2, 3, 3
  ];
  ```
- **Dot Styling**:
  - Lower dots: `#7a9493` / `rgba(255, 255, 255, 0.45)`
  - Crest / peak dots: glowing amber `#c5a56e` / `#e57373` (subtle pulse effect).

---

## 6. Icon Specifications & Inlining Guide

All icons can be rendered with clean, high-fidelity inline SVGs:

1. **Logo Emblem (Control AI)**:
   A four-pointed radiant compass star with soft rounded notches, stroke/fill `#d9bd86`.
2. **Network View Icon**:
   3 circular nodes connected by 3 line segments (graph triangle).
3. **Hierarchy View Icon**:
   Top parent node with branching tree lines to 2 child nodes.
4. **Zoom In / Zoom Out**:
   Thin rounded pill/circle with `+` and `-` glyphs (`#e9eceb`).
5. **Filter Funnel**:
   Inverted trapezoid funnel with short vertical spout.
6. **Sort**:
   Opposing vertical arrows ($\uparrow \downarrow$).
7. **Fit View**:
   Square framing corners (`[ ]`).
8. **Healthcare Caduceus**:
   Vertical staff with entwined double serpent wings, antique gold `#d9bd86`.
9. **Declining Sentiment Arrow**:
   Gentle downward sinusoidal wavy arrow with down-right arrowhead, salmon-red `#e57373`.
10. **3-Step Risk Level Indicator**:
    Three $6\text{px} \times 6\text{px}$ rounded rectangles side by side:
    - Tick 1 (Left): Red `#d9534f`
    - Tick 2 (Center): Amber `#c5a56e`
    - Tick 3 (Right): Green `#27ae60` (or dim `#1a393d` if inactive)
11. **Checkmark Circle**:
    Circular ring with clean interior checkmark.
12. **Document / Regulation**:
    Rectangular document with folded top-right dog-ear and 3 horizontal lines.
13. **Services Network Cube**:
    Isometric cube / hex node mesh.
14. **Chat Send Arrow**:
    Diagonal paper airplane / angled arrow inside a circular teal button (`#183e42` / `#549b9f`).

---

## 7. Interactive State Machine for UI Builder

1. **Category Hub Selection**:
   - Default active: Hub 3 ("Related Federal Laws").
   - Clicking any of the 5 Hubs (Services, Entities, Related Federal Laws, KPIs, Sub-Laws):
     1. Transitions active gold border & focal glow to that hub card.
     2. Retargets the optic-fiber fan curves to originate from the new hub's right edge (`402 + 180 = 582`, $y = y_{\text{hub}} + 30$).
     3. Smoothly animates leaf node titles and gap pills to correspond to that category.
     4. Updates the Right Inspector Drawer with that hub's detailed analysis.
2. **Leaf Node Selection**:
   - Clicking any of the 19 leaf nodes:
     1. Highlights that node's dot with an amber pulse ring (`0 0 12px #d9bd86`).
     2. Highlights the connecting optic-fiber curve to full opacity white-cyan (`#ffffff` / `#a0d9bc`).
     3. Populates the Right Inspector Drawer title, sentiment score, and risk indicators with the selected law's data.
3. **OmniHarness Live Integration**:
   - Connects to `/ws/graph` for real-time node state broadcasts.
   - Updates task claims, agent execution logs, and blast-radius notifications live.
