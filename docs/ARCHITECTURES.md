# Architecture editor and recursive teams

Version 0.7.0 extends the 22 executable presets with an architecture workshop and durable branch controls in the downloadable desktop application. Every model stage requires a connected provider, local runtime, or linked device.

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

## Node palette and typed connections

Click a palette entry, or drag it onto the canvas. Model agents and model routers call connected models. Routers should declare a JSON output contract and send their decision to downstream `run_if` conditions. A condition node applies that condition and passes validated inputs; a join combines named inputs or non-skipped upstream results without inference. Voting remains deterministic. Child teams, recursive calls and dynamic fanout create actual nested missions.

Select the receiving node and open **Typed connection fields**. Map an upstream dot-separated selector, such as `findings.0.title`, to a named input. Empty selectors pass the whole output. Selectors perform data lookup only. Missing fields, skipped required inputs, duplicate input names and mismatched input schemas fail before the consumer's model request. A node with field mappings receives the projected inputs instead of the unselected upstream text; provenance still follows those inputs. Configure its input schema under contracts. Unbound joins omit skipped results. Conditions can select a JSON field before comparing their case-insensitive text marker.

**Parameters and reusable block interfaces** defines the team's parameter/input/output JSON Schemas and named output ports (`name`, `source`, `path`). Agent instructions can use `{{parameter_name}}`. Mission launch presents parameter controls and applies schema defaults. Child calls inherit matching parameters, then apply the node's overrides and per-task overrides. Expanded instructions are bounded to 12,000 characters; input envelopes and combined typed outputs are bounded to 64 KB.

## Dynamic recursive fanout

Choose **Dynamic recursive fanout**, select a saved child architecture or this team, and select an upstream source. Its task output selector first selects a sub-object; its task list selector then finds the task array, default `tasks`. Without an upstream source the list comes from mission inputs. Each entry is a task string or `{ "task": "Scoped work", "parameters": { ... } }`.

The node admits zero to its configured maximum of 1–8 children. Children run concurrently when capacity permits; routing and low-quota scheduling may serialize their requests. Empty lists return an empty result list. Duplicate tasks with identical parameters, oversized lists, malformed entries and insufficient root child-run capacity fail before allocating any child from that fanout. Output is `{ "results": [{ "task": "...", "output": ..., "child_run_id": "..." }] }`.

Fanout follows the same base-case rules as recursive calls: depth 1–4, optional upstream stop marker, finite frozen snapshots and the root's maximum 32 created child runs. Preview includes the configured maximum width when enforcing the 128-node expansion cap. Actual fanout can be smaller. **Pause repeated recursive work** compares normalized task, parameters, upstream data and team semantics with ancestors. Matching work pauses before another child is created. This is exact repetition detection, not semantic similarity; depth and budgets remain the hard limits.

## Reusable agents, blocks and versions

Use checkboxes in the team list to select a connected group, give it a block name, and choose **Extract into block**. Extraction saves an independent child architecture, replaces the selected nodes with a child-team node and reconnects boundary edges. Typed inputs become named component inputs; outputs preserve their originating node names. If a selected node uses an external task/condition source, include that source in the selection. Extraction validates the resulting graph atomically. Save the parent architecture after reviewing the collapsed graph.

The **Personal agent library** stores instructions, contracts, allowed tools, branch controls and preferred model route IDs. Insert by clicking a blueprint or dragging it onto the canvas; every copy gets a new node ID. Route preferences contain no API keys. Missing/unavailable routes are checked at execution. Library entries can be removed without changing already inserted nodes.

Saving a custom architecture writes a persistent numbered revision. **Saved version history** shows authors, timestamps and field diffs; restoring an older revision creates another revision. Concurrent saves reject stale revision numbers. Undo/redo handles the current editing session; revision history survives restart. Missions freeze their selected revisions at launch.

## Live task tree and branch controls

Open **Recursion tree** on any mission. The tree shows root and child tasks, depth, progress, status, assigned model routes, direct requests/tokens and each agent's inclusive branch consumption. A branch request/token allowance includes all descendants and retries, intersects every ancestor and root allowance, and admits reservations atomically. Empty allowances inherit the mission limit; zero disallows model requests. Token usage follows the same conservative/reporting rules as the mission budget.

Enable **Pause before this node for input review** to create a durable breakpoint. The tree displays the incoming task/typed data and offers **Approve / resume** or **Skip branch**. Manual **Hold branch** pauses at a safe boundary: an active model request completes first, and its response is retained as a pending result. Approval reuses that response. Other independent branches can complete while one is held. Checkpoints and decisions survive engine restart; approval resumes the relevant child and its waiting ancestors. Waiting time is excluded from active mission time.

**Edit paused branch** changes its prompt, role, route, contracts, allowances or child-team settings before approval. Node IDs and dependencies remain fixed. Completed siblings, completed child results that still match, conversation history and tool receipts remain saved. Active requests must reach a hold before editing. Tool grants can only narrow, and implicit vision requirements are retained. Editing a child-team branch cancels its unfinished descendants and resolves a new snapshot for that branch; completed work remains in the task tree. New requests still count against the original mission limits. Skipping prunes unfinished descendants; it cannot rewrite completed work.

## Shared design drafts

Create a shared draft from **Architectures → Share editable draft**, scoped to a project. Create an **architect** credential under **Control room → Team & setup → Team rooms** and link another desktop coordinator through its private TLS connection. Use **Shared designs** locally or select **Browse the linked coordinator**. Architect credentials can edit only shared drafts for their granted projects. They cannot run missions, apply artifacts, change grants or publish architectures.

Valid local changes sync after a short delay. The editor shows current participants, revision and recent field edits. Independent fields merge; overlapping fields return a conflict and keep the local draft available for export. **Reload shared draft** explicitly discards those local edits. Presence expires after 15 seconds; draft revisions/history persist in the coordinator database. A session supports at most 1,000 edit revisions; publish and create another session afterwards.

Only the owner can **Publish shared draft**. Publication checks the reviewed draft revision and the source architecture's revision, and rejects intervening changes. Published architectures enter the local mission library. Shared drafts remain separate from running mission snapshots. Collaborative state and credentials stay on the chosen coordinator; the public download domain does not host designs or inference.
