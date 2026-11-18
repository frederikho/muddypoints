"""Generates the SVG slides of the demo board (frontend/img/demo/slide-N.svg). Plain Python, no dependencies.

    python3 scripts/make_demo_slides.py
"""
import math
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "frontend" / "img" / "demo"
W, H = 1600, 900
INK, MUTED, ACCENT, ORANGE, GREEN, RED = "#1c2330", "#667085", "#4f46e5", "#e07a1f", "#12805c", "#d92d20"
SANS = "Helvetica, Arial, sans-serif"
SERIF = "Georgia, 'Times New Roman', serif"


def page(n, title, body):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="{SANS}">
<rect width="{W}" height="{H}" fill="#ffffff"/>
<rect width="{W}" height="14" fill="{ACCENT}"/>
<text x="90" y="150" font-size="68" font-weight="800" fill="{INK}">{title}</text>
{body}
<text x="90" y="858" font-size="24" fill="{MUTED}">Statistics I · Lecture 4</text>
<text x="{W - 90}" y="858" font-size="24" fill="{MUTED}" text-anchor="end">{n}</text>
</svg>
'''


def bullets(items, x=90, y=270, size=40, gap=78):
    out = []
    for i, t in enumerate(items):
        out.append(f'<circle cx="{x + 10}" cy="{y + i * gap - 13}" r="8" fill="{ACCENT}"/>'
                   f'<text x="{x + 40}" y="{y + i * gap}" font-size="{size}" fill="{INK}">{t}</text>')
    return "".join(out)


def curve(f, x0, x1, ax0, ax1, base, scale, steps=120):
    pts = []
    for i in range(steps + 1):
        x = x0 + (x1 - x0) * i / steps
        pts.append(f"{ax0 + (ax1 - ax0) * i / steps:.1f},{base - f(x) * scale:.1f}")
    return "M" + " L".join(pts)


def slide1():
    agenda = bullets(["Sample variance and why we divide by n − 1", "Standard error and the Central Limit Theorem",
                      "Confidence intervals", "The t-distribution", "Worked example: exam scores"], y=470, size=36, gap=70)
    body = (f'<text x="90" y="260" font-size="40" fill="{MUTED}">Variance, standard error and confidence intervals</text>'
            f'<text x="90" y="360" font-size="30" font-weight="700" fill="{ACCENT}" letter-spacing="2">TODAY</text>' + agenda)
    return page(1, "Statistics I", body).replace("Statistics I · Lecture 4", "Prof. Alice Keller · Spring term")


def slide2():
    xs = [4, 6, 7, 7.5, 9, 10, 11.5, 12, 14, 16]
    mean = sum(xs) / len(xs)
    ax = lambda v: 900 + v * 32
    dots = "".join(f'<circle cx="{ax(v):.0f}" cy="{640}" r="14" fill="{ACCENT}" opacity=".85"/>' for v in xs)
    lines = "".join(f'<line x1="{ax(v):.0f}" y1="640" x2="{ax(mean):.0f}" y2="640" stroke="{ORANGE}" stroke-width="3" stroke-dasharray="6 6"/>' for v in xs)
    body = (f'<text x="90" y="290" font-size="64" font-style="italic" font-family="{SERIF}" fill="{INK}">s² = Σ (xᵢ − x̄)² / (n − 1)</text>'
            + bullets(["x̄ is the sample mean", "Divide by n − 1, not n (Bessel’s correction)", "s = √s² is in the same unit as the data"], y=420, size=36, gap=64)
            + f'<line x1="{ax(0)}" y1="700" x2="{ax(18)}" y2="700" stroke="{MUTED}" stroke-width="3"/>'
            + lines + dots
            + f'<line x1="{ax(mean):.0f}" y1="590" x2="{ax(mean):.0f}" y2="700" stroke="{ORANGE}" stroke-width="5"/>'
            + f'<text x="{ax(mean):.0f}" y="570" font-size="30" fill="{ORANGE}" text-anchor="middle" font-style="italic" font-family="{SERIF}">x̄</text>'
            + f'<text x="{ax(0)}" y="760" font-size="26" fill="{MUTED}">Distances to the mean, squared and summed</text>')
    return page(2, "Sample variance", body)


def slide3():
    # histogram of a sampling distribution (bell shaped)
    bars, n = [], 15
    for i in range(n):
        x = (i - n / 2 + .5) / (n / 5)
        h = math.exp(-x * x / 2) * 330
        bars.append(f'<rect x="{1040 + i * 36}" y="{720 - h:.0f}" width="32" height="{h:.0f}" fill="{ACCENT}" opacity=".8" rx="4"/>')
    body = (f'<text x="90" y="290" font-size="64" font-style="italic" font-family="{SERIF}" fill="{INK}">SE = s / √n</text>'
            + bullets(["Standard error: spread of the sample mean", "Bigger samples → smaller SE", "Central Limit Theorem: x̄ is roughly normal for large n"], y=420, size=36, gap=70)
            + "".join(bars) + f'<line x1="1020" y1="720" x2="1580" y2="720" stroke="{MUTED}" stroke-width="3"/>'
            + f'<text x="1300" y="770" font-size="26" fill="{MUTED}" text-anchor="middle">distribution of x̄ over many samples</text>')
    return page(3, "Standard error &amp; the CLT", body)


def slide4():
    rows = [(60, 82, True), (55, 78, True), (66, 90, False), (50, 73, True), (58, 80, True), (62, 86, True), (44, 66, False), (57, 79, True)]
    sx = lambda v: 960 + v * 6.5
    out = [f'<line x1="{sx(70)}" y1="320" x2="{sx(70)}" y2="740" stroke="{GREEN}" stroke-width="4" stroke-dasharray="10 8"/>',
           f'<text x="{sx(70)}" y="300" font-size="26" fill="{GREEN}" text-anchor="middle">true mean μ</text>']
    for i, (a, b, hit) in enumerate(rows):
        y = 360 + i * 48
        c = ACCENT if hit else RED
        out.append(f'<line x1="{sx(a)}" y1="{y}" x2="{sx(b)}" y2="{y}" stroke="{c}" stroke-width="8" stroke-linecap="round"/>'
                   f'<circle cx="{sx((a + b) / 2)}" cy="{y}" r="9" fill="{c}"/>')
    body = (f'<text x="90" y="290" font-size="60" font-style="italic" font-family="{SERIF}" fill="{INK}">x̄ ± t* · s / √n</text>'
            + bullets(["95 % interval: it catches μ in 95 % of repeated samples", "It is not: “95 % probability that μ is in this interval”", "Wider interval → more confidence, less precision"], y=420, size=34, gap=84)
            + "".join(out) + f'<text x="1330" y="800" font-size="26" fill="{MUTED}" text-anchor="middle">8 samples, 2 intervals miss μ (red)</text>')
    return page(4, "Confidence intervals", body)


def slide5():
    normal = lambda x: math.exp(-x * x / 2) / math.sqrt(2 * math.pi)
    t3 = lambda x: math.gamma(2) / (math.sqrt(3 * math.pi) * math.gamma(1.5)) * (1 + x * x / 3) ** -2
    base, ax0, ax1 = 740, 860, 1500
    body = (bullets(["Used when σ is unknown (we estimate it by s)", "Heavier tails than the normal distribution", "Degrees of freedom: df = n − 1", "More data → t gets closer to normal"], y=290, size=36, gap=80)
            + f'<line x1="{ax0}" y1="{base}" x2="{ax1}" y2="{base}" stroke="{MUTED}" stroke-width="3"/>'
            + f'<path d="{curve(normal, -4, 4, ax0, ax1, base, 900)}" fill="none" stroke="{MUTED}" stroke-width="5"/>'
            + f'<path d="{curve(t3, -4, 4, ax0, ax1, base, 900)}" fill="none" stroke="{ACCENT}" stroke-width="6"/>'
            + f'<text x="1330" y="430" font-size="28" fill="{MUTED}">normal</text>'
            + f'<text x="1330" y="470" font-size="28" fill="{ACCENT}" font-weight="700">t (df = 3)</text>')
    return page(5, "The t-distribution", body)


def slide6():
    body = (f'<text x="90" y="260" font-size="34" fill="{MUTED}">Exam scores of n = 25 students</text>'
            f'<text x="90" y="360" font-size="52" font-style="italic" font-family="{SERIF}" fill="{INK}">x̄ = 68.4,   s = 11.2</text>'
            f'<text x="90" y="470" font-size="52" font-style="italic" font-family="{SERIF}" fill="{INK}">SE = 11.2 / √25 = 2.24</text>'
            f'<text x="90" y="580" font-size="52" font-style="italic" font-family="{SERIF}" fill="{INK}">t* (df = 24) = 2.064</text>'
            f'<rect x="90" y="650" width="900" height="110" rx="16" fill="#eef0ff"/>'
            f'<text x="130" y="722" font-size="52" font-style="italic" font-family="{SERIF}" fill="{ACCENT}">95 % CI: [63.8 ; 73.0]</text>'
            f'<line x1="1120" y1="560" x2="1480" y2="560" stroke="{MUTED}" stroke-width="3"/>'
            f'<line x1="1180" y1="560" x2="1420" y2="560" stroke="{ACCENT}" stroke-width="10" stroke-linecap="round"/>'
            f'<circle cx="1300" cy="560" r="14" fill="{ACCENT}"/>'
            f'<text x="1300" y="520" font-size="26" fill="{INK}" text-anchor="middle">68.4</text>'
            f'<text x="1180" y="610" font-size="24" fill="{MUTED}" text-anchor="middle">63.8</text>'
            f'<text x="1420" y="610" font-size="24" fill="{MUTED}" text-anchor="middle">73.0</text>')
    return page(6, "Worked example", body)


for i, fn in enumerate([slide1, slide2, slide3, slide4, slide5, slide6], 1):
    (OUT / f"slide-{i}.svg").write_text(fn(), encoding="utf-8")
print("wrote", len(list(OUT.glob("slide-*.svg"))), "slides to", OUT)
