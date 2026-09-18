---
name: EdgeGate Issue
about: One Issue slice of the EdgeGate roadmap
title: "Eg#: <short title>"
labels: Issue
---

## Issue
Eg# — <title>   · scope: `<scope>`

## Context
Phase <n> of the roadmap (docs/PLAN.md). Why this slice exists, what it
unlocks for the system.

## Guide
- Code guide: `docs/code/<doc>.md`
- Concepts: tutorials <n>, <n>
- Grows: `edgegate/<file>.py` (+ `proxy.py` if it touches the flow)

## What's in
- (bullets; mirrors the commit body you'll write)

## Acceptance / Verify
- (runnable curl/commands that prove it passes)

## Definition of done
- all prior-issue checks still pass
- access log stays valid JSON; /metrics counters updated
- stdlib only — no new dependencies