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
