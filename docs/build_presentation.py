"""Incident Commander — PPTX matching docs/deck.html (13 slides)."""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

OUT = Path(__file__).with_name("Incident-Commander-deck.pptx")

BG = RGBColor(30, 70, 104)
PANEL = RGBColor(36, 82, 118)
INK = RGBColor(246, 251, 255)
MUTED = RGBColor(194, 212, 228)
LINE = RGBColor(90, 140, 168)
ACCENT = RGBColor(94, 224, 240)
DARK = RGBColor(8, 48, 56)
WHITE = RGBColor(255, 255, 255)
OK = RGBColor(94, 211, 154)
BAD = RGBColor(255, 122, 122)
WARN = RGBColor(255, 196, 90)
GAP = RGBColor(62, 52, 28)
FONT = "Calibri"
MONO = "Consolas"
W, H = Inches(13.333), Inches(7.5)


def rgb_fill(shape, color):
    shape.fill.solid()
    shape.fill.fore_color.rgb = color


def no_line(shape):
    shape.line.fill.background()


def line_color(shape, color, pt=1.0):
    shape.line.color.rgb = color
    shape.line.width = Pt(pt)


def add_text(slide, l, t, w, h, text, size=14, bold=False, color=INK,
             align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, font=FONT):
    box = slide.shapes.add_textbox(l, t, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    try:
        tf._txBody.bodyPr.set("anchor", {
            MSO_ANCHOR.TOP: "t", MSO_ANCHOR.MIDDLE: "ctr", MSO_ANCHOR.BOTTOM: "b"
        }.get(anchor, "t"))
    except Exception:
        pass
    p = tf.paragraphs[0]
    p.alignment = align
    r = p.add_run()
    r.text = text
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.color.rgb = color
    r.font.name = font
    return box


def rect(slide, l, t, w, h, fill, line=None, radius=0.08):
    sh = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, l, t, w, h)
    rgb_fill(sh, fill)
    if line is None:
        no_line(sh)
    else:
        line_color(sh, line, 1.0)
    try:
        sh.adjustments[0] = radius
    except Exception:
        pass
    return sh


def decorate(slide, n):
    bg = slide.background
    bg.fill.solid()
    bg.fill.fore_color.rgb = BG
    add_text(slide, Inches(0.5), Inches(7.18), Inches(8.5), Inches(0.22),
             "INCIDENT COMMANDER  ·  investigation policy", 10, False, MUTED)
    add_text(slide, Inches(10.6), Inches(7.18), Inches(2.2), Inches(0.22),
             f"{n:02d}  /  13", 10, False, MUTED, PP_ALIGN.RIGHT, font=MONO)


def kicker(slide, text):
    add_text(slide, Inches(0.5), Inches(0.2), Inches(12.3), Inches(0.26),
             text.upper(), 12, True, ACCENT, font=MONO)


def title(slide, text, size=26):
    add_text(slide, Inches(0.5), Inches(0.44), Inches(12.3), Inches(0.7),
             text, size, True, WHITE)


def take(slide, text, y=6.35):
    rect(slide, Inches(0.5), Inches(y), Inches(12.3), Inches(0.72), PANEL, LINE, 0.08)
    add_text(slide, Inches(0.65), Inches(y + 0.08), Inches(12.0), Inches(0.56),
             text, 14, True, WHITE)


def node(slide, l, t, w, h, head, body="", variant="neutral"):
    fills = {"neutral": PANEL, "accent": ACCENT, "gap": GAP}
    lines = {"neutral": LINE, "accent": ACCENT, "gap": WARN}
    hc = {"neutral": MUTED, "accent": DARK, "gap": WARN}
    bc = {"neutral": INK, "accent": DARK, "gap": WHITE}
    rect(slide, Inches(l), Inches(t), Inches(w), Inches(h), fills[variant], lines[variant], 0.08)
    add_text(slide, Inches(l + 0.14), Inches(t + 0.08), Inches(w - 0.28), Inches(0.26),
             head, 11, True, hc[variant])
    if body:
        add_text(slide, Inches(l + 0.14), Inches(t + 0.34), Inches(w - 0.28), Inches(h - 0.42),
                 body, 13, False, bc[variant])


def down(slide, x, y):
    add_text(slide, Inches(x - 0.15), Inches(y), Inches(0.3), Inches(0.22),
             "↓", 12, True, ACCENT, PP_ALIGN.CENTER)


def s01(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    decorate(s, 1)
    kicker(s, "01  ·  problem")
    title(s, "When production breaks, finding the cause is not enough.")
    add_text(s, Inches(0.5), Inches(1.12), Inches(12.3), Inches(0.32),
             "The real challenge is deciding what to investigate next.", 16, False, MUTED)
    node(s, 4.15, 1.5, 5.0, 0.42, "PRODUCTION INCIDENT")
    down(s, 6.65, 1.92)
    node(s, 3.7, 2.12, 5.9, 0.7, "MULTIPLE PLAUSIBLE ROOT CAUSES",
         "First-screen evidence still fits more than one story.")
    down(s, 6.65, 2.82)
    node(s, 2.2, 3.02, 3.7, 0.95, "HYPOTHESIS H1", "")
    add_text(s, Inches(2.35), Inches(3.35), Inches(3.4), Inches(0.4), "48%", 22, True, WHITE, font=MONO)
    add_text(s, Inches(2.35), Inches(3.72), Inches(3.4), Inches(0.2), "illustrative split", 11, False, MUTED)
    node(s, 7.4, 3.02, 3.7, 0.95, "HYPOTHESIS H2", "")
    add_text(s, Inches(7.55), Inches(3.35), Inches(3.4), Inches(0.4), "41%", 22, True, WHITE, font=MONO)
    add_text(s, Inches(7.55), Inches(3.72), Inches(3.4), Inches(0.2), "illustrative split", 11, False, MUTED)
    down(s, 6.65, 3.97)
    node(s, 3.5, 4.18, 6.3, 0.95, "THE GAP", "What should we investigate next?", "gap")
    down(s, 6.65, 5.13)
    node(s, 4.15, 5.32, 5.0, 0.48, "NEXT BEST EVIDENCE", variant="accent")
    take(s, "Traditional RCA: which cause is most likely?    Ours: what evidence should we collect next to reduce uncertainty?")


def s02(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    decorate(s, 2)
    kicker(s, "02  ·  positioning")
    title(s, "From observability to investigation")
    add_text(s, Inches(0.5), Inches(1.1), Inches(8.2), Inches(0.3),
             "Observability shows what is happening. We focus on what to investigate next.", 15, False, MUTED)
    add_text(s, Inches(9.0), Inches(1.1), Inches(3.8), Inches(1.1),
             "Modern observability already provides visibility, correlation, RCA, and AI-assisted analysis. That stack is assumed — not replaced.", 12, False, MUTED)
    node(s, 3.4, 1.5, 6.5, 0.42, "EXISTING OBSERVABILITY")
    for i, n in enumerate(("METRICS", "LOGS", "TRACES")):
        node(s, 3.4 + i * 2.2, 2.08, 2.05, 0.42, n)
    add_text(s, Inches(3.4), Inches(2.55), Inches(6.5), Inches(0.28),
             "“What is happening?”", 14, True, MUTED, PP_ALIGN.CENTER)
    node(s, 3.2, 2.88, 6.9, 2.35, "INVESTIGATION LAYER", "Competing hypotheses")
    rect(s, Inches(4.45), Inches(3.7), Inches(4.4), Inches(0.7), ACCENT, ACCENT, 0.08)
    add_text(s, Inches(4.45), Inches(3.76), Inches(4.4), Inches(0.32),
             "Evidence selection", 16, True, DARK, PP_ALIGN.CENTER)
    add_text(s, Inches(4.45), Inches(4.08), Inches(4.4), Inches(0.26),
             "under uncertainty", 11, False, DARK, PP_ALIGN.CENTER)
    add_text(s, Inches(3.2), Inches(4.55), Inches(6.9), Inches(0.32),
             "Belief update", 13, False, MUTED, PP_ALIGN.CENTER)
    add_text(s, Inches(3.2), Inches(5.35), Inches(6.9), Inches(0.35),
             "“What should we investigate next?”", 16, True, ACCENT, PP_ALIGN.CENTER)
    take(s, "We are not replacing observability — we build an investigation layer on top of it.")


def s03(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    decorate(s, 3)
    kicker(s, "03  ·  research gap")
    title(s, "Where current AI-SRE research still falls short")
    add_text(s, Inches(0.5), Inches(1.1), Inches(12.3), Inches(0.3),
             "Agents can reason about incidents. Evaluations still report recurring process failures.", 15, False, MUTED)
    node(s, 0.5, 1.5, 6.0, 3.5, "FINAL DIAGNOSIS",
         "“Can the agent identify the root cause?”\n\n"
         "·  Incomplete exploration\n·  Hallucinated data interpretation\n"
         "·  Inefficient investigation\n·  Large end-to-end performance gaps")
    node(s, 6.8, 1.5, 6.0, 3.5, "INVESTIGATION PROCESS",
         "“Can the agent choose the right evidence to collect next?”\n\n"
         "Quality of the path, not only the final label.", "accent")
    rect(s, Inches(0.5), Inches(5.15), Inches(12.3), Inches(1.15), GAP, WARN, 0.08)
    add_text(s, Inches(0.7), Inches(5.25), Inches(12.0), Inches(0.25), "RESEARCH GAP", 11, True, WARN, font=MONO)
    add_text(s, Inches(0.7), Inches(5.52), Inches(12.0), Inches(0.65),
             "Current systems often emphasize the quality of the final diagnosis.\n"
             "We focus on the quality of the investigation process that leads to it.", 15, True, WHITE)


def s04(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    decorate(s, 4)
    kicker(s, "04  ·  where it fits")
    title(s, "A production microservices incident")
    node(s, 0.5, 1.2, 7.4, 0.7, "PRODUCTION ENVIRONMENT",
         "Cloud · Kubernetes · microservices — the existing stack")
    for i, n in enumerate(("Metrics", "Logs", "Traces")):
        node(s, 0.5 + i * 2.5, 2.05, 2.35, 0.42, n.upper())
    node(s, 0.5, 2.62, 7.4, 0.85, "OUR SYSTEM", "What should I investigate next?", "accent")
    node(s, 0.5, 3.6, 7.4, 0.7, "RECOMMENDED CHECK  →  SRE / IC  →  NEW EVIDENCE  →  UPDATED BELIEFS")
    node(s, 8.15, 1.2, 4.65, 1.15, "ON-CALL SRE",
         "Where to start, instead of picking the next check by habit.")
    node(s, 8.15, 2.5, 4.65, 1.15, "INCIDENT COMMANDER",
         "The reason for the recommended check. Human stays in the loop.")
    node(s, 8.15, 3.8, 4.65, 1.15, "COMPLEX MICROSERVICES",
         "When several causes still fit a large evidence surface.")
    take(s, "Observability provides the evidence. Our system helps decide which evidence to investigate next.")


def s05(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    decorate(s, 5)
    kicker(s, "05  ·  difference")
    title(s, "From root-cause ranking to investigation decisions")
    rect(s, Inches(0.5), Inches(1.2), Inches(6.3), Inches(4.55), PANEL, LINE, 0.08)
    add_text(s, Inches(0.7), Inches(1.32), Inches(2.9), Inches(0.25), "TRADITIONAL RCA", 11, True, MUTED)
    add_text(s, Inches(3.7), Inches(1.32), Inches(2.9), Inches(0.25), "OUR APPROACH", 11, True, ACCENT)
    rows = [
        ("Rank causes", "Maintain competing hypotheses"),
        ("Most likely cause", "Identify remaining uncertainty"),
        ("Answer-oriented", "Investigation-oriented"),
        ("Evidence as input", "Evidence as a decision signal"),
        ("Often one-shot", "Sequential"),
        ("What is the cause?", "What should we check next?"),
    ]
    y = 1.7
    for a, b in rows:
        add_text(s, Inches(0.7), Inches(y), Inches(2.85), Inches(0.5), a, 13, False, MUTED)
        add_text(s, Inches(3.7), Inches(y), Inches(2.9), Inches(0.5), b, 13, True, INK)
        y += 0.62
    node(s, 7.05, 1.2, 5.75, 0.7, "H1", "predicts wait / latency rises first")
    node(s, 7.05, 2.0, 5.75, 0.7, "H2", "predicts wait / latency rises later")
    node(s, 7.05, 2.85, 5.75, 0.75, "DIFFERENT PREDICTIONS", "The check they disagree about", "gap")
    node(s, 7.05, 3.75, 5.75, 0.95, "CHECK WAIT-TIME BEHAVIOR", "Then update · then the next check", "accent")
    take(s, "The system does not stop at “which hypothesis is most likely?” — it asks what evidence would distinguish the remaining hypotheses.")


def s06(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    decorate(s, 6)
    kicker(s, "06  ·  how it works")
    title(s, "The investigation is iterative")
    add_text(s, Inches(0.5), Inches(1.1), Inches(12.3), Inches(0.3),
             "Every observation changes what should be investigated next.", 16, False, MUTED)
    steps = [
        "Incident", "Competing hypotheses", "Current beliefs", "Candidate probes",
        "Select next investigation", "Collect evidence", "Update beliefs", "Repeat until convergence",
    ]
    y = 1.5
    for i, name in enumerate(steps):
        v = "accent" if name.startswith("Select") else "neutral"
        node(s, 0.5, y, 6.3, 0.48, name, variant=v)
        y += 0.55
    node(s, 7.1, 1.5, 5.7, 1.7, "NOT ONE-SHOT RCA",
         "The loop returns to beliefs. A new observation can change the next check.", "accent")
    node(s, 7.1, 3.4, 5.7, 2.0, "STOPPING — IN THIS PROTOTYPE",
         "Continue while residual uncertainty remains and a discriminating check still exists.\n\nτ = 0.15  ·  max 4 probes  ·  45s wall")


def s07(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    decorate(s, 7)
    kicker(s, "07  ·  what we built")
    title(s, "A working offline investigation engine")
    rect(s, Inches(0.5), Inches(1.2), Inches(3.55), Inches(1.35), PANEL, ACCENT, 0.08)
    add_text(s, Inches(0.5), Inches(1.28), Inches(3.55), Inches(0.75),
             "8", 36, True, ACCENT, PP_ALIGN.CENTER, font=MONO)
    add_text(s, Inches(0.6), Inches(2.05), Inches(3.35), Inches(0.35),
             "Recorded incident bundles", 12, False, MUTED, PP_ALIGN.CENTER)
    metrics = [("3", "Analytical lenses"), ("4", "Max probes / case"),
               ("45s", "Wall-time budget"), ("0.15", "Convergence τ")]
    positions = [(0.5, 2.7), (2.3, 2.7), (0.5, 4.05), (2.3, 4.05)]
    for (x, y), (n, lab) in zip(positions, metrics):
        rect(s, Inches(x), Inches(y), Inches(1.75), Inches(1.2), PANEL, LINE, 0.08)
        add_text(s, Inches(x), Inches(y + 0.12), Inches(1.75), Inches(0.5),
                 n, 20, True, WHITE, PP_ALIGN.CENTER, font=MONO)
        add_text(s, Inches(x + 0.08), Inches(y + 0.65), Inches(1.6), Inches(0.45),
                 lab, 11, False, MUTED, PP_ALIGN.CENTER)
    rect(s, Inches(0.5), Inches(5.4), Inches(3.55), Inches(0.75), PANEL, LINE, 0.08)
    add_text(s, Inches(0.5), Inches(5.48), Inches(3.55), Inches(0.32),
             "ON / OFF", 16, True, ACCENT, PP_ALIGN.CENTER, font=MONO)
    add_text(s, Inches(0.5), Inches(5.82), Inches(3.55), Inches(0.25),
             "Probe ablation harness", 11, False, MUTED, PP_ALIGN.CENTER)
    pipe = [
        ("01", "Incident bundle", False),
        ("02", "Seeded hypotheses", False),
        ("03", "Change · telemetry · history lenses", False),
        ("04", "Belief reconciliation", False),
        ("05", "Probe selection — discrimination / cost", True),
        ("06", "Evidence from the case fixture", False),
        ("07", "Belief update", False),
        ("08", "Convergence", False),
    ]
    y = 1.2
    for idx, name, on in pipe:
        fill, fg, border = (ACCENT, DARK, ACCENT) if on else (PANEL, INK, LINE)
        rect(s, Inches(4.3), Inches(y), Inches(8.5), Inches(0.52), fill, border, 0.08)
        add_text(s, Inches(4.45), Inches(y + 0.1), Inches(0.5), Inches(0.32),
                 idx, 12, True, ACCENT if not on else DARK, font=MONO)
        add_text(s, Inches(5.05), Inches(y + 0.1), Inches(7.5), Inches(0.32), name, 14, True, fg)
        y += 0.58
    take(s, "Working software over a recorded corpus — not a live observability deployment. Hypotheses and the probe catalog are seeded.")


def s08(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    decorate(s, 8)
    kicker(s, "08  ·  decision mechanism")
    title(s, "Select the check the remaining stories disagree about")
    node(s, 0.5, 1.25, 6.0, 1.2, "H1", "")
    add_text(s, Inches(0.7), Inches(1.6), Inches(5.6), Inches(0.45), "rises first", 22, True, OK, font=MONO)
    add_text(s, Inches(0.7), Inches(2.08), Inches(5.6), Inches(0.25), "connection_wait_time", 13, False, MUTED)
    node(s, 6.8, 1.25, 6.0, 1.2, "H2", "")
    add_text(s, Inches(7.0), Inches(1.6), Inches(5.6), Inches(0.45), "rises later", 22, True, BAD, font=MONO)
    add_text(s, Inches(7.0), Inches(2.08), Inches(5.6), Inches(0.25), "connection_wait_time", 13, False, MUTED)
    node(s, 3.15, 2.65, 7.0, 0.7, "DIFFERENT PREDICTIONS", "Which probe separates them?", "gap")
    node(s, 2.4, 3.5, 8.5, 0.7, "CANDIDATE PROBES", "P1 · P2 · P3 · P4 — skip checks both hypotheses would survive")
    node(s, 3.15, 4.4, 7.0, 0.85, "NEXT PROBE", "argmax( discrimination / cost_ms )", "accent")
    take(s, "Current prototype: heuristic discrimination-per-cost. Not formal information-theoretic expected information gain.")


def s09(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    decorate(s, 9)
    kicker(s, "09  ·  concrete case")
    title(s, "INC-4471")
    add_text(s, Inches(0.5), Inches(1.1), Inches(12.3), Inches(0.3),
             "Checkout 5xx after a deploy. Two stories still fit the first screen.", 16, False, MUTED)
    rows = [
        ("01", "Multiple plausible hypotheses", "Deploy / pool vs upstream cascade"),
        ("02", "Select a discriminative probe", "P_conn_wait — wait leads vs lags"),
        ("03", "Evidence changes the beliefs", "Wait rose first → one story eliminated"),
        ("04", "Engineer owns the action", "Rollback is recommended, not fired"),
    ]
    y = 1.5
    for n, h, b in rows:
        rect(s, Inches(0.5), Inches(y), Inches(7.7), Inches(1.05), PANEL, LINE, 0.08)
        add_text(s, Inches(0.7), Inches(y + 0.28), Inches(0.7), Inches(0.45), n, 18, True, ACCENT, font=MONO)
        add_text(s, Inches(1.55), Inches(y + 0.18), Inches(6.4), Inches(0.35), h, 16, True, WHITE)
        add_text(s, Inches(1.55), Inches(y + 0.55), Inches(6.4), Inches(0.35), b, 13, False, MUTED)
        y += 1.15
    rect(s, Inches(8.45), Inches(1.5), Inches(4.35), Inches(4.6), PANEL, ACCENT, 0.08)
    add_text(s, Inches(8.65), Inches(1.7), Inches(4.0), Inches(0.3), "WHAT YOU WILL SEE", 12, True, ACCENT, font=MONO)
    add_text(s, Inches(8.65), Inches(2.15), Inches(4.0), Inches(2.4),
             "Hypothesis board · probe ticker · evidence chain.\n\n"
             "The wait-time series comes from the case file. The policy that selects the probe is live.",
             15, False, INK)
    add_text(s, Inches(8.65), Inches(5.15), Inches(4.0), Inches(0.7),
             "Now let’s open the system.", 16, True, ACCENT)


def s10(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    decorate(s, 10)
    kicker(s, "10  ·  evaluation")
    title(s, "Probe ON vs probe OFF")
    add_text(s, Inches(0.5), Inches(1.1), Inches(12.3), Inches(0.35),
             "Same 8 recorded cases. Ablation harness ic/harness.py. Authored corpus — not a production evaluation.", 14, False, MUTED)
    rect(s, Inches(0.5), Inches(1.55), Inches(12.3), Inches(3.45), PANEL, LINE, 0.08)
    add_text(s, Inches(0.7), Inches(1.68), Inches(5.3), Inches(0.28), "METRIC", 11, True, MUTED)
    add_text(s, Inches(6.2), Inches(1.68), Inches(3.0), Inches(0.28), "PROBES ON", 11, True, ACCENT)
    add_text(s, Inches(9.4), Inches(1.68), Inches(3.0), Inches(0.28), "PROBES OFF", 11, True, MUTED)
    rows = [
        ("Root-cause accuracy  ·  n = 8", "100%", "12.5%"),
        ("Brier score  ·  lower is better", "0.004", "0.352"),
        ("False-positive rollback", "0%", "37.5%"),
        ("Probes executed / case", "1.1", "0"),
        ("Convergence rate", "τ = 0.15", "not reported by harness"),
    ]
    y = 2.1
    for a, b, c in rows:
        add_text(s, Inches(0.7), Inches(y), Inches(5.3), Inches(0.48), a, 14, False, INK)
        add_text(s, Inches(6.2), Inches(y), Inches(3.0), Inches(0.48), b, 16, True, WHITE, font=MONO)
        add_text(s, Inches(9.4), Inches(y), Inches(3.1), Inches(0.48), c, 14, False, MUTED, font=MONO)
        y += 0.52
    take(s, "We evaluate not only whether the diagnosis is correct, but how the investigation reaches it.  Headline slices: ON 100% · OFF 0%.")


def s11(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    decorate(s, 11)
    kicker(s, "11  ·  scope")
    title(s, "What is implemented vs what is not")
    node(s, 0.5, 1.2, 6.05, 4.5, "IMPLEMENTED",
         "Competing hypotheses\nPosterior beliefs\nPrediction-based probe selection\n"
         "Cost-aware selection (cost_ms)\nSequential investigation loop\n"
         "Evidence-based belief updates\nStopping / convergence (τ, K, T)\nProbe ON / OFF ablation")
    node(s, 6.8, 1.2, 6.0, 4.5, "NOT YET",
         "Live telemetry ingestion\nDynamic hypothesis generation\nDynamic probe generation\n"
         "Formal information-theoretic EIG\nProduction integrations\nLarge-scale evaluation", "gap")
    take(s, "The current system validates the investigation-policy concept, not a production deployment.")


def s12(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    decorate(s, 12)
    kicker(s, "12  ·  future work")
    title(s, "Same engine. Stronger data. Safer actions.")
    rows = [
        ("NOW", "Working prototype", "Recorded incidents · seeded hyps · fixed catalog · heuristic discrimination / cost", True, False),
        ("01", "Formal decision", "Entropy / EIG · utility · cost · risk — not just divergence count", False, False),
        ("02", "Dynamic generation", "Hypotheses and probes produced at runtime, not only seeded", False, False),
        ("03", "Live observability", "Read-only metrics, logs, traces — not a recorded JSON case", False, False),
        ("04", "Risk-aware investigation", "Information value + cost + risk, with human approval", False, False),
        ("05", "Large-scale evaluation", "Steps to separation · wasted checks · investigation cost · uncertainty drop", False, False),
        ("THEN", "Production investigation copilot", "Principled, measurable next-check decisions on live incidents", False, True),
    ]
    y = 1.18
    for ph, pt, pd, now, end in rows:
        fill = ACCENT if end else (RGBColor(28, 90, 118) if now else PANEL)
        border = ACCENT if (now or end) else LINE
        fg = DARK if end else INK
        rect(s, Inches(0.5), Inches(y), Inches(12.3), Inches(0.62), fill, border, 0.06)
        add_text(s, Inches(0.65), Inches(y + 0.16), Inches(1.1), Inches(0.3),
                 ph, 11, True, ACCENT if not end else DARK, font=MONO)
        add_text(s, Inches(1.85), Inches(y + 0.14), Inches(3.4), Inches(0.35), pt, 14, True, fg)
        add_text(s, Inches(5.4), Inches(y + 0.14), Inches(7.2), Inches(0.35), pd, 12, False, MUTED if not end else DARK)
        y += 0.68
    take(s, "Not more autonomy for its own sake. More principled, measurable investigation decisions.")


def s13(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    decorate(s, 13)
    kicker(s, "13  ·  direction")
    title(s, "From finding the cause to finding the next question")
    node(s, 0.5, 1.25, 6.0, 4.4, "TODAY",
         "Incident\n↓  Observability\n↓  Engineer\n↓  Manual investigation\n↓  Root cause")
    node(s, 6.8, 1.25, 6.0, 4.4, "OUR DIRECTION",
         "Incident\n↓  Observability\n↓  Competing hypotheses\n↓  Next-best investigation\n"
         "↓  Evidence → belief update\n↓  Next-best investigation\n↓  Convergence", "accent")
    take(s, "The goal is not simply to predict the root cause. It is to systematically decide what evidence to collect next.")


def main():
    prs = Presentation()
    prs.slide_width = W
    prs.slide_height = H
    for fn in (s01, s02, s03, s04, s05, s06, s07, s08, s09, s10, s11, s12, s13):
        fn(prs)
    prs.save(OUT)
    print(f"wrote {OUT}  ({len(prs.slides)} slides)")


if __name__ == "__main__":
    main()
