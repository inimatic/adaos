# AdaOS Solution Directions

Status: authoritative portfolio framing. The directions below are product
hypotheses at different maturity levels, not a single delivery commitment.

Last reviewed: 2026-08-07.

Read [AdaOS Product Model](index.md) first. It defines the difference between a
deployment profile, solution pack, solution agent, endpoint, and channel.

## Portfolio Summary

| Direction | Product kind | Maturity | Primary promise |
| --- | --- | --- | --- |
| AdaOS Home | Deployment profile and household solution composition | Strategic direction; reference topology | A privately governed assistant environment across people, rooms, routines, and devices. |
| AdaOS Research Fabric | Reusable solution framework | Proposed target architecture | Reproducible, governed studies with portable evidence and replaceable providers. |
| aResearcher | Solution agent and workbench | Strategic direction | A human-governed research loop from question through evidence and publication support. |
| AdaOS Campus | Institutional deployment profile and education solution family | Strategic direction | Course and learning environments that preserve roles, consent, provenance, and private data boundaries. |
| AdaOS Enterprise | Organizational deployment profile and solution family | Long-term direction | Governed assistant environments for teams, processes, integrations, and organizational policy. |
| reDevice | Cross-cutting endpoint family | Implemented | Reuse a physical device as an evolving AdaOS interaction, media, sensing, or control endpoint. |

Shared primitives may already be implemented while a named direction remains
strategic. Product maturity must be assessed from end-to-end solution evidence,
not from the existence of an individual API or schema.
Durable advantage claims must follow the proof and wording boundaries in
[Advantage Claims And Evidence](advantage-claims.md).

## AdaOS Home

**Product kind:** deployment profile plus household solution composition.

**Maturity:** strategic direction using the simplest current hub/member and
browser/device topology as a reference.

### Value promise

AdaOS Home provides one privately governed environment in which a person or
household can combine personal assistance, shared routines, local devices, and
guest access without making a remote service the sole authority over identity,
policy, or long-lived state.

### Reference topology and actors

The reference shape is one named Assistant environment backed by a household
hub, optional member nodes, browsers, and reDevice or other endpoints.

Expected actors include owner, co-owner, household member, child where policy
requires guardian approval, temporary guest, device, and software agent. Role
and membership remain separate from the person's profile.

### Domain objects

- household and place;
- room or zone;
- person profile and preferences;
- membership and grant;
- device, endpoint, capability, and assignment;
- routine, automation, and pending action;
- private memory and household-shared memory;
- incident, alert, and maintenance record.

### Canonical scenarios

1. Create a private Assistant, invite a household member, and bind their own
   browser or device without exposing another person's private profile.
2. Add and name a reDevice, assign it to a room, and approve the capabilities
   it may use.
3. Run a morning or arrival routine across calendar, media, and device skills
   with an explainable pending action for consequential steps.
4. Grant a temporary guest bounded access, then expire or revoke it with an
   audit record.
5. Detect an unavailable device or failed automation, explain the degraded
   state, and recover without silently changing household policy.

### Capability composition

Home should reuse shared identity, privacy, memory, calendar, media, device
access, workflow, observability, and notification contracts. Household-specific
packs should primarily contribute routines, room and household projections,
policy presets, and natural-language behavior.

### Policy boundary and non-goals

- Owners manage access but do not automatically receive ordinary product read
  access to every person's private memory.
- Child, guest, camera, microphone, lock, payment, and destructive device
  actions require explicit policy and consent paths.
- AdaOS Home does not imply medical diagnosis, emergency-service guarantees,
  or universal compatibility with consumer hardware.
- reDevice is not exclusive to Home.

## Research and aResearcher

**Product kind:** reusable solution framework plus a future solution agent and
workbench.

**Maturity:** the Research Fabric is a proposed target architecture;
aResearcher is a strategic solution direction above it.

### Value promise

The research direction supports an inspectable, reproducible, and governed path
from a research question through protocol, execution, analysis, evidence, and
publication support. Its differentiator is not unbounded autonomy: it is
traceable human and policy authority over a replaceable set of models, trackers,
executors, and storage providers.

### Deployment reach and actors

The same research solution may run in a personal Assistant, a university lab or
course, or a corporate R&D environment. Expected actors include researcher,
principal investigator or project owner, collaborator, reviewer, operator,
executor agent, tracker provider, and model or data service.

### Domain objects

- study, research question, and hypothesis;
- protocol and analysis plan;
- dataset and split manifest;
- trial group, trial, and execution attempt;
- artifact, metric, observation, and evidence bundle;
- conclusion or claim with review state;
- manuscript, review, and publication candidate.

The normative research contracts belong to
[AdaOS Research Fabric](../architecture/research-fabric.md). aResearcher is a
conversational and visual operating surface over those contracts; it does not
own a second study database or workflow truth.

### Canonical scenarios

1. Turn a question and literature set into reviewable hypotheses, unresolved
   assumptions, and a draft protocol without starting confirmatory execution.
2. Lock a protocol, code and data identities, analysis plan, budget, and stop
   rules before a confirmatory run.
3. Submit paired trials to a local or remote executor, reconcile attempts, and
   record tracker-independent evidence.
4. Recompute the declared analysis from the evidence bundle and distinguish
   exploratory findings from confirmatory claims.
5. Draft a manuscript or review package with provenance, citations, limitations,
   and explicit human approval before publication.

### Policy boundary and non-goals

- A model may propose hypotheses, code, analysis, or prose, but it is not an
  autonomous authority for protocol lock, test unblinding, claim acceptance, or
  publication.
- Provider dashboards are not the sole evidence record.
- The direction does not promise scientific validity merely because a workflow
  completed.
- It does not make one tracker, executor, database, model family, or research
  method an AdaOS core dependency.

## AdaOS Campus

**Product kind:** institutional deployment profile plus teaching, learning, and
course solution packs.

**Maturity:** strategic direction. Shared identity, invite, profile, policy,
webspace, skill, and scenario foundations do not yet constitute a complete
Campus product.

### Value promise

AdaOS Campus provides governed assistant environments for courses, lectures,
seminars, laboratories, and collaborative projects while keeping academic
roles, consent, private learning context, shared materials, and assessment
authority explicit.

### Topology hypothesis and actors

An institution may operate multiple Assistant environments for courses,
laboratories, departments, or cohorts. Whether a course is represented by its
own Assistant, a workspace, or a domain aggregate inside a broader environment
remains a product and topology decision; it must not be frozen by marketing
terminology before a pilot proves the boundary.

Expected actors include institution operator, instructor, teaching assistant,
student, guest lecturer, project collaborator, course agent, and personal
student or instructor agent.

### Domain objects

- person profile and personal preferences;
- institution and course membership;
- instructor, teaching-assistant, student, and guest roles;
- course, cohort, syllabus, and schedule;
- learning activity: lecture, seminar, laboratory, office hours, or project;
- source, slide deck, note, recording, discussion, and question;
- assignment, submission, feedback, and assessment decision;
- individual and group project artifacts.

A lecture or seminar is a durable learning activity, not a user profile. Role
and membership are access records, not editable profile preferences. Learning
evidence and assessment decisions are also distinct from both.

### Data scopes

At minimum, Campus must distinguish:

- student-private learning context;
- instructor-private preparation and notes;
- teaching-team shared material;
- course-wide material and discussion;
- project-group material;
- institution-visible operational and retention metadata;
- assessment records with explicit authority and appeal boundaries.

### Canonical scenarios

1. Create a course environment, invite instructors and students with bounded
   roles, and expose the scope and retention policy before acceptance.
2. Prepare a lecture from cited sources, existing course artifacts, and the
   instructor's private notes without leaking the private preparation layer.
3. Support a live session with shared materials and moderated questions while
   keeping student-private context separate.
4. Run a seminar or group research project in which sources, claims,
   contributions, and generated artifacts retain provenance.
5. Produce a post-session summary, unanswered-question queue, and personal
   follow-up without treating model inference as an academic assessment.

### Capability composition

Campus should reuse identity, invite, consent, calendar, document, conversation,
research, workflow, provenance, policy, and audit capabilities. Domain packs
should add course and learning-activity schemas, instructor and student views,
LMS adapters, academic-integrity rules, and time-bounded membership presets.

### Policy boundary and non-goals

- No hidden student profiling or surveillance.
- A student must be able to understand and manage the allowed use of their
  personal learning context.
- Instructor access to course operations does not imply unrestricted access to
  student-private memory.
- The first product direction should not autonomously assign grades or make
  disciplinary decisions.
- AdaOS Campus does not claim LMS replacement, accreditation compliance, or
  institution-wide deployment before explicit integration and policy evidence.

## AdaOS Enterprise

**Product kind:** organizational deployment profile plus team and process
solution packs.

**Maturity:** long-term direction. Enterprise-compatible primitives should
guide shared architecture without distorting simpler household and Campus
deployments.

### Value promise

AdaOS Enterprise provides governed local-first assistant environments for
organizational knowledge, teams, processes, approvals, integrations, and
operational automation. It should let an organization choose where execution
and state live while preserving identity, authorization, provenance, audit, and
rollback boundaries.

### Topology hypothesis and actors

An organizational deployment may manage multiple Assistant environments for
teams, projects, departments, or security domains. Cross-environment
federation, centralized administration, and directory-driven provisioning are
target capabilities, not current implementation claims.

Expected actors include organization administrator, workspace administrator,
device administrator, employee, contractor, guest, process owner, auditor,
support operator, and domain agent.

### Domain objects

- organization, organizational unit, team, project, and workspace;
- external identity binding, group, membership, grant, and admin scope;
- policy, retention rule, approval, exception, and audit event;
- document, knowledge source, case, task, and process instance;
- integration binding, credential reference, device, and service;
- incident, operational change, evidence, and rollback record.

### Canonical scenarios

1. Provision a team from an external identity source while requiring local
   policy to grant actual Assistant or workspace access.
2. Onboard an employee or contractor with role, device, data, and expiry
   constraints, then revoke access consistently.
3. Turn a meeting or request into reviewable tasks and approvals with source
   links and accountable owners.
4. Execute a standard operating procedure across internal systems with pending
   actions for consequential steps and a durable audit trail.
5. Detect an incident, collect bounded evidence, propose remediation, obtain
   approval, and verify or roll back the change.

### Capability composition

Enterprise should reuse the same identity, policy, artifact, workflow,
observability, support, Builder, and package lifecycle used elsewhere. Domain
packs should add directory and group adapters, organization policy presets,
business-system integrations, retention controls, and administrative
projections.

### Policy boundary and non-goals

- External identity verification or provisioning does not itself authorize
  access inside an AdaOS environment.
- AdaOS should expose adapters for mature identity and provisioning standards
  rather than inventing a proprietary enterprise directory protocol.
- The direction does not currently claim general multi-tenant SaaS isolation,
  regulatory compliance, enterprise federation, or support SLAs.
- An agent may recommend a consequential action but must not approve its own
  high-risk change where separation of duties is required.

## Cross-Cutting reDevice Direction

reDevice is an endpoint family, not a fifth solution domain. Its reusable
contracts cover endpoint identity, pairing, assignment, capability discovery,
commands, events, media, health, and revocation. Domain profiles may add presets
and projections, but they must not fork endpoint identity or routing.

Examples include a room assistant at home, an instructor console or shared
display in a classroom, an instrument or observation endpoint in a laboratory,
and a meeting-room or operations endpoint in an office.

## Portfolio Packaging Rule

Capabilities should be classified before they are assigned to a direction:

| Layer | Examples | Packaging rule |
| --- | --- | --- |
| Platform capability | Identity, policy, workflow, artifacts, events, lifecycle | Keep in shared core or shared framework contracts only when multiple domains prove the abstraction. |
| Horizontal skill | Calendar, documents, communication, device control, search | Reuse across directions with scoped bindings and policy. |
| Domain skill | Research protocol manager, course planner, LMS adapter, directory adapter | Package for the domain without creating a new runtime. |
| Scenario pack | Morning routine, lecture preparation, paired trial, employee onboarding | Compose shared and domain skills around a measurable outcome. |
| Deployment preset | Household roles, course membership windows, organization admin scopes | Version and validate as policy/configuration, not hard-coded product branches. |

Product pages may market outcomes separately. Engineering should preserve one
capability vocabulary, package lifecycle, evidence model, and policy boundary.

## Planning Boundary

This page owns portfolio framing, not implementation priority. Cross-domain
delivery priority belongs in an explicitly approved product roadmap or durable
Issues. Technical domain roadmaps continue to own contracts, implementation
sequence, and acceptance evidence within their scopes.

See [Roadmap Inventory and Authority Map](../architecture/roadmap-inventory.md)
for the planning hierarchy and
[Documentation Language and Translation Policy](../documentation-language-policy.md)
for translation authority.
