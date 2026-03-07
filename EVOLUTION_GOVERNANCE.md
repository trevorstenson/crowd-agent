# Evolution Governance

This document describes Crowd Agent's current operating model.

## Core Thesis

Crowd Agent is:

- **a self-improving coding organism influenced by the internet**

It is not a system that simply builds the highest-voted issue.

The crowd shapes evolutionary pressure around the system. The agent chooses the next mutation.

## Operating Model

Crowd Agent operates through four pieces:

### 1. Evolution Tracks

Evolution tracks are the main public steering mechanism.

The active tracks are:
- `Capability`
- `Reliability`
- `Survival`
- `Legibility`
- `Virality`

Mechanics:
- one open GitHub issue exists per track
- people react with `+1` or `-1`
- recent reactions create pressure for that trait
- old reactions decay unless people return and refresh them

Meaning:
- the crowd is not assigning exact work
- the crowd is expressing what the organism should become more like

### 2. Mutation Proposals

Mutation proposals are candidate evolutionary changes submitted by humans.

Each proposal answers:
- what should change
- which track it affects
- why it compounds

Active labels:
- `mutation`
- `track:capability`
- `track:reliability`
- `track:survival`
- `track:legibility`
- `track:virality`

### 3. Autonomous Roadmap

The autonomous roadmap is the standing source of self-directed work.

It exists to:
- guarantee forward motion when the crowd is quiet
- encode the agent's long-term trajectory
- keep high-leverage self-improvement tasks available at all times

Current source:
- `agent/autonomous_roadmap.json`

### 4. Weighted Nightly Selection

Each night, the agent chooses from a single candidate pool:
- roadmap tasks
- open mutation proposals
- urgent failure or survival tasks

The system does not use "top voted issue wins."

## Selection Score

The nightly selector scores candidates using four factors:

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

This keeps selection legible and easy to inspect.

## Public Story

The project should be explainable in one breath:

**Crowd Agent evolves every night.  
The internet can push what traits it should optimize for.  
The agent decides the exact mutation.**

## Frontend Model

The dashboard reflects this operating model.

### 1. Hero

Show:
- mission
- current state
- current selected mutation

### 2. Evolution Pressures

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

Show:
- total builds
- success rate
- streak
- last build

### 6. Participate

Teach only three actions:
- push a track
- propose a mutation
- review a PR

## Intended Outcome

Someone landing on the project should immediately understand:

- this system keeps evolving even when nobody gives it direct commands
- the crowd can still influence what it becomes
- the dashboard shows a real trajectory, not just a backlog
