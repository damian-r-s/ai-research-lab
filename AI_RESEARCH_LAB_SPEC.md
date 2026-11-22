# AI RESEARCH & INNOVATION LAB
## Master technical specification and implementation instructions for Claude Code

**Version:** 1.0  
**Project:** Autonomous laboratory for research, analysis and innovation discovery  
**Environment:** Private server / Dell Pro Max Tower T2 computer  
**Operating mode:** 24/7  
**Primary goal:** Finding, researching, verifying and evaluating potential technological, scientific, industrial and business innovations.

> Note: this is the English version of specification v1.0. It is the governing specification.
> Later architectural decisions made by the project owner are recorded in `docs/DECISIONS.md`
> (U-1 to U-6). Where they conflict with this text, those decisions take precedence. The main
> ones are LangGraph orchestration, an English-only system, and the existing home server as the
> production host.

---

# 1. THE MOST IMPORTANT INSTRUCTION FOR CLAUDE CODE

You are building an autonomous AI research system called **AI Research & Innovation Lab**.

It is not an ordinary chatbot, assistant or set of independent agents talking to each other.

It must be an extensive, modular research platform that:

1. Independently discovers problems, needs, trends and technological opportunities.
2. Generates potential innovation concepts.
3. Conducts multi-stage, in-depth research on each selected concept.
4. Verifies information against independent sources.
5. Analyses competition, market, science, patents, technology, economics and risks.
6. Actively tries to refute its own hypotheses.
7. Assesses feasibility and economic potential.
8. Prepares complete research reports.
9. Preserves the history of every study and every decision.
10. Presents results to the user in a clear web dashboard.

The system must run around the clock, including when the user is not logged in.

**The most important rule: research quality matters more than the number of ideas generated.**

Do not force the creation of a particular number of proposals. The system may present one proposal, three proposals, or none at all if the available evidence is insufficient.

Do not build a second, executive system. The scope of this project ends at research, analysis, recommendation and documentation.

---

# 2. SYSTEM PHILOSOPHY

The laboratory should work like a combination of:

- a team of scientists,
- a team of engineers,
- a research and development department,
- a consulting firm,
- a market analysis team,
- a competitive intelligence team,
- a patent department,
- financial analysts,
- an independent audit team.

Do not limit the system to one industry.

Among other things, it should research:

- artificial intelligence and software,
- robotics and automation,
- electronics and semiconductors,
- energy and energy storage,
- medicine and medical technology,
- biotechnology,
- materials and industrial chemistry,
- manufacturing and industry,
- transport and logistics,
- mechanical devices,
- construction,
- agriculture,
- consumer technology,
- space technology,
- environmental technology,
- infrastructure solutions,
- other fields that can produce valuable innovations.

The system should not reject an idea just because it is not related to software.

It must also be able to recognise physical products, devices, materials, industrial processes and new applications of existing technologies.

---

# 3. HIGH-LEVEL ARCHITECTURE

Build the system as a modular platform with the following main components:

1. **Core Orchestrator** – central process management.
2. **Agent Runtime** – environment for running agents.
3. **Research Engine** – conducting research.
4. **Discovery Engine** – detecting problems and opportunities.
5. **Idea Generation Engine** – generating hypotheses and concepts.
6. **Evidence Engine** – collecting and verifying evidence.
7. **Market Intelligence** – market and competition research.
8. **Scientific Research** – analysis of scientific publications and studies.
9. **Patent Intelligence** – patent search and analysis.
10. **Technical Feasibility Engine** – technical feasibility assessment.
11. **Financial Analysis Engine** – economic modelling.
12. **Red Team Engine** – actively challenging and refuting ideas.
13. **Independent Review Engine** – independent review of results.
14. **Scoring Engine** – scoring and ranking proposals.
15. **Report Generator** – preparing reports.
16. **Knowledge Base** – persistent knowledge base.
17. **Audit & Event System** – history of events and decisions.
18. **Web Dashboard** – the user's central dashboard.
19. **Resource Manager** – management of hardware, models, tokens and costs.
20. **Security Layer** – access control, isolation and security.

Components must be logically separated from each other and have clearly defined interfaces.

Do not create one huge file containing all of the system's logic.

---

# 4. AGENT SYSTEM

## 4.1. Target number of roles

Design a system that allows at least 30–50 specialised agent roles to be defined and can easily be extended to 60 or more.

This does not mean running 50 models at the same time.

The system must assign tasks and resources dynamically.

Example roles:

### A. Management and coordination

1. Chief Research Orchestrator.
2. Research Project Manager.
3. Research Strategist.
4. Task Planner.
5. Research Quality Controller.

### B. Opportunity discovery

6. Trend Discovery Agent.
7. Problem Discovery Agent.
8. Customer Needs Analyst.
9. Opportunity Discovery Agent.
10. Innovation Scout.

### C. Science and technology

11. Scientific Research Agent.
12. AI Technology Specialist.
13. Software Engineering Researcher.
14. Electronics Researcher.
15. Robotics Researcher.
16. Energy Technology Researcher.
17. Materials Science Researcher.
18. Medical Technology Researcher.
19. Mechanical Engineering Researcher.
20. Industrial Technology Researcher.

### D. Market and business

21. Market Research Analyst.
22. Competitor Intelligence Agent.
23. Customer Research Analyst.
24. Product Strategy Analyst.
25. Business Model Analyst.
26. Financial Analyst.
27. Pricing Analyst.

### E. Verification

28. Patent Research Agent.
29. Technical Feasibility Analyst.
30. Regulatory Research Analyst.
31. Legal Risk Analyst.
32. Risk Analyst.
33. Red Team Agent.
34. Scientific Skeptic Agent.
35. Independent Verification Agent.

### F. Synthesis and reporting

36. Research Synthesizer.
37. Evidence Evaluator.
38. Innovation Scoring Agent.
39. Report Writer.
40. Final Review Agent.

This is an example role structure, not a fixed list of active processes.

Each role should have:
- a name,
- a responsibility,
- a description of its competencies,
- a system instruction,
- required inputs,
- an expected output format,
- allowed tools,
- constraints,
- quality criteria,
- the ability to be assigned an AI model,
- a cost and working-time limit.

Roles should be configurable without modifying the whole application's code.

## 4.2. Agent collaboration

Agents must not hold uncontrolled, endless conversations.

All collaboration must be carried out as a task with:
- an identifier,
- a goal,
- context,
- required data,
- a deadline or time limit,
- a token limit,
- a cost limit,
- completion criteria,
- a status,
- a result,
- sources,
- error information.

Agent results must be stored as structured data.

The orchestrator decides which roles are needed, in what order, and whether a study should be repeated.

---

# 5. THE AUTONOMOUS RESEARCH CYCLE

This is the most important element of the whole system.

The system should conduct research according to the following process.

## Stage 1 – Discovery

The system independently looks for:

- new trends,
- unsolved problems,
- unmet customer needs,
- new scientific discoveries,
- new patents,
- technological changes,
- new manufacturing possibilities,
- changes in the cost and availability of technologies,
- areas where existing solutions are inefficient.

The result is a set of potential research areas.

## Stage 2 – Hypothesis generation

The system generates possible solutions.

Each hypothesis receives:
- a unique identifier,
- a problem description,
- a proposed solution,
- a target group,
- potential applications,
- a preliminary novelty assessment,
- a preliminary feasibility assessment,
- sources of inspiration,
- a list of unknowns.

At this stage, an idea must not yet be presented as a confirmed innovation.

## Stage 3 – Preliminary screening

Reject ideas that:
- have no clear problem,
- are based on obviously false assumptions,
- have no real application,
- duplicate existing solutions without a significant advantage,
- have an extremely unfavourable ratio of costs to potential benefits.

Every rejection must have a justification.

## Stage 4 – In-depth research

For promising concepts, launch the appropriate teams of agents.

Analyse in parallel:

1. The problem and user needs.
2. The market and the size of potential demand.
3. Competition.
4. Existing products and solutions.
5. Scientific publications.
6. Patents.
7. Available technologies.
8. Technical feasibility.
9. Potential production costs.
10. Prototype development costs.
11. Deployment costs.
12. Operating costs.
13. Possible revenue models.
14. Regulations and legal issues.
15. Technological and business risks.

Each area should have its own partial report.

## Stage 5 – Red Team

Every seriously considered idea must go through critical analysis.

The Red Team must actively look for reasons why the project might fail.

Example questions:

- Does the problem really exist?
- Will customers really pay for the solution?
- Does a similar product already exist?
- Is the proposed technology available?
- Will the production cost exceed the value of the product?
- Are there patent restrictions?
- Is there a cheaper and simpler solution?
- Does research confirm the assumptions?
- Is the data reliable?
- What are the weakest elements of the concept?
- What would have to happen for the idea to turn out to be wrong?

The Red Team must not be just another agent confirming the work of the others.

It must have separate instructions and the task of finding counterarguments.

## Stage 6 – Independent verification

The verifying agent must check the most important claims against the available sources.

It must not automatically accept the results of other agents.

Every important claim should be labelled as:
- confirmed,
- partially confirmed,
- unconfirmed,
- contradicted by the available data,
- requiring further research.

Important claims should be backed by primary sources where available.

The system should detect:
- missing sources,
- duplication of the same source,
- outdated data,
- conflicting information,
- incorrect citations,
- claims that go beyond the available evidence.

## Stage 7 – Final assessment

The system prepares an assessment based on explicit criteria.

Example scoring:

| Criterion | Weight |
|---|---:|
| Problem significance | 15% |
| Market potential | 15% |
| Competitive advantage | 10% |
| Novelty of the solution | 10% |
| Technical feasibility | 15% |
| Economic potential | 15% |
| Quality of evidence | 10% |
| Risk and limitations | 10% |

Weights should be configurable.

Do not equate the score with an objective probability of success.

Every assessment must have a justification, a confidence level and an indication of missing data.

## Stage 8 – Final report

The report goes to the user's dashboard.

The system may:
- present a recommendation,
- send the project for further research,
- reject the concept,
- withhold the assessment due to lack of data.

Do not force a positive result.

---

# 6. DATA SOURCES AND INTERNET RESEARCH

Design the data sources module as an independent set of adapters.

Potential sources:

### Science
- arXiv,
- PubMed,
- Crossref,
- OpenAlex,
- Semantic Scholar,
- Europe PMC,
- scientific journal websites.

### Patents
- Google Patents,
- Espacenet,
- WIPO PATENTSCOPE,
- USPTO,
- other available registries.

### Market
- manufacturer websites,
- product catalogues,
- technical documentation,
- public industry reports,
- shop websites,
- public statistical data,
- official price lists.

### Technology
- manufacturer documentation,
- software repositories,
- standards documentation,
- technical publications,
- reports on new technologies.

Every source must have:
- a name,
- a URL,
- a retrieval date,
- a publication date, if known,
- a source type,
- related claims,
- a reliability rating,
- an availability status.

Do not assume that every website can be retrieved automatically. Use official APIs, rate limits, terms of use and appropriate error-handling mechanisms.

The system must not invent sources or data.

---

# 7. RESEARCH REPORT – REQUIRED STRUCTURE

Every recommended proposal should receive a separate, complete report.

The report must contain:

1. An executive summary for the user.
2. The name and description of the concept.
3. What problem it solves.
4. Where the idea came from.
5. What evidence confirms the existence of the problem.
6. The target group.
7. Analysis of customers and their needs.
8. Market analysis.
9. Competitive analysis.
10. Existing solutions.
11. Analysis of novelty and advantage.
12. Review of scientific publications.
13. Patent review.
14. Proposed technical architecture of the product.
15. Feasibility assessment.
16. Required components and technologies.
17. Estimated prototype cost.
18. Estimated MVP cost.
19. Estimated production or deployment cost.
20. Operating costs.
21. Potential selling prices.
22. Possible revenues.
23. Estimated margin.
24. Break-even point.
25. Pessimistic scenario.
26. Realistic scenario.
27. Optimistic scenario.
28. Technical risks.
29. Financial risks.
30. Market risks.
31. Legal and regulatory risks.
32. Red Team report.
33. Independent verification report.
34. Confidence level.
35. Missing information.
36. Sources.
37. Final recommendation.

Every number must have a defined status:
- value confirmed by a source,
- estimate,
- assumption,
- model scenario.

Do not present forecasts as facts.

---

# 8. CENTRAL WEB DASHBOARD

The dashboard is the main place for managing the laboratory.

It must work on a computer and on a phone.

## 8.1. Main dashboard

Display:
- laboratory status,
- number of active studies,
- number of pending tasks,
- number of completed studies,
- number of rejected concepts,
- number of proposals awaiting a decision,
- active agent roles,
- CPU usage,
- RAM usage,
- GPU usage,
- VRAM usage,
- disk usage,
- token consumption,
- API costs,
- errors and warnings.

Data must come from the real state of the system.

Do not display artificially generated statistics.

## 8.2. Research project view

Every project should have:
- a name,
- an identifier,
- a description,
- a creation date,
- a status,
- a research stage,
- a priority,
- a current score,
- a change history,
- a list of agents,
- a list of tasks,
- documents,
- sources,
- analysis results,
- Red Team critique,
- unresolved questions.

## 8.3. Agents view

The user should see:
- role name,
- assigned model,
- current task,
- status,
- start time,
- end time,
- tokens used,
- cost,
- last result,
- errors.

## 8.4. Project history

The user must be able to trace:
- where the idea came from,
- which sources inspired it,
- who analysed what,
- which hypotheses were put forward,
- what evidence was found,
- what arguments the Red Team raised,
- what was rejected,
- why the system issued its final recommendation.

## 8.5. Controls

The dashboard should allow:
- starting a study,
- pausing,
- resuming,
- stopping,
- retrying a task,
- requesting additional analysis,
- changing priority,
- accepting a proposal,
- rejecting a proposal,
- downloading a report,
- reviewing sources.

Stopping must actually stop tasks and processes, not just change a status label.

---

# 9. TECHNICAL ARCHITECTURE

Propose and use a modern, modular technology stack.

Preferred solutions:

### Backend
- Python.
- FastAPI.
- Pydantic.
- LangGraph or a comparable orchestration framework.
- REST API.
- WebSocket or Server-Sent Events.

### Frontend
- Next.js.
- React.
- TypeScript.
- Responsive interface.

### Database
- PostgreSQL.
- pgvector.
- Persistent storage of research results.
- Document versioning.

### Queues and tasks
- Redis.
- Durable task queue.
- Worker processes.
- Task retry handling.
- Idempotent operations.

### AI models
- Ollama as one of the possible local backends.
- Adapters for external APIs.
- Ability to add and swap model providers.
- Model routing depending on the type of task.

### Infrastructure
- Docker.
- Docker Compose to start with.
- Separate containers for the main components.
- Ability to migrate to larger infrastructure in the future.

These are architectural preferences, not a reason to use every technology without thought. Assess their suitability and justify your choices before implementation.

---

# 10. AI MODEL LAYER

Do not assume that one model should perform all tasks.

Build a Model Provider / Model Router layer.

It should support:

- local models,
- external APIs,
- different models for different roles,
- changing the model without changing the application logic,
- token limits,
- cost limits,
- response time measurement,
- retry,
- fallback,
- model availability checks.

For example:

- simple classifications can be performed by smaller models,
- data extraction can be performed by specialised models,
- difficult analyses can go to stronger models,
- critique and verification can be performed by different models than those that produced the original analysis.

The system must distinguish local compute costs from the real costs of external APIs.

If a model is unavailable, the system must report this explicitly rather than pretend the task was performed.

---

# 11. HARDWARE AND SCALING

Current hardware base:

- Dell Pro Max Tower T2.
- Intel Core Ultra 5 – exact model to be confirmed.
- 32 GB RAM.
- 1 TB disk.
- Power supply reported as 1500 W.
- Upgrade options available.

The target configuration under consideration is a powerful local laboratory:

- An NVIDIA RTX PRO class GPU with a large amount of VRAM.
- In particular, to be verified: NVIDIA RTX PRO 6000 Blackwell Workstation Edition with 96 GB VRAM.
- 128 GB RAM or more, depending on official platform support.
- A fast NVMe disk of 4 TB or more.
- Additional space for the research archive.
- Adequate cooling and power.

**IMPORTANT:** Do not treat GPU compatibility as finally confirmed solely on the basis of the declared power supply rating.

Before purchase, the following must be confirmed:
- the exact Dell configuration,
- the power supply variant and cables,
- official support for the specific card,
- card dimensions,
- cooling,
- memory support,
- available slots and motherboard limitations.

However, do not block the software implementation while waiting for the hardware upgrade.

The system must already work on the current configuration, with the ability to add GPUs, RAM and other resources.

Build a Resource Manager that detects the resources that are actually available.

Do not assume that all tasks must run locally. The system must also support external models, with cost and privacy controls.

---

# 12. KNOWLEDGE BASE AND SYSTEM MEMORY

The laboratory must have persistent memory.

It must not lose history after a restart.

Store:
- projects,
- hypotheses,
- tasks,
- agent results,
- documents,
- sources,
- citations,
- decisions,
- scores,
- report versions,
- event history,
- errors,
- costs.

Build semantic search capability.

The system should recognise that a new idea may be similar to a previously analysed project.

Do not repeat the same research unnecessarily.

When returning to an earlier project, the system must know what has already been checked, which data is outdated and what needs to be refreshed.

---

# 13. AUTONOMY AND SCHEDULING

The system must run 24/7.

Design a scheduler that can:
- initiate new discovery cycles,
- continue unfinished research,
- retry tasks after errors,
- update outdated data,
- control the queue,
- allocate resources,
- limit the number of parallel tasks.

Do not set a rigid requirement to deliver a particular number of ideas per week.

Example rhythm:
- continuous discovery and data collection,
- periodic screening of hypotheses,
- multi-stage research of selected concepts,
- periodic reports,
- on-demand reports.

A report may state that no proposal meeting the requirements was found in a given period.

That is a valid result.

---

# 14. COSTS AND LIMITS

Introduce a budget system.

Every project should have:
- a token limit,
- an external cost limit,
- a time limit,
- a limit on the number of attempts,
- a limit on parallel tasks.

The administrator should be able to configure:
- a daily budget,
- a monthly budget,
- the maximum cost of a single study,
- limits for individual models,
- maximum resource usage.

When a limit is reached:
- stop or pause the task,
- record the reason,
- show the state in the dashboard,
- do not continue without appropriate approval or configuration.

Do not allow infinite agent loops.

---

# 15. SECURITY

The system will autonomously retrieve data and run processes, so security is critical.

Requirements:

1. Roles and permissions based on the principle of least privilege.
2. Isolation of code execution.
3. Restricted agent access to files.
4. Secure storage of API keys.
5. No secrets in the repository.
6. Logging of actions.
7. Input data validation.
8. Protection against prompt injection.
9. Separation of untrusted internet data from system instructions.
10. Database backup.
11. Disaster recovery mechanism.
12. A global system stop button.
13. Dashboard access control.
14. Encryption of communication.
15. Restriction of outbound traffic where possible.

Agents must not independently:
- buy hardware,
- make payments,
- contact customers,
- publish materials,
- send external messages,
- start paid services without authorisation.

The system may prepare a recommendation and a plan of action, but external operations require the user's approval.

---

# 16. DOCUMENTS AND HISTORY

Every project must have a complete set of documents.

Required features:
- document creation,
- versioning,
- version comparison,
- edit history,
- PDF and Markdown export,
- linking documents to tasks and sources.

Do not irreversibly overwrite earlier results.

Every significant change should be reproducible.

---

# 17. TESTS AND RELIABILITY

The system must not be considered ready just because the frontend opens.

Prepare:

- unit tests,
- integration tests,
- API tests,
- workflow tests,
- data persistence tests,
- restart tests,
- cost limit tests,
- task stopping tests,
- model error tests,
- security tests,
- permission tests.

In particular, check:
- that a task is not executed twice after a restart,
- that the status matches the actual state,
- that tasks are not lost after a failure,
- that an interrupted study can be resumed,
- that the report has real sources,
- that an agent cannot approve its own work as independent verification.

---

# 18. IMPLEMENTATION ORDER

Do not try to build the whole system in one step.

Deploy it in stages, but design the architecture so that further modules can be added without rebuilding the foundations.

## Phase 0 – Analysis and design

Before writing a large amount of code:

1. Examine the existing repository.
2. Check the environment.
3. Prepare an architecture document.
4. Prepare the directory structure.
5. Identify technical risks.
6. Prepare an implementation plan.
7. Identify decisions requiring approval.

Do not delete existing code without checking what it does.

## Phase 1 – Foundation

Build:
- backend,
- frontend,
- PostgreSQL,
- migrations,
- configuration system,
- logging,
- basic authentication,
- event system,
- Docker Compose,
- application health monitoring.

Result: a working platform skeleton.

## Phase 2 – Orchestrator and tasks

Build:
- Core Orchestrator,
- durable queue,
- worker runtime,
- task statuses,
- retry,
- stopping and resuming,
- time and cost limits.

Result: the system can manage real tasks.

## Phase 3 – Model layer

Build:
- local model adapter,
- external API adapter,
- Model Router,
- token measurement,
- cost measurement,
- error handling.

Result: roles can perform tasks using different models.

## Phase 4 – First real research workflow

Implement one complete process:

1. The user creates a research project.
2. The Discovery Agent identifies a problem.
3. The Idea Agent prepares a hypothesis.
4. The Research Agent collects sources.
5. The Market Analyst analyses the market.
6. The Technical Analyst assesses feasibility.
7. The Red Team looks for arguments against.
8. The Independent Reviewer verifies the results.
9. The Research Synthesizer prepares the report.
10. The system records the whole history.
11. The user sees the result in the dashboard.

This must be a real workflow, not a demonstration with fictitious data.

## Phase 5 – Research engines

Add further modules:
- science,
- patents,
- competition,
- finance,
- regulations,
- technologies,
- risk.

## Phase 6 – Agent expansion

Add further roles, configurations, tools and specialisations.

## Phase 7 – 24/7 autonomy

Build:
- scheduler,
- cyclical discovery,
- autonomous continuation of research,
- failure recovery,
- resource monitoring,
- periodic reporting.

## Phase 8 – Extended dashboard

Refine:
- process visualisation,
- timeline,
- agent details,
- reports,
- sources,
- history,
- control and audit.

## Phase 9 – Optimisation and scaling

Optimise:
- model routing,
- GPU usage,
- concurrency,
- memory,
- costs,
- search speed,
- knowledge base performance.

---

# 19. WORKING RULES FOR CLAUDE CODE

Work like an experienced architect and a team of software engineers.

The following rules apply:

1. Analyse first, then implement.
2. Do not create fictitious features just to make the interface look finished.
3. Do not use mock-ups in places where the system is supposed to perform real operations.
4. Do not mark a task as completed if it has not been done.
5. Do not hide errors.
6. Do not ignore architectural problems.
7. Do not write monolithic code.
8. Do not duplicate logic.
9. Use typing and validation.
10. Document significant decisions.
11. Add tests for new features.
12. Keep migrations compatible.
13. Do not put API keys in the code.
14. Do not introduce uncontrolled autonomy.
15. Do not start paid services without approval.
16. Do not start the next phase if the foundation of the previous one does not pass its tests.

After each phase, present:
- what was built,
- which files were added,
- what changes were made,
- which tests were run,
- which tests passed,
- which errors remain,
- what the limitations are,
- what you propose next.

If you encounter an important architectural decision, present the options and justify your recommendation.

Do not ask about minor details that you can safely resolve as an experienced engineer.

However, do not guess on matters of security, costs, destructive changes and external operations.

---

# 20. COMPLETION CRITERIA

The laboratory can be considered a first working version only when:

- a research project can be created,
- the system can execute a real workflow,
- agents perform their assigned tasks,
- results are stored in the database,
- sources are available and linked to claims,
- the Red Team can challenge a concept,
- independent verification works separately,
- the system produces a report,
- the user can see the history of a study,
- tasks can be stopped and resumed,
- the system survives a restart,
- costs and resource usage are measured,
- errors are recorded,
- automated tests pass.

Do not consider the whole laboratory finished after building the first workflow. That is only a working foundation for further expansion.

---

# 21. ULTIMATE GOAL

Create an autonomous, multi-industry AI Research & Innovation Lab that continuously searches for valuable technological and business opportunities, conducts multidimensional research, challenges its own conclusions, independently verifies evidence and presents the user only with well-justified recommendations.

The system must be:

- ambitious,
- scalable,
- modular,
- transparent,
- fault-tolerant,
- secure,
- extensible,
- ready for 24/7 operation.

**Do not optimise it for the number of agents or the number of ideas. Optimise it for the quality of discoveries, the reliability of evidence and the value of the final recommendations.**

This is the overriding principle of the entire project.
