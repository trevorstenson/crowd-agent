# Evolution Governance

## Core Thesis

Crowd Agent should be framed as:

- **a self-improving coding organism influenced by the internet**

Not as:

- an agent that builds the highest-voted GitHub issue

The crowd should not micromanage every move. The crowd should shape the evolutionary pressure around the system, while the agent chooses its next mutation.

## Minimal Governance Model

Keep the system simple:

1. **Evolution Tracks**
2. **Mutation Proposals**
3. **Autonomous Roadmap**
4. **Weighted Nightly Selection**

That is enough to preserve the project's novelty without creating governance sprawl.

## The Four Pieces

### 1. Evolution Tracks

These are the main public steering mechanism.

Recommended tracks:
- `Capability`
- `Reliability`
- `Survival`
- `Legibility`
- `Virality`

Mechanic:
- create one pinned issue per track
- people react with `+1` or `-1`
- reactions create a pressure score for that trait

Interpretation:
- the crowd is not saying exactly what to build
- the crowd is saying what the organism should become more like

This is the core public interaction surface.

### 2. Mutation Proposals

These replace generic feature-request voting.

A mutation proposal is a candidate evolutionary change. It should answer:
- what should change
- which track it affects
- why it compounds

Keep the format lightweight. A mutation proposal should be easy to submit and easy to score.

Recommended labels:
- `mutation`
- `track:capability`
- `track:reliability`
- `track:survival`
- `track:legibility`
- `track:virality`

### 3. Autonomous Roadmap

The roadmap remains the standing source of self-directed work.

Purpose:
- guarantee forward motion when the crowd is quiet
- encode the agent's long-term trajectory
- keep high-leverage self-improvement tasks available at all times

Current home:
- `agent/autonomous_roadmap.json`

### 4. Weighted Nightly Selection

Each night, the agent should choose from a single candidate pool:
- roadmap tasks
- open mutation proposals
- urgent failure or survival tasks

Do not use “top voted issue wins.”

Use a simple score instead.

## Simple Scoring Model

Start with only four factors:

- `priority`
- `track_pressure`
- `mission_alignment`
- `effort_fit`

Example:

```text
score =
  0.35 * priority +
  0.25 * track_pressure +
  0.25 * mission_alignment +
  0.15 * effort_fit
```

This is intentionally small. More factors can be added later if needed.

## What To Remove

Do not build these yet:
- factions
- challenges as a separate system
- seasons
- too many scoring terms
- too many labels
- too many public interaction modes

They add complexity before the core loop has proven itself.

## Public Story

The product should be explainable in one breath:

**Fenton evolves every night.  
The internet can push what traits it should optimize for.  
The agent decides the exact mutation.**

That is the pitch.

## Frontend Model

The dashboard should reflect this simpler system.

### 1. Hero

Headline:
- `A self-improving coding organism influenced by the internet`

Show:
- mission
- current state
- current selected mutation

### 2. Evolution Pressures

This replaces the current vote queue as the main interaction block.

Show:
- the five tracks
- current pressure on each track
- which track most influenced the latest mutation

### 3. Current Mutation

Show:
- what the agent is evolving right now
- why it chose it
- which pressures contributed

### 4. Recent Evolution

Show recent mutations in terms of:
- what changed
- what trait moved
- what pressure influenced it

### 5. Capability / Survival Stats

Keep this small at first:
- total builds
- success rate
- streak
- last build
- benchmark score later

### 6. Participate

Teach only three actions:
- push a track
- propose a mutation
- review a PR

That is enough.

## Migration Plan

### Phase 1: Reframe

Update the language everywhere:
- README
- dashboard copy
- issue templates

Stop centering “vote on the next issue.”

### Phase 2: Hybrid Selection

Keep existing task flow temporarily, but add:
- track pressure
- mutation proposals
- weighted selection

The old issue queue can still exist during transition, but it should stop being the headline mechanic.

### Phase 3: Full Evolution Mode

Retire generic direct-vote issue selection.

Primary public interaction becomes:
- track pressure
- mutation proposals

Primary internal direction becomes:
- autonomous roadmap + weighted selection

## Success Condition

Someone landing on the project should immediately understand:

- this system keeps evolving even when nobody gives it direct commands
- the crowd can still influence what it becomes
- the dashboard shows a real trajectory, not just a backlog

That is the minimal version worth building.
