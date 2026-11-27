#!/usr/bin/env python3.11
"""Dependency-free SVG plotting for the audit (no matplotlib in this env).

Two primitives:
  line_chart(path, title, xs, series, ylabel) — one path per series,
    xs is a list of (x_value, label), series is list of (name, [y-or-None]).
  bar_chart(path, title, items, ylabel) — items is [(label, value)].

Writes a standalone .svg. Deterministic.
"""
from __future__ import annotations

import math


def _scale(vals, lo=None, hi=None):
    vs = [v for v in vals if v is not None]
    if lo is None:
        lo = min(vs) if vs else 0.0
    if hi is None:
        hi = max(vs) if vs else 1.0
    if hi <= lo:
        hi = lo + 1.0
    pad = (hi - lo) * 0.06
    return lo - pad, hi + pad


def line_chart(path: str, title: str, xs, series, ylabel='',
               w=880, h=520) -> str:
    ML, MR, MT, MB = 70, 20, 40, 70
    pw, ph = w - ML - MR, h - MT - MB
    xvals = [x[0] for x in xs]
    allv = [y for _, ys in series for y in ys if y is not None]
    ylo, yhi = _scale(allv)
    X = lambda v: ML + (v - min(xvals)) / (max(xvals) - min(xvals)) * pw \
        if len(set(xvals)) > 1 else ML + pw / 2
    Y = lambda v: MT + ph - (v - ylo) / (yhi - ylo) * ph
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" '
           f'height="{h}" font-family="monospace" font-size="12">']
    out.append(f'<rect width="{w}" height="{h}" fill="white"/>')
    out.append(f'<text x="{ML}" y="20">{title}</text>')
    out.append(f'<text x="16" y="{MT + ph / 2}" '
               f'font-size="10" transform="rotate(-90 16 {MT + ph / 2})">'
               f'{ylabel}</text>')
    # y gridlines (5)
    for i in range(5):
        v = ylo + (yhi - ylo) * i / 4
        yy = Y(v)
        out.append(f'<line x1="{ML}" y1="{yy:.1f}" x2="{ML + pw}" '
                   f'y2="{yy:.1f}" stroke="#ddd"/>')
        out.append(f'<text x="{ML - 6}" y="{yy + 3:.1f}" font-size="10" '
                   f'text-anchor="end">{v:.3f}</text>')
    # x labels
    step = max(1, len(xs) // 10)
    for i, (xv, lab) in enumerate(xs):
        if i % step == 0:
            out.append(f'<text x="{X(xv):.1f}" y="{MT + ph + 14}" '
                       f'font-size="9" text-anchor="middle">{lab}</text>')
    colors = ['#1a56db', '#c2410c', '#047857', '#7c3aed', '#b45309']
    for si, (name, ys) in enumerate(series):
        pts = []
        for xv, yv in zip(xvals, ys):
            if yv is None:
                continue
            pts.append(f'{X(xv):.1f},{Y(yv):.1f}')
        col = colors[si % len(colors)]
        if len(pts) > 1:
            out.append(f'<polyline points="{" ".join(pts)}" fill="none" '
                       f'stroke="{col}" stroke-width="1.5"/>')
        elif pts:
            out.append(f'<circle cx="{pts[0].split(",")[0]}" '
                       f'cy="{pts[0].split(",")[1]}" r="3" fill="{col}"/>')
        out.append(f'<text x="{ML + pw - 4}" y="{30 + si * 14}" '
                   f'font-size="10" text-anchor="end" fill="{col}">'
                   f'{name}</text>')
    out.append('</svg>')
    with open(path, 'w') as fh:
        fh.write('\n'.join(out))
    return path


def bar_chart(path: str, title: str, items, ylabel='', w=880, h=520) -> str:
    ML, MR, MT, MB = 70, 20, 40, 90
    pw, ph = w - ML - MR, h - MT - MB
    vals = [v for _, v in items]
    ylo, yhi = _scale(vals, 0.0, None)
    n = len(items)
    bw = pw / n * 0.7
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" '
           f'height="{h}" font-family="monospace" font-size="12">']
    out.append(f'<rect width="{w}" height="{h}" fill="white"/>')
    out.append(f'<text x="{ML}" y="20">{title}</text>')
    for i in range(5):
        v = yhi * i / 4
        yy = MT + ph - (v - ylo) / (yhi - ylo) * ph
        out.append(f'<line x1="{ML}" y1="{yy:.1f}" x2="{ML + pw}" '
                   f'y2="{yy:.1f}" stroke="#ddd"/>')
        out.append(f'<text x="{ML - 6}" y="{yy + 3:.1f}" font-size="10" '
                   f'text-anchor="end">{v:.3f}</text>')
    for i, (lab, v) in enumerate(items):
        x0 = ML + (i + 0.15) / n * pw
        yy = MT + ph - (v - ylo) / (yhi - ylo) * ph
        out.append(f'<rect x="{x0:.1f}" y="{yy:.1f}" '
                   f'width="{bw:.1f}" height="{MT + ph - yy:.1f}" '
                   f'fill="#1a56db"/>')
        out.append(f'<text x="{x0 + bw / 2:.1f}" y="{MT + ph + 14}" '
                   f'font-size="9" text-anchor="middle" '
                   f'transform="rotate(30 {x0 + bw / 2:.1f} {MT + ph + 14})">'
                   f'{lab}</text>')
    out.append(f'<text x="16" y="{MT + ph / 2}" font-size="10" '
               f'transform="rotate(-90 16 {MT + ph / 2})">{ylabel}</text>')
    out.append('</svg>')
    with open(path, 'w') as fh:
        fh.write('\n'.join(out))
    return path
