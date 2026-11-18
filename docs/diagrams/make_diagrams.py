"""Writes the design drawings into this folder: 01-auto-trace.drawio and 02-change-problem-link.drawio.
Names are made-up examples (host-02, relay, vks-a); no real host names or addresses.

Export: docker run --rm -v "$PWD":/data rlespinasse/drawio-desktop-headless -x -f png -o X.png X.drawio
"""
import html
from pathlib import Path

OUT = Path(__file__).parent

# One colour per kind of part, the same in every drawing.
C = {"page": ("#f6f7f9", "#dde1e7"), "panel": ("#ffffff", "#dde1e7"),
     "hub": ("#e8eef7", "#5b7bb5"),        # what the hub does by itself
     "decide": ("#fff8e1", "#c9a227"),     # a yes/no check
     "skip": ("#f1f2f4", "#9aa3b2"),       # stops here, nothing more
     "tool": ("#e6f6fa", "#2a9bb8"),       # a look-only tool run
     "model": ("#f1ebfd", "#7c4dce"),      # the model is called
     "link": ("#e8f5e9", "#2e7d32"),       # change -> problem link (no model)
     "out": ("#ffffff", "#2a9bb8"),        # what people see on the page
     "change": ("#3d7bff", "#3d7bff"), "problem": ("#e8900c", "#e8900c"),
     "note": ("#fff8e1", "#f9a825"), "window": ("#fdf1de", "#f3d9ab")}
TEXT, MUTED = "#1d2330", "#5d6676"


class D:
    def __init__(self):
        self.cells, self.n = [], 2

    def id(self):
        self.n += 1
        return f"c{self.n}"

    def box(self, x, y, w, h, text="", kind="panel", size=12, bold=False, color=TEXT, align="center",
            valign="middle", rounded=1, extra=""):
        i, (fill, line) = self.id(), C[kind]
        t = f"<b>{text}</b>" if bold and text else text
        st = (f"rounded={rounded};whiteSpace=wrap;html=1;fillColor={fill};strokeColor={line};fontSize={size};"
              f"fontColor={color};align={align};verticalAlign={valign};arcSize=8;spacingLeft=8;spacingRight=8;{extra}")
        self.cells.append(f'<mxCell id="{i}" value="{html.escape(t)}" style="{st}" vertex="1" parent="1">'
                          f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
        return i

    def diamond(self, x, y, w, h, text, kind="decide"):
        return self.box(x, y, w, h, text, kind, size=12, bold=True, extra="rhombus;")

    def text(self, x, y, w, h, text, size=12, color=TEXT, align="left", bold=False, valign="middle"):
        i = self.id()
        t = f"<b>{text}</b>" if bold else text
        self.cells.append(f'<mxCell id="{i}" value="{html.escape(t)}" style="text;html=1;fontSize={size};'
                          f'fontColor={color};align={align};verticalAlign={valign};whiteSpace=wrap;spacing=2;" '
                          f'vertex="1" parent="1"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
        return i

    def arrow(self, src, dst, label="", color="#5d6676", dashed=False, exit=None, entry=None):
        i = self.id()
        st = f"edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;endFill=1;strokeColor={color};strokeWidth=2;" \
             f"fontSize=11;fontColor={TEXT};labelBackgroundColor=#ffffff;{'dashed=1;' if dashed else ''}"
        if exit:
            st += f"exitX={exit[0]};exitY={exit[1]};exitDx=0;exitDy=0;"
        if entry:
            st += f"entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;"
        self.cells.append(f'<mxCell id="{i}" value="{html.escape(label)}" style="{st}" edge="1" parent="1" source="{src}" '
                          f'target="{dst}"><mxGeometry relative="1" as="geometry"/></mxCell>')

    def line(self, x1, y1, x2, y2, color="#dde1e7", width=1, dashed=False, arrow=False):
        i = self.id()
        st = (f"endArrow={'block;endFill=1' if arrow else 'none'};html=1;strokeColor={color};strokeWidth={width};"
              f"{'dashed=1;' if dashed else ''}")
        self.cells.append(f'<mxCell id="{i}" value="" style="{st}" edge="1" parent="1"><mxGeometry relative="1" as="geometry">'
                          f'<mxPoint x="{x1}" y="{y1}" as="sourcePoint"/><mxPoint x="{x2}" y="{y2}" as="targetPoint"/>'
                          f'</mxGeometry></mxCell>')

    def dot(self, x, y, r, kind):
        i, (fill, _) = self.id(), C[kind]
        self.cells.append(f'<mxCell id="{i}" value="" style="ellipse;fillColor={fill};strokeColor=none;" vertex="1" '
                          f'parent="1"><mxGeometry x="{x - r}" y="{y - r}" width="{2 * r}" height="{2 * r}" as="geometry"/></mxCell>')

    def triangle(self, x, y, s, kind):
        i, (fill, _) = self.id(), C[kind]
        self.cells.append(f'<mxCell id="{i}" value="" style="triangle;direction=north;fillColor={fill};strokeColor=none;" '
                          f'vertex="1" parent="1"><mxGeometry x="{x - s / 2}" y="{y - s / 2}" width="{s}" height="{s}" '
                          f'as="geometry"/></mxCell>')

    def legend(self, x, y, items):
        for kind, label in items:
            self.box(x, y, 18, 18, "", kind)
            self.text(x + 24, y - 2, 170, 22, label, size=11, color=MUTED)
            x += 182

    def save(self, name, w, h):
        xml = (f'<mxfile host="drawio"><diagram name="{name}" id="{name}"><mxGraphModel dx="{w}" dy="{h}" grid="0" '
               f'gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
               f'pageWidth="{w}" pageHeight="{h}" math="0" shadow="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
               + "".join(self.cells) + "</root></mxGraphModel></diagram></mxfile>")
        (OUT / f"{name}.drawio").write_text(xml)


# ------------------------------------------------------------------ 01 auto-trace
def auto_trace():
    d, W, H = D(), 1420, 700
    d.box(0, 0, W, H, "", "page", rounded=0)
    d.text(24, 12, 1100, 30, "Auto-trace: a new problem starts its own look-only trace", size=18, bold=True)
    d.text(24, 40, 1100, 22, "Runs on the hub. The model is called only for a new problem, within the limits. "
                             "Nothing is ever changed.", size=12, color=MUTED)

    watch = d.box(24, 110, 170, 80, "Watch round<br>every 60 s on the hub<br><i>(already runs today)</i>", "hub")
    new = d.diamond(230, 100, 160, 100, "New<br>problem?")
    quiet = d.box(235, 260, 150, 64, "No: nothing more.<br>No model call.", "skip")
    limits = d.diamond(430, 95, 190, 110, "Within<br>limits?")
    d.text(532, 214, 130, 48, "· not traced yet<br>· under 10 this hour", size=11, color=MUTED)
    over = d.box(435, 280, 180, 70, "Over: the Map says<br>&quot;not traced: limit reached&quot;", "skip")

    d.box(670, 84, 380, 410, "", "panel")
    d.text(686, 92, 350, 24, "Auto-trace (look-only tools, no fix tools)", size=13, bold=True)
    s1 = d.box(690, 124, 340, 52, "1 · Link to a recent change<br><span style=\"color:#5d6676\">same host or cluster, "
                                  "within 10 min</span>", "link", size=12)
    s2 = d.box(690, 192, 340, 52, "2 · what_changed<br><span style=\"color:#5d6676\">on that host or cluster</span>",
               "tool", size=12)
    s3 = d.box(690, 260, 340, 52, "3 · Logs and state of the thing<br><span style=\"color:#5d6676\">container, pod "
                                  "or VKS machine</span>", "tool", size=12)
    s4 = d.box(690, 328, 340, 52, "4 · Past cases<br><span style=\"color:#5d6676\">has this been seen and fixed "
                                  "before?</span>", "tool", size=12)
    s5 = d.box(690, 412, 340, 64, "5 · The model writes<br><b>Cause:</b> and <b>Fix:</b> from steps 1 to 4", "model")
    for a, b in ((s1, s2), (s2, s3), (s3, s4), (s4, s5)):
        d.arrow(a, b)

    d.text(1100, 84, 300, 24, "What people see", size=13, bold=True)
    o1 = d.box(1100, 116, 300, 92, "<b>Map</b>, open problems<br>▲ relay unhealthy · host-02<br>"
                                   "<span style=\"color:#5d6676\">traced: likely image relay:5</span><br>[Open trace]",
               "out", align="left")
    o2 = d.box(1100, 228, 300, 72, "<b>Trace</b> view<br>the trail and evidence are filled in;<br>ask more from there",
               "out", align="left")
    o3 = d.box(1100, 320, 300, 56, "<b>Console</b> feed<br>● auto-trace done: relay on host-02", "out", align="left")
    o4 = d.box(1100, 396, 300, 56, "<b>Log</b>, in plain English<br>&quot;Auto-traced relay on host-02 …&quot;",
               "out", align="left")

    d.arrow(watch, new)
    d.arrow(new, quiet, "no")
    d.arrow(new, limits, "yes")
    d.arrow(limits, over, "no", exit=(0.5, 1), entry=(0.5, 0))
    d.arrow(limits, s1, "yes", entry=(0, 0.5))
    for o in (o1, o2, o3, o4):
        d.arrow(s5, o, exit=(1, 0.5), entry=(0, 0.5))

    d.box(24, 520, 600, 116, "", "note", align="left")
    d.text(40, 528, 570, 24, "Settings (compose.yaml settings block)", size=13, bold=True)
    d.text(40, 556, 580, 96, "<b>auto-trace: \"on\"</b>  &nbsp;&quot;off&quot; turns it off<br>"
                             "<b>auto-trace-per-hour: \"10\"</b>  &nbsp;most model calls an hour<br>"
                             "<b>link-minutes: \"10\"</b>  &nbsp;how far back a change counts<br>"
                             "Uses learn-llm-url's model when set, else the chat model.", size=12, valign="top")
    d.legend(670, 532, [("hub", "hub, by itself"), ("decide", "a check"), ("skip", "stops, no model"),
                        ("link", "link, no model")])
    d.legend(670, 566, [("tool", "look-only tool"), ("model", "model call"), ("out", "on the page")])
    d.save("01-auto-trace", W, H)


# ------------------------------------------------------------------ 02 change -> problem link
def link():
    d, W, H = D(), 1420, 640
    d.box(0, 0, W, H, "", "page", rounded=0)
    d.text(24, 12, 1100, 30, "Change → problem link: was the problem right after a change?", size=18, bold=True)
    d.text(24, 40, 1100, 22, "Worked out from the recorded changes and the watch's problems. No model call, "
                             "so it costs nothing and runs every round.", size=12, color=MUTED)

    # timeline 13:40 .. 14:10 across x0..x1
    x0, x1, t0, t1 = 140, 860, 13 * 60 + 40, 14 * 60 + 10

    def X(hhmm):
        h, m = hhmm.split(":")
        return x0 + (int(h) * 60 + int(m) - t0) / (t1 - t0) * (x1 - x0)

    d.box(24, 80, 860, 430, "", "panel")
    d.text(40, 88, 400, 24, "Example: one lane per host and cluster", size=13, bold=True)
    for t in ("13:40", "13:50", "14:00", "14:10"):
        d.text(X(t) - 30, 116, 60, 20, t, size=11, color=MUTED, align="center")
        d.line(X(t), 140, X(t), 450, "#eceef2")
    lanes = {"host-02": 180, "host-03": 290, "vks-a": 400}
    for name, y in lanes.items():
        d.text(40, y - 12, 90, 24, name, size=13, bold=True)
        d.line(x0, y, x1, y, "#c9ced6", 2)

    def window(lane, t_problem):  # the 10 min before a problem
        y = lanes[lane]
        h, m = t_problem.split(":")
        start = f"{(int(h) * 60 + int(m) - 10) // 60}:{(int(h) * 60 + int(m) - 10) % 60:02d}"
        d.box(X(start), y - 26, X(t_problem) - X(start), 52, "", "window", rounded=0, extra="opacity=70;")

    # host-02: strong link
    window("host-02", "13:53")
    d.dot(X("13:52"), 180, 9, "change")
    d.text(X("13:52") - 200, 196, 210, 36, "13:52 image relay:4 → relay:5", size=11, align="right")
    d.triangle(X("13:53"), 180, 20, "problem")
    d.text(X("13:53") + 14, 140, 200, 30, "13:53 relay unhealthy", size=11)
    d.box(X("13:53") + 30, 196, 250, 44, "<b>linked</b>: same container,<br>1 min after", "link", size=11, align="left")
    # host-03: maybe link + an old change outside the window
    window("host-03", "14:00")
    d.dot(X("13:44"), 290, 9, "change")
    d.text(X("13:44") - 70, 306, 140, 36, "13:44 new container cache", size=11, align="center")
    d.dot(X("13:58"), 290, 9, "change")
    d.text(X("13:58") - 130, 250, 140, 30, "13:58 cache restarted", size=11, align="right")
    d.triangle(X("14:00"), 290, 20, "problem")
    d.text(X("14:00") + 14, 250, 160, 30, "14:00 web exited", size=11)
    d.box(X("14:00") - 20, 306, 200, 44, "<b>maybe</b>: same host,<br>other name, 2 min", "decide", size=11, align="left")
    d.text(X("13:44") - 70, 342, 200, 30, "13:44 is 16 min before: not linked", size=11, color=MUTED, align="center")
    # vks-a: strong link
    window("vks-a", "14:08")
    d.dot(X("14:05"), 400, 9, "change")
    d.text(X("14:05") - 230, 412, 226, 26, "14:05 shop/web image 1.2 → 1.3", size=11, align="right")
    d.triangle(X("14:08"), 400, 20, "problem")
    d.text(X("14:08") - 200, 360, 190, 30, "14:08 shop/web 0 of 3 ready", size=11, align="right")
    d.box(X("14:05") - 230, 448, 230, 40, "<b>linked</b>: same workload, 3 min", "link", size=11, align="left")

    d.legend(40, 526, [("change", "a change"), ("problem", "a problem starts"), ("window", "the 10 min before")])

    # the rule
    d.box(910, 80, 486, 230, "", "panel")
    d.text(926, 88, 450, 24, "The rule", size=13, bold=True)
    d.text(926, 116, 456, 120, "A change counts for a problem when it is<br>"
                               "&nbsp;1 · on the same host or cluster<br>"
                               "&nbsp;2 · <b>before</b> the problem started<br>"
                               "&nbsp;3 · at most <b>10 min</b> before (link-minutes)", size=12, valign="top")
    d.box(926, 232, 140, 60, "<b>linked</b><br>same name", "link", size=11)
    d.box(1076, 232, 140, 60, "<b>maybe</b><br>same host, other name", "decide", size=11)
    d.box(1226, 232, 156, 60, "<b>none</b><br>other host, after,<br>or too long before", "skip", size=11)

    # where it shows
    d.box(910, 330, 486, 280, "", "panel")
    d.text(926, 338, 450, 24, "Where it shows", size=13, bold=True)
    d.box(926, 370, 456, 52, "<b>What changed</b>: a line joins the two markers in the lane", "out", align="left")
    d.box(926, 432, 456, 52, "<b>Map</b>: the problem says<br>&quot;1 min after image relay:4 → relay:5&quot;", "out",
          align="left")
    d.box(926, 494, 456, 52, "<b>Auto-trace</b>: given to the model as the first evidence", "model", align="left")
    d.box(926, 556, 456, 40, "<b>Console</b>: ▲ relay unhealthy ← image relay:5", "out", align="left")
    d.save("02-change-problem-link", W, H)


if __name__ == "__main__":
    auto_trace()
    link()
