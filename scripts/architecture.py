# High-level architecture diagram for docs/agent.md (Agent Design): writes docs/figures/architecture.png.
#
# Run from the repo root:
#   python scripts/architecture.py
#
# Supervisor loop (docs/agent.md, Pipeline): the agent (router) is the hub; every subagent returns its results
# to the agent, which decides the next step.

from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # no window, just write the png
import matplotlib.pyplot as plt
from matplotlib.patches import Arc, Circle, Ellipse, FancyArrowPatch, FancyBboxPatch, Rectangle

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "figures" / "architecture.png"

BOX_FILL, BOX_EDGE = "#e8f0fc", "#2b2b2b"     # components
AGENT_FILL = "#a9c8f0"                        # the agent (hub), darker to stand out
LLM_FILL, LLM_EDGE = "#b7dfb9", "#4f8f55"     # "LLM" tag
DATA_FILL, DATA_EDGE = "#d9d9d9", "#5a5a5a"   # data stores (cylinders)
TEXT = "#111111"


def component(ax, x, y, w, h, name, llm=False, fill=BOX_FILL, bold=False, edge_width=1.2):
    """Light blue box with the name (green LLM tag only if llm=True)."""
    ax.add_patch(Rectangle((x, y), w, h, facecolor=fill, edgecolor=BOX_EDGE, linewidth=edge_width))
    ax.text(x + w / 2, y + h - (0.38 if llm else h / 2), name, ha="center", va="center", fontsize=12.5, color=TEXT,
            fontweight="bold" if bold else "normal")
    if llm:
        ax.add_patch(FancyBboxPatch((x + w / 2 - 0.42, y + 0.28), 0.84, 0.36, boxstyle="round,pad=0.02,rounding_size=0.08",
                                    facecolor=LLM_FILL, edgecolor=LLM_EDGE, linewidth=1))
        ax.text(x + w / 2, y + 0.46, "LLM", ha="center", va="center", fontsize=9, fontweight="bold", color=TEXT)


def cylinder(ax, x, y, w, h, label):
    """Gray data store: body + top ellipse + bottom arc."""
    e = 0.22  # ellipse height
    ax.add_patch(Rectangle((x, y + e / 2), w, h - e, facecolor=DATA_FILL, edgecolor="none"))
    ax.add_patch(Arc((x + w / 2, y + e / 2), w, e, theta1=180, theta2=360, color=DATA_EDGE, linewidth=1.2))
    ax.add_patch(Ellipse((x + w / 2, y + e / 2), w, e, facecolor=DATA_FILL, edgecolor="none"))
    ax.plot([x, x], [y + e / 2, y + h - e / 2], color=DATA_EDGE, linewidth=1.2)
    ax.plot([x + w, x + w], [y + e / 2, y + h - e / 2], color=DATA_EDGE, linewidth=1.2)
    ax.add_patch(Ellipse((x + w / 2, y + h - e / 2), w, e, facecolor="#ececec", edgecolor=DATA_EDGE, linewidth=1.2))
    ax.text(x + w / 2, y + (h - e) / 2, label, ha="center", va="center", fontsize=10, color=TEXT)


def person(ax, x, y, label):
    """Simple outline of a person (head + shoulders) for the student."""
    ax.add_patch(Circle((x, y + 0.72), 0.38, facecolor="white", edgecolor="#5a4a4a", linewidth=1.4))
    ax.add_patch(Arc((x, y - 0.35), 1.5, 1.3, theta1=0, theta2=180, color="#5a4a4a", linewidth=1.4))
    ax.plot([x - 0.75, x + 0.75], [y - 0.35, y - 0.35], color="#5a4a4a", linewidth=1.4)
    ax.text(x, y - 0.65, label, ha="center", va="top", fontsize=12.5, color=TEXT)


def line(ax, points, label=None, both=False, label_xy=None, ha="center"):
    """Right-angle connector through points, arrowhead at the end (and start if both)."""
    xs, ys = zip(*points)
    ax.plot(xs[:-1] + (xs[-1],), ys[:-1] + (ys[-1],), color="black", linewidth=1.1, solid_capstyle="butt")
    ax.add_patch(FancyArrowPatch(points[-2], points[-1], arrowstyle="-|>", mutation_scale=10, color="black", linewidth=1.1))
    if both:
        ax.add_patch(FancyArrowPatch(points[1], points[0], arrowstyle="-|>", mutation_scale=10, color="black", linewidth=1.1))
    if label:
        ax.text(*label_xy, label, ha=ha, va="center", fontsize=10, color=TEXT,
                bbox=dict(facecolor="white", edgecolor="none", pad=1.2))


def label(ax, x, y, text, ha="center"):
    ax.text(x, y, text, ha=ha, va="center", fontsize=9, color=TEXT, linespacing=1.1,
            bbox=dict(facecolor="white", edgecolor="none", pad=1.0))


def main():
    fig, ax = plt.subplots(figsize=(15.6, 8.3), facecolor="white")
    ax.set_xlim(0, 15.8)
    ax.set_ylim(0.3, 8.3)
    ax.axis("off")

    # top level: student, the agent (root of the tree), memory + compactor
    person(ax, 1.5, 6.45, "Student")
    component(ax, 5.9, 5.6, 2.2, 1.8, "ECAgent\n(router)", fill=AGENT_FILL, bold=True, edge_width=2.2)
    line(ax, [(2.5, 6.95), (5.9, 6.95)])
    label(ax, 4.2, 7.2, "initial query")
    line(ax, [(5.9, 6.2), (2.5, 6.2)])
    label(ax, 4.2, 5.95, "plan draft / reply")

    cylinder(ax, 11.0, 5.9, 1.5, 1.6, "Memory\n(state, notes)")
    line(ax, [(8.1, 7.15), (11.0, 7.15)])
    label(ax, 9.55, 7.4, "messages")
    line(ax, [(11.0, 6.2), (8.1, 6.2)])
    label(ax, 9.55, 6.68, "student state: interests,\ngoals, dislikes, preferences,\nschedule, constraints")
    line(ax, [(11.5, 5.9), (11.5, 5.55), (12.0, 5.55), (12.0, 5.9)])     # self-loop: nothing to compact
    label(ax, 11.35, 5.68, "under token limit:\nkeep messages", ha="right")

    component(ax, 13.3, 6.0, 1.8, 1.3, "Compactor")
    line(ax, [(12.5, 7.0), (13.3, 7.0)])
    label(ax, 13.8, 7.75, "every message: contradictions\nnear token limit: summary")
    line(ax, [(13.3, 6.35), (12.5, 6.35)])
    label(ax, 14.2, 5.75, "updated state / summary")

    # second level: the subagents. The router picks one, the agent's code runs it, results come back up
    component(ax, 1.4, 2.6, 2.2, 1.3, "Retriever")
    line(ax, [(6.1, 5.6), (6.1, 4.9), (2.0, 4.9), (2.0, 3.9)])            # agent -> retriever
    label(ax, 4.0, 5.15, "activity request, nothing found yet")
    line(ax, [(3.0, 3.9), (3.0, 4.6), (6.35, 4.6), (6.35, 5.6)])          # retriever -> agent
    label(ax, 4.6, 4.35, "found activities")

    component(ax, 5.9, 2.6, 2.2, 1.3, "Recommender")
    line(ax, [(6.75, 5.6), (6.75, 3.9)])                                  # agent -> recommender
    label(ax, 6.65, 4.25, "rank", ha="right")
    line(ax, [(7.25, 3.9), (7.25, 5.6)])                                  # recommender -> agent
    label(ax, 7.35, 4.25, "ranked", ha="left")

    component(ax, 8.9, 2.6, 1.9, 1.3, "Scheduler")
    line(ax, [(7.85, 5.6), (7.85, 4.9), (10.4, 4.9), (10.4, 3.9)])        # agent -> scheduler
    label(ax, 9.25, 5.1, "activities found, plan needed")
    line(ax, [(9.5, 3.9), (9.5, 4.6), (7.6, 4.6), (7.6, 5.6)])            # scheduler -> agent
    label(ax, 9.4, 4.25, "candidate plans", ha="right")

    # actions after the student approves, and the data the subagents use
    cylinder(ax, 11.7, 2.55, 1.7, 1.35, "Calendar +\nregistration")
    line(ax, [(8.0, 5.6), (8.0, 5.3), (12.55, 5.3), (12.55, 3.9)])        # agent -> calendar
    label(ax, 12.65, 4.6, "approved plan", ha="left")
    line(ax, [(11.7, 3.25), (10.8, 3.25)])
    label(ax, 11.25, 3.55, "busy time")
    cylinder(ax, 1.8, 0.45, 1.4, 1.25, "Events +\nclubs DB")
    line(ax, [(2.5, 2.6), (2.5, 1.7)])
    label(ax, 2.6, 2.15, "search", ha="left")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=200, facecolor="white", bbox_inches="tight")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
