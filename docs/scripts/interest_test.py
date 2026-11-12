# Interest test: calibration for the grader's interest check (interest_score in eval/eval.py).
#
# For each simulated profile, scores every activity in the frozen snapshot against the student's
# current interests (eval_only.current_truth.interests): best cosine similarity between the
# activity's text and any interest, with all-MiniLM-L6-v2 embeddings. Shows how events and clubs
# score relative to the cutoff, and writes the plot to docs/figures/interest-test.png.
# Also compares against the old keyword check (eval_only.interest_keywords, as in
# eval/early/early_eval.py): which activities each method passes, and where they disagree
# (printed, and plotted to docs/figures/interest-test-keywords.png).
#
# Run from the repo root:
#   python docs/scripts/interest_test.py
#   python docs/scripts/interest_test.py --threshold 0.5 --out /tmp/scores.png
#
# Needs sentence-transformers and matplotlib (the model downloads on first run).

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # no window, just write the png
import matplotlib.pyplot as plt
import numpy as np
from sentence_transformers import SentenceTransformer

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))  # so agent.retriever imports when run as a script
from agent.retriever import Retriever  # noqa: E402

PROFILES = ROOT / "eval" / "profiles" / "simulated-profiles.json"
WEEK = ("2026-11-16", "2026-11-22")  # reference week (docs/benchmark.md)
MODEL = "all-MiniLM-L6-v2"

# reference palette from the dataviz guide: series 1 / 2, surface, text, grid
EVENTS_COLOR, CLUBS_COLOR = "#2a78d6", "#eb6834"
SURFACE, TEXT, TEXT_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"


def activity_text(a):
    # same text the grader embeds: title | categories | description (common format)
    return " | ".join(x for x in (a["title"], a["categories"], a["description"]) if x)


def load_activities(db_path):
    """This week's events (one per title, recurring events repeat daily) and all clubs, common format."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM events WHERE date BETWEEN ? AND ? ORDER BY date, start_time", WEEK)
    events, seen = [], set()
    for row in rows:
        if row["title"] not in seen:
            seen.add(row["title"])
            events.append(Retriever.to_common(dict(row)))
    clubs = [Retriever.to_common(dict(row)) for row in conn.execute("SELECT * FROM clubs ORDER BY name")]
    return events, clubs


def best_scores(model, activity_vecs, interests):
    # normalized vectors, so cosine = dot product; keep the best interest per activity
    interest_vecs = model.encode(interests, normalize_embeddings=True)
    return (activity_vecs @ interest_vecs.T).max(axis=1)


def summarize(pid, interests, events, clubs, ev_scores, cl_scores, threshold):
    print(f"\n{pid}  {interests}")
    for kind, items, scores in (("events", events, ev_scores), ("clubs", clubs, cl_scores)):
        n_over = int((scores >= threshold).sum())
        print(f"  {kind}: {n_over}/{len(items)} >= {threshold}")
        for i in scores.argsort()[::-1][:5]:
            print(f"    {scores[i]:.2f}  {items[i]['title'][:70]}")


def keyword_match(a, keywords):
    # the old check: a keyword at the start of a word, so "sustainab" hits "sustainability"
    # but "bar" misses "Barbara" (same rule as has_keyword in eval/early/early_eval.py)
    return any(re.search(r"\b" + re.escape(k), activity_text(a), re.I) for k in keywords)


def compare(items, scores, keywords, threshold, n_examples=4):
    """Keyword vs embedding pass/fail on the same activities: counts + examples where they disagree."""
    kw = np.array([keyword_match(a, keywords) for a in items])
    emb = scores >= threshold
    only_kw = [i for i in np.flatnonzero(kw & ~emb)]
    only_emb = [i for i in np.flatnonzero(emb & ~kw)]
    # disagreements closest to the cutoff first are the most informative, so sort by score
    only_kw.sort(key=lambda i: -scores[i])
    only_emb.sort(key=lambda i: -scores[i])
    return {"keyword": int(kw.sum()), "embedding": int(emb.sum()), "both": int((kw & emb).sum()),
            "keyword_only": [(float(scores[i]), items[i]["title"]) for i in only_kw[:n_examples]],
            "n_keyword_only": len(only_kw),
            "embedding_only": [(float(scores[i]), items[i]["title"]) for i in only_emb[:n_examples]],
            "n_embedding_only": len(only_emb)}


def print_comparison(kind, c):
    print(f"  {kind} vs keywords: keyword {c['keyword']}, embedding {c['embedding']}, both {c['both']}")
    for label, key, n in (("keyword only", "keyword_only", "n_keyword_only"),
                          ("embedding only", "embedding_only", "n_embedding_only")):
        if c[n]:
            print(f"    {label} ({c[n]}): " + "; ".join(f"{s:.2f} {short(t, 40)}" for s, t in c[key]))


def short(title, n=28):
    return title if len(title) <= n else title[:n - 1] + "…"


def plot(results, threshold, out):
    """One panel per profile: score distributions of events vs clubs, cutoff marked."""
    bins = np.linspace(0, 0.8, 33)
    fig, axes = plt.subplots(len(results), 1, figsize=(9, 2.1 * len(results)), sharex=True, facecolor=SURFACE)
    for ax, (pid, events, clubs, ev, cl) in zip(axes, results):
        ax.set_facecolor(SURFACE)
        # step histograms of counts on a log axis, so the few activities above the cutoff stay visible
        # (most score low; ~90 events vs ~1250 clubs)
        ax.hist(cl, bins=bins, histtype="step", linewidth=2, color=CLUBS_COLOR, label="clubs")
        ax.hist(ev, bins=bins, histtype="step", linewidth=2, color=EVENTS_COLOR, label="events")
        ax.set_yscale("log")
        ax.set_ylim(0.8, 600)
        ax.axvline(threshold, color=TEXT_2, linestyle="--", linewidth=1.2)
        ax.text(threshold + 0.005, 400, f"cutoff {threshold:.2f}", color=TEXT_2, fontsize=8, va="top")
        # mark the best event and best club with a dot at its score
        i, j = int(ev.argmax()), int(cl.argmax())
        ax.plot([ev[i]], [1], "o", color=EVENTS_COLOR, markersize=6, zorder=3)
        ax.plot([cl[j]], [1], "o", color=CLUBS_COLOR, markersize=6, zorder=3)
        ax.text(0.995, 0.92, f"best club {cl[j]:.2f}: {short(clubs[j]['title'])}", transform=ax.transAxes,
                ha="right", va="top", fontsize=8, color=TEXT)
        ax.text(0.995, 0.72, f"best event {ev[i]:.2f}: {short(events[i]['title'])}", transform=ax.transAxes,
                ha="right", va="top", fontsize=8, color=TEXT)
        ax.set_title(pid, loc="left", fontsize=9, color=TEXT, pad=3)
        ax.set_yticks([1, 10, 100], labels=["1", "10", "100"])
        ax.minorticks_off()
        ax.set_ylabel("activities", color=TEXT_2, fontsize=8)
        ax.grid(color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
        ax.tick_params(colors=TEXT_2, labelsize=8, length=0)
    axes[-1].set_xlabel("best cosine similarity to the student's current interests", color=TEXT_2, fontsize=9)
    fig.suptitle(f"Interest scores of snapshot activities per profile ({MODEL})", x=0.02, y=0.995, ha="left",
                 fontsize=11, color=TEXT)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper right", bbox_to_anchor=(0.99, 0.998), ncol=2, frameon=False,
               fontsize=9, labelcolor=TEXT)
    fig.tight_layout(rect=(0, 0, 1, 0.975))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=200, facecolor=SURFACE)
    print(f"\nwrote {out}")


def plot_comparison(results, keywords_by_pid, threshold, out):
    """
    Keyword vs embedding: every activity as a dot at its embedding score, colored by whether the old
    keyword check passed it. Dots right of the cutoff pass the embedding check, so orange dots left
    of it are keyword-only passes and gray dots right of it are embedding-only passes.
    """
    rng = np.random.default_rng(0)  # fixed jitter, so the figure is the same every run
    fig, axes = plt.subplots(len(results), 2, figsize=(10, 1.9 * len(results)), sharex=True, facecolor=SURFACE)
    for row, (pid, events, clubs, ev, cl) in zip(axes, results):
        for ax, kind, items, scores in ((row[0], "events", events, ev), (row[1], "clubs", clubs, cl)):
            ax.set_facecolor(SURFACE)
            kw = np.array([keyword_match(a, keywords_by_pid[pid]) for a in items])
            y = rng.uniform(-0.35, 0.35, len(items))
            # no keyword match first (underneath), then keyword matches on top
            ax.scatter(scores[~kw], y[~kw], s=9, color="#b9b8b2", alpha=0.6, linewidths=0, label="no keyword match")
            ax.scatter(scores[kw], y[kw], s=14, color=CLUBS_COLOR, alpha=0.85, linewidths=0, label="keyword match")
            ax.axvline(threshold, color=TEXT_2, linestyle="--", linewidth=1.2)
            emb = scores >= threshold
            # counts in the title line, so they never sit on top of the dots or the cutoff line
            ax.set_title(f"{pid}: {kind}", loc="left", fontsize=8.5, color=TEXT, pad=3)
            ax.set_title(f"keyword only {int((kw & ~emb).sum())} · both {int((kw & emb).sum())} · "
                         f"embedding only {int((emb & ~kw).sum())}", loc="right", fontsize=7.5, color=TEXT_2, pad=3)
            ax.set_ylim(-0.5, 0.5)
            ax.set_yticks([])
            ax.grid(axis="x", color=GRID, linewidth=0.8)
            ax.set_axisbelow(True)
            for side in ("top", "right", "left"):
                ax.spines[side].set_visible(False)
            ax.spines["bottom"].set_color(GRID)
            ax.tick_params(colors=TEXT_2, labelsize=8, length=0)
    for ax in axes[-1]:
        ax.set_xlabel("best cosine similarity (embedding check)", color=TEXT_2, fontsize=9)
    for ax in axes[-1]:  # cutoff label once per column, under the dots
        ax.text(threshold + 0.008, -0.47, f"cutoff {threshold:.2f}", color=TEXT_2, fontsize=7.5, va="bottom")
    fig.suptitle(f"Old keyword check vs embedding check ({MODEL})", x=0.02, y=0.995, ha="left",
                 fontsize=11, color=TEXT)
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles[::-1], labels[::-1], loc="upper right", bbox_to_anchor=(0.99, 0.998), ncol=2,
               frameon=False, fontsize=9, labelcolor=TEXT, markerscale=1.5)
    fig.tight_layout(rect=(0, 0, 1, 0.975))
    fig.savefig(out, dpi=200, facecolor=SURFACE)
    print(f"wrote {out}")


def main():
    ap = argparse.ArgumentParser(description="Calibrate the grader's embedding interest check.")
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "snapshot-2026-11-11.db")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "figures" / "interest-test.png")
    ap.add_argument("--threshold", type=float, default=0.45)
    args = ap.parse_args()

    with open(PROFILES) as f:
        profiles = json.load(f)
    events, clubs = load_activities(args.db)
    model = SentenceTransformer(MODEL)
    # embed each activity once, reuse for every profile
    ev_vecs = model.encode([activity_text(a) for a in events], normalize_embeddings=True)
    cl_vecs = model.encode([activity_text(a) for a in clubs], normalize_embeddings=True, batch_size=128)

    results = []
    for pid, p in profiles.items():
        interests = p["eval_only"]["current_truth"]["interests"]  # current only, never superseded
        ev, cl = best_scores(model, ev_vecs, interests), best_scores(model, cl_vecs, interests)
        summarize(pid, interests, events, clubs, ev, cl, args.threshold)
        # same activities through the old keyword check, to see what the switch changes
        keywords = p["eval_only"]["interest_keywords"]
        print_comparison("events", compare(events, ev, keywords, args.threshold))
        print_comparison("clubs", compare(clubs, cl, keywords, args.threshold))
        results.append((pid, events, clubs, ev, cl))
    plot(results, args.threshold, args.out)
    keywords_by_pid = {pid: p["eval_only"]["interest_keywords"] for pid, p in profiles.items()}
    plot_comparison(results, keywords_by_pid, args.threshold, args.out.with_name("interest-test-keywords.png"))


if __name__ == "__main__":
    main()
