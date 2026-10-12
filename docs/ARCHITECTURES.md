# Architecture editor and recursive teams

Version 0.6.5 adds twelve patterns to the original ten. These are executable graphs in the downloadable desktop application. Every model stage requires a connected provider, local runtime, or linked device.

Open **Architectures**, search by name or purpose, and filter by General, Coding, Research, Decision, Recursive, or Custom. Select an agent in the team list or canvas to edit its role, instructions, behavior and contracts. Built-ins save as custom copies.

The editor supports duplicate/remove, undo/redo, automatic layout, draggable positions, JSON import/export, and connections through either handles or **From / To → Connect agents**. An agent's inspector lists removable connections. Removing a connection also clears conditions and task sources that are no longer upstream. Duplicate preserves incoming connections. Unsaved edits receive an in-app save/discard choice before switching patterns. Ctrl+S saves; Ctrl+Z / Ctrl+Shift+Z undo/redo while focus is outside text inputs. Text inputs retain their usual native undo.

**Check architecture** validates saved references and expands nested calls without inference. It displays node count, possible child calls, depth and model-stage bounds. Contract/schema validation also runs at import and save. Independent roots run in parallel; a join waits for its dependencies. Raw dependency edges must remain acyclic.

## Patterns

| Category | Presets |
| --- | --- |
| General | Supervisor; Pipeline; Parallel / join; Creator / critic; Single-agent baseline; Specialist handoff; Hierarchical team; Bounded revision; Map / reduce; Router and specialists |
| Coding | Test-driven team; Specify / build / verify |
| Research | Shared blackboard; Evidence and fact-check |
| Decision | Debate / ensemble; Debate with a judge; Red team / blue team; Specialist review board; Diamond comparison |
| Recursive | Recursive decomposition; Recursive critique and repair; Recursive fork / join |

Router and specialists validates an exact JSON route and runs one of three conditioned specialists. Skipped branches make no model request. The deterministic ensemble counts structured decisions; agreement does not establish correctness. Test-driven work runs at most two rounds by default, bounded revision at most three. Tools remain subject to the project and agent's grants.

## Recursive behavior

Choose **Recursive team** as a node behavior. **This team (self-call)** remains a self-call when the architecture is copied, exported, imported or installed from the registry. A different saved team can also be selected; every reference cycle must use explicit recursive call nodes. A plain reusable child-team node cannot create a reference cycle.

Select **Task passed to child** from an upstream node, or keep the current task and upstream context. An upstream validated JSON object with a string `task` passes that field; otherwise the selected result's text or serialized data becomes the task. Configure the source node's output contract to validate JSON when using typed tasks. Scoped child tasks must be between 1 and 12,000 characters. Child input is `{task, upstream}`. Upstream results are data and retain their provenance labels.

**Recursive depth limit** is 1–4 and counts all nested calls from the root mission, whose depth is zero. At the limit, the recursive node becomes a real model agent that executes its **Base case instructions**. A case-insensitive **Solve directly when upstream contains** marker can trigger the base case earlier. If a task source is selected, only that source is checked; otherwise any upstream output is checked. Markers are model-generated heuristics, so depth and mission budgets remain the hard termination controls.

Recursive decomposition scopes one smaller task, calls the team, then integrates the actual result. Recursive critique and repair carries reviewed defects into another copy of the team. Both default to depth two. Recursive fork / join runs two scoped calls and merges them, defaulting to one nested level to keep the tree small. It partitions into a fixed pair of branches; it does not create an arbitrary number of model-generated graph nodes.

At mission launch, the engine freezes a finite tree of architecture revisions. It contains no circular JSON references. Later edits to saved teams do not alter that mission. Completed child runs are reused on resume when their inputs and revision match; nested pause and restart preserve completed work. Child tools intersect parent grants, and provenance follows the child output back to the parent.

All descendants share the root's request, token, active-time, estimated-spend and child-run limits. Set **Shared child-run limit** when launching a mission (default four; range 0–32). The expanded snapshot is capped at 128 nodes and four nested levels before execution. A deeply branching tree may exceed this cap even when its per-team graph has fewer than 16 nodes. Child-team buttons show depth/status; open one to inspect its agents and use **Parent mission** or **Root mission** to navigate back.

Revision rounds rerun the graph; recursive calls create nested child missions. Combining them can multiply work. Preview and preflight estimates exclude model retries, tool requests, conditional skips and early stops; mission budgets enforce actual admission. Automated acceptance uses owned deterministic providers and does not claim that a real model will produce accurate decomposition or recognize every stopping marker.
