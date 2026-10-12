from .schema import Architecture


def template(id, name, description, agents, edges):
    return Architecture(id=id, name=name, description=description, builtin=True,
        nodes=[dict(id=a[0], name=a[1], role=a[2], instructions=a[3], x=a[4], y=a[5]) for a in agents],
        edges=[dict(source=a, target=b) for a, b in edges]).model_dump()


TEMPLATES = [
    template("supervisor", "Supervisor", "A planner delegates to two specialists; a synthesizer joins their work.", [
        ("planner", "Planner", "Break down the task", "Write a concrete plan and assign useful directions to specialists.", 0, 120),
        ("researcher", "Researcher", "Explore evidence and options", "Evaluate facts and options. Do not invent sources or claim to have browsed.", 280, 0),
        ("builder", "Builder", "Develop a practical solution", "Produce a concrete solution with assumptions and tradeoffs.", 280, 240),
        ("synthesizer", "Synthesizer", "Join the team's work", "Integrate the supplied work into one useful final answer.", 560, 120),
    ], [("planner", "researcher"), ("planner", "builder"), ("researcher", "synthesizer"), ("builder", "synthesizer")]),
    template("pipeline", "Pipeline", "Pass work through planning, execution, and review.", [
        ("planner", "Planner", "Plan the approach", "Define requirements and a concrete approach.", 0, 100),
        ("builder", "Builder", "Produce the work", "Carry out the plan and produce the requested deliverable.", 280, 100),
        ("reviewer", "Reviewer", "Review and finalize", "Check the supplied output, fix errors, and return the final deliverable.", 560, 100),
    ], [("planner", "builder"), ("builder", "reviewer")]),
    template("parallel", "Parallel / join", "Independent perspectives work in parallel, then merge.", [
        ("analyst", "Analyst", "Evaluate the problem", "Analyze requirements, constraints, and opportunities.", 0, 0),
        ("skeptic", "Skeptic", "Challenge assumptions", "Find weak assumptions, failure modes, and alternatives.", 0, 230),
        ("synthesizer", "Synthesizer", "Merge perspectives", "Reconcile the supplied perspectives and deliver a clear answer.", 320, 115),
    ], [("analyst", "synthesizer"), ("skeptic", "synthesizer")]),
    template("critic", "Creator / critic", "Draft, critique, then revise with the critique in context.", [
        ("creator", "Creator", "Make a first draft", "Produce a substantive first version of the requested work.", 0, 100),
        ("critic", "Critic", "Find specific improvements", "Assess the draft and propose specific, justified improvements.", 280, 100),
        ("reviser", "Reviser", "Revise and deliver", "Use the original draft and critique to deliver a stronger final version.", 560, 100),
    ], [("creator", "critic"), ("creator", "reviser"), ("critic", "reviser")]),
]

TEMPLATES += [
    template("baseline", "Single-agent baseline", "One agent for comparison with a swarm.", [
        ("worker", "Worker", "Complete the task", "Complete the task with the granted tools and available evidence.", 200, 100),
    ], []),
    template("handoff", "Specialist handoff", "Triage, specialist work, and final handoff.", [
        ("triage", "Triage", "Classify and scope the task", "Identify the specialist work needed and preserve important context.", 0, 100),
        ("specialist", "Specialist", "Resolve the scoped task", "Use the triage findings and available tools to solve the problem.", 280, 100),
        ("finisher", "Finisher", "Complete the handoff", "Check deliverables and state unfinished work explicitly.", 560, 100),
    ], [("triage", "specialist"), ("specialist", "finisher")]),
    template("hierarchy", "Hierarchical team", "A lead can create capped specialist subtasks through agent.delegate.", [
        ("lead", "Team lead", "Coordinate bounded specialist tasks", "Use agent.delegate when granted; otherwise break down the task yourself. Integrate actual specialist results.", 0, 100),
        ("reviewer", "Reviewer", "Review delegated findings", "Check the supplied results against the task.", 300, 100),
    ], [("lead", "reviewer")]),
    template("blackboard", "Shared blackboard", "Parallel specialists use permissioned project memory, then join.", [
        ("investigator", "Investigator", "Gather and record evidence", "Use memory.search and memory.save when granted. Record findings with sources and uncertainty.", 0, 0),
        ("checker", "Checker", "Investigate competing explanations", "Use shared memory when granted. Keep differing findings rather than overwriting them.", 0, 230),
        ("synthesizer", "Synthesizer", "Integrate the shared findings", "Read shared memory when available; reconcile findings and identify conflicts.", 330, 100),
    ], [("investigator", "synthesizer"), ("checker", "synthesizer")]),
    template("ensemble", "Debate / ensemble", "Three independent decisions with critique and synthesis.", [
        ("one", "Perspective A", "Make an independent decision", 'Return JSON with a string decision and justification.', 0, 0),
        ("two", "Perspective B", "Challenge the default decision", 'Return JSON with a string decision and justification.', 0, 180),
        ("three", "Perspective C", "Explore alternatives", 'Return JSON with a string decision and justification.', 0, 360),
        ("vote", "Majority vote", "Count structured decisions", "", 300, 180),
    ], [("one", "vote"), ("two", "vote"), ("three", "vote")]),
]
next(t for t in TEMPLATES if t["id"] == "ensemble")["nodes"][-1]["kind"] = "vote"
revision = dict(next(t for t in TEMPLATES if t["id"] == "critic"), id="revision", name="Bounded revision", max_rounds=3,
    description="Draft, critique and revise for at most three checkpointed rounds.")
TEMPLATES.append(revision)

# Topology presets are ordinary, editable graphs; no preset needs extra grants.
TEMPLATES += [
    template("map_reduce", "Map / reduce", "Scope work once, explore three dimensions in parallel, and reduce the findings.", [
        ("scope", "Scope", "Define the units of work", "Define requirements, boundaries and useful dimensions to investigate.", 0, 180),
        ("facts", "Evidence mapper", "Map evidence", "Develop the evidence dimension. Mark unknowns and cite only available sources.", 280, 0),
        ("options", "Options mapper", "Map alternatives", "Develop alternatives and their tradeoffs.", 280, 180),
        ("risks", "Risk mapper", "Map constraints and risks", "Develop constraints, failure modes and mitigations.", 280, 360),
        ("reduce", "Reducer", "Combine the mapped work", "Reduce the supplied findings into a coherent deliverable without dropping disagreements.", 560, 180),
    ], [("scope", n) for n in ("facts", "options", "risks")] + [(n, "reduce") for n in ("facts", "options", "risks")]),
    template("jury", "Debate with a judge", "Two advocates develop opposing cases, cross-examine them, and submit to a judge.", [
        ("for", "Advocate", "Develop the strongest supporting case", "Develop a grounded supporting case. State assumptions explicitly.", 0, 0),
        ("against", "Challenger", "Develop the strongest opposing case", "Develop a grounded opposing case and useful alternatives.", 0, 220),
        ("cross", "Cross-examiner", "Test both cases", "Identify unsupported claims and decisive evidence in both supplied cases.", 280, 110),
        ("judge", "Judge", "Make a reasoned decision", "Assess the two cases and cross-examination. Explain the decision and remaining uncertainty.", 560, 110),
    ], [("for", "cross"), ("against", "cross"), ("cross", "judge")]),
    template("test_driven", "Test-driven team", "Define acceptance checks, implement, then test and repair in two bounded rounds.", [
        ("tests", "Test designer", "Define acceptance checks", "Define meaningful acceptance cases before implementation. Write reviewable tests if coding is requested.", 0, 100),
        ("implement", "Implementer", "Implement to the acceptance checks", "Implement the requested work against the supplied checks. Use tools only when granted.", 280, 100),
        ("verify", "Test and repair", "Verify and repair the work", "Run granted named checks if available. Report actual outcomes, repair failures, and identify checks you could not run.", 560, 100),
    ], [("tests", "implement"), ("implement", "verify")]),
    template("evidence", "Evidence and fact-check", "Investigate independently, audit evidence, then produce a sourced synthesis.", [
        ("primary", "Primary investigator", "Gather primary evidence", "Use granted search/retrieval tools. Prioritize primary sources and preserve source IDs and URLs. Do not invent retrieval.", 0, 0),
        ("counter", "Counter-evidence", "Investigate competing explanations", "Look for conflicting evidence and alternative explanations with available tools.", 0, 220),
        ("audit", "Evidence auditor", "Check provenance and claims", "Audit supplied claims and citations. Separate verified retrieval, inference and missing evidence.", 280, 110),
        ("report", "Report writer", "Write the evidence-backed result", "Synthesize audited findings, cite available sources, and state limitations.", 560, 110),
    ], [("primary", "audit"), ("counter", "audit"), ("audit", "report")]),
    template("red_team", "Red team / blue team", "Develop a solution, challenge its failure modes, then harden and review it.", [
        ("blue", "Builder", "Develop a defensible solution", "Produce a concrete solution with explicit assumptions and boundaries.", 0, 100),
        ("red", "Red team", "Stress-test the design", "Challenge the supplied design with realistic failure cases and evidence.", 280, 100),
        ("harden", "Hardener", "Address the identified failures", "Revise the design to address the supplied failures, preserving useful tradeoffs.", 560, 100),
        ("review", "Final reviewer", "Assess residual risk", "Review the revised solution and state residual risks and validation steps.", 840, 100),
    ], [("blue", "red"), ("red", "harden"), ("harden", "review")]),
    template("router", "Router and specialists", "A structured triage chooses one specialist; skipped branches do not call a model.", [
        ("router", "Router", "Choose one specialty", 'Return only JSON {"route":"CODE"}, {"route":"RESEARCH"}, or {"route":"GENERAL"} for the task. Choose exactly one.', 0, 180),
        ("code", "Coding specialist", "Implement the coding task", "Produce reviewable code using granted tools and actual check results.", 280, 0),
        ("research", "Research specialist", "Investigate the research task", "Use available evidence and granted retrieval tools; preserve provenance.", 280, 180),
        ("general", "General specialist", "Solve the general task", "Produce a concrete, useful answer with assumptions and tradeoffs.", 280, 360),
        ("deliver", "Finisher", "Deliver the chosen specialist's work", "Use the completed specialist output; ignore skipped branches. Check it against the original task.", 560, 180),
    ], [("router", n) for n in ("code", "research", "general")] + [(n, "deliver") for n in ("code", "research", "general")]),
    template("review_board", "Specialist review board", "A draft receives parallel quality, feasibility and safety reviews before revision.", [
        ("draft", "Drafter", "Create the deliverable", "Produce a substantive first version.", 0, 180),
        ("quality", "Quality reviewer", "Check accuracy and completeness", "Find concrete errors and missing requirements in the draft.", 280, 0),
        ("feasibility", "Feasibility reviewer", "Check practical feasibility", "Assess implementation effort, resources and operational constraints.", 280, 180),
        ("safety", "Risk reviewer", "Check harmful failure modes", "Assess realistic privacy, security and user-impact risks relevant to this task.", 280, 360),
        ("revise", "Editor", "Integrate the reviews", "Revise the draft using all supplied reviews and explain unresolved conflicts.", 560, 180),
    ], [("draft", n) for n in ("quality", "feasibility", "safety")] + [(n, "revise") for n in ("quality", "feasibility", "safety")]),
    template("diamond", "Diamond comparison", "Plan once, build two alternative solutions, compare, and deliver the best approach.", [
        ("plan", "Planner", "Define the comparison criteria", "Define requirements and two distinct approaches worth comparing.", 0, 100),
        ("simple", "Simple approach", "Develop the simplest viable solution", "Develop a simple solution against the supplied requirements.", 280, 0),
        ("robust", "Robust approach", "Develop a resilient alternative", "Develop a resilient alternative against the supplied requirements.", 280, 220),
        ("compare", "Comparator", "Compare concrete outcomes", "Compare both solutions against the original criteria and produce the final deliverable.", 560, 100),
    ], [("plan", "simple"), ("plan", "robust"), ("simple", "compare"), ("robust", "compare")]),
    template("spec_build_verify", "Specify / build / verify", "An explicit specification guides implementation and independent verification.", [
        ("spec", "Specifier", "Write a testable specification", "Translate the task into constraints, acceptance criteria and an implementation plan.", 0, 100),
        ("build", "Builder", "Implement the specification", "Produce the requested implementation against the supplied specification.", 280, 100),
        ("verify", "Independent verifier", "Verify the deliverable", "Independently assess acceptance criteria using granted tools. Correct defects and distinguish observed from proposed checks.", 560, 100),
    ], [("spec", "build"), ("spec", "verify"), ("build", "verify")]),
    template("recursive_decompose", "Recursive decomposition", "Scope a smaller task, call this team again, and synthesize; stop at depth two or BASE_CASE.", [
        ("scope", "Decomposer", "Scope the next smaller task", "Choose one concrete smaller task needed to solve this task. Output only that task. If it is already directly solvable, prefix BASE_CASE and describe what to solve.", 0, 100),
        ("recurse", "Recursive solver", "Solve the scoped task", "At the base case, solve the supplied task directly using available evidence and granted tools. Return useful work, not another delegation request.", 280, 100),
        ("combine", "Synthesizer", "Integrate the recursive result", "Use the scoped task and actual child result to answer this level's original task. State unresolved work.", 560, 100),
    ], [("scope", "recurse"), ("scope", "combine"), ("recurse", "combine")]),
    template("recursive_review", "Recursive critique and repair", "Review a draft, recursively repair remaining defects, and finalize; stop at depth two or APPROVED.", [
        ("draft", "Drafter", "Create or repair the work", "Create the requested work, incorporating supplied defects and previous findings.", 0, 100),
        ("review", "Reviewer", "Specify remaining repairs", "Review the draft. If it meets the task, prefix APPROVED and include the deliverable. Otherwise describe a concrete repair task and include enough of the draft to repair it.", 280, 100),
        ("repair", "Recursive repairer", "Repair the reviewed work", "At the base case, perform the requested repair directly. If APPROVED, preserve the approved deliverable. Do not request another delegation.", 560, 100),
        ("deliver", "Finisher", "Deliver the repaired result", "Deliver the repaired or approved work, preserving the original requirements.", 840, 100),
    ], [("draft", "review"), ("review", "repair"), ("repair", "deliver")]),
    template("recursive_fork", "Recursive fork / join", "Two scoped branches call this team and join. One nested level by default keeps the tree small.", [
        ("left_task", "First task planner", "Scope the first branch", "Choose the first distinct part of this task that can be solved independently. Output only its task; prefix BASE_CASE when directly solvable.", 0, 0),
        ("right_task", "Second task planner", "Scope the second branch", "Choose a second distinct independent part of this task. Output only its task; prefix BASE_CASE when directly solvable.", 0, 240),
        ("left", "First recursive branch", "Solve the first branch", "At the base case solve this branch directly, with granted tools and evidence. Do not delegate again.", 280, 0),
        ("right", "Second recursive branch", "Solve the second branch", "At the base case solve this branch directly, with granted tools and evidence. Do not delegate again.", 280, 240),
        ("join", "Joiner", "Combine the branch results", "Combine both actual branch results into the requested deliverable. Identify gaps and conflicts.", 560, 120),
    ], [("left_task", "left"), ("right_task", "right"), ("left", "join"), ("right", "join")]),
]

for t in TEMPLATES:
    if t["id"] in {"test_driven", "spec_build_verify"}: t["category"] = "coding"
    elif t["id"] in {"evidence", "blackboard"}: t["category"] = "research"
    elif t["id"] in {"ensemble", "jury", "red_team", "review_board", "diamond"}: t["category"] = "decision"
    elif t["id"].startswith("recursive_"): t["category"] = "recursive"
    else: t["category"] = "general"
    if t["id"] == "test_driven": t["max_rounds"] = 2
    if t["id"] == "router":
        t["nodes"][0]["output_schema"] = dict(type="object", properties=dict(route=dict(type="string", enum=["CODE", "RESEARCH", "GENERAL"])), required=["route"], additionalProperties=False)
        for node in t["nodes"][1:4]: node["run_if"] = dict(source="router", contains='"' + node["id"].upper() + '"')
    if t["id"] in {"recursive_decompose", "recursive_review", "recursive_fork"}:
        for node in t["nodes"]:
            source = {"recurse": "scope", "repair": "review", "left": "left_task", "right": "right_task"}.get(node["id"])
            if source:
                node.update(kind="recursive", subswarm_id="self", task_source=source, max_depth=1 if t["id"] == "recursive_fork" else 2,
                    recursion_stop_contains="APPROVED" if t["id"] == "recursive_review" else "BASE_CASE")

# Catch invalid built-ins before they can enter the persistent catalogue.
TEMPLATES = [Architecture.model_validate(t).model_dump() for t in TEMPLATES]
