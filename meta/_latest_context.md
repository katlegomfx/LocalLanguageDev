# Documentation for ..\meta_prompt

## architechture_prompt.md

```md
### Mission

Build a production-grade Python project called **Flexi Harness**.

Flexi Harness is a recursively self-improving agent operating system.

The system must be capable of:

1. Accepting goals.
2. Planning execution.
3. Using tools.
4. Executing tasks.
5. Observing outcomes.
6. Storing knowledge.
7. Evaluating performance.
8. Generating improvement proposals.
9. Testing proposed improvements.
10. Deploying only verified improvements.
11. Repeating indefinitely.

The architecture should be designed so that future versions can continuously improve their own workflows, prompts, memory systems, tool selection strategies, and runtime behavior.

---

### High-Level Architecture

Implement the following layers:

Goal Layer
↓
Planning Layer
↓
Execution Layer
↓
Tool Ecosystem
↓
Observability Layer
↓
Blackboard Memory
↓
Meta-Critic Layer
↓
Improvement Laboratory
↓
Benchmark Arena
↓
Governor
↓
Promotion Pipeline

---

### Core Requirements

#### 1. Agent Kernel

Create a central runtime that manages:

* goals
* tasks
* agents
* events
* state transitions

Required classes:

AgentKernel
GoalManager
TaskManager
EventBus
AgentRuntime

---

#### 2. Blackboard Memory

Implement a shared memory system.

Memory categories:

facts
observations
failures
hypotheses
open_questions
decisions
next_actions
insights

All agents must read and write through the blackboard.

Nothing important should exist only in model context.

Storage:

SQLite initially.

Interfaces:

add_fact()
add_observation()
add_failure()
search()
summarize()

---

#### 3. Tool Framework

Create a plugin system.

Tool categories:

filesystem
coding
research
memory
automation
system
diagnostics
web

Every tool must expose:

name
description
capabilities
cost_score
reliability_score

Base interface:

Tool
ToolRegistry
ToolExecutor
CapabilityRouter

Tool selection should be capability-driven.

---

#### 4. Observability

Record every action.

Track:

task_id
agent
tool
runtime
token_usage
success
error
cost

Store all telemetry in SQLite.

Create:

TelemetryStore
MetricsEngine

---

#### 5. Meta-Critic

After every task:

Evaluate:

* plan quality
* tool choice
* execution quality
* memory usage
* failure causes

Output:

RootCauseAnalysis

Store all findings.

---

#### 6. Capability Graph

Implement a graph describing capabilities.

Example:

Research
├─ Search
├─ Verification
└─ Synthesis

Coding
├─ Generation
├─ Refactoring
└─ Testing

Track:

score
confidence
usage_count
improvement_history

The harness should identify weak capabilities automatically.

---

#### 7. Improvement Laboratory

Create a dedicated subsystem that generates improvements.

Input:

telemetry
failures
benchmarks
critic findings

Output:

ImprovementProposal

Example:

{
"title": "Improve Tool Routing",
"hypothesis": "...",
"expected_gain": 0.15
}

---

#### 8. Experiment Framework

Every improvement becomes an experiment.

Implement:

Experiment
ExperimentRunner
ExperimentStore

Experiment flow:

Generate
→ Sandbox
→ Test
→ Benchmark
→ Compare
→ Accept or Reject

Store historical results.

---

#### 9. Sandbox Environment

Never modify production directly.

Structure:

production/
sandbox/

The system must:

clone files
apply modifications
run tests
measure results

before promotion.

---

#### 10. Benchmark Arena

Create benchmark suites for:

planning
coding
research
memory retrieval
tool routing
recovery

Support:

baseline versions
candidate versions

Output:

BenchmarkReport

---

#### 11. Governor

The governor decides promotion.

Inputs:

performance
cost
complexity
reliability
safety

Rule:

Reject improvements that increase complexity while decreasing overall utility.

Implement:

GovernorAgent

---

#### 12. Runtime Self-Repair

Create:

RecoveryAgent

When exceptions occur:

1. Capture stack trace.
2. Collect relevant files.
3. Generate patch.
4. Apply in sandbox.
5. Run tests.
6. Promote if verified.

---

#### 13. Dynamic Workflow Generator

Workflows must not be hardcoded.

Generate workflows based on task type.

Example:

Research Goal:

Planner
→ Researcher
→ Verifier
→ Critic

Coding Goal:

Planner
→ Coder
→ Tester
→ Recovery
→ Verifier

Represent workflows as directed graphs.

---

#### 14. Multi-Agent System

Implement:

ExecutiveAgent
PlannerAgent
ResearchAgent
CodingAgent
CriticAgent
VerifierAgent
RecoveryAgent
BenchmarkAgent
ImprovementAgent
GovernorAgent

All communicate through the EventBus and Blackboard.

---

#### 15. Recursive Improvement Loop

Implement:

while True:

1. Execute goals.
2. Collect telemetry.
3. Analyze performance.
4. Generate improvement proposals.
5. Run experiments.
6. Benchmark candidates.
7. Promote winners.
8. Update memory.
9. Continue.

The improvement system itself should be observable and benchmarked.

---

#### 16. Architecture Evolution

Create:

ArchitectureEvolutionEngine

Allowed to improve:

prompts
workflows
tool routing
memory retrieval
benchmark suites
agent composition

All modifications require benchmark validation.

---

#### 17. Agent Factory

The harness may create new specialized agents.

Examples:

CodeReviewAgent
SecurityAgent
DocumentationAgent
BenchmarkDesignerAgent

New agents must:

register capabilities
provide benchmarks
pass validation

before activation.

---

#### 18. Storage

Use:

SQLite
JSON artifacts
Vector memory abstraction layer

Prepare interfaces for future:

PostgreSQL
Neo4j
Qdrant

migration.

---

#### 19. UI

Build a Rich TUI dashboard showing:

Goals
Tasks
Agents
Experiments
Telemetry
Failures
Benchmarks
Improvement Queue
Capability Scores

Live updating.

---

#### 20. Deliverables

Generate:

* Complete project structure.
* Full implementation.
* Type hints.
* Dataclasses/Pydantic models.
* Unit tests.
* Integration tests.
* Benchmark framework.
* Example experiments.
* Example agents.
* Example workflows.
* Documentation.

Work incrementally.

Before generating code:

1. Produce architecture.
2. Produce file tree.
3. Produce implementation plan.
4. Then generate code module-by-module.

Never use placeholders or TODO comments.

Every generated file must be production-ready.

```


```

## directAsk.py

```py

from datetime import datetime
import os
from ollama import chat

def ask_model(model, messages):
  stream = chat(
    model=model,
    messages=messages,
    stream=True
  )

  in_thinking = False
  content = ""
  thinking = ""

  for chunk in stream:
    message = chunk.get("message") if isinstance(
      chunk, dict) else getattr(chunk, "message", None)
    if message is None:
      continue

    chunk_thinking = message.get("thinking") if isinstance(
      message, dict) else getattr(message, "thinking", None)
    chunk_content = message.get("content") if isinstance(
      message, dict) else getattr(message, "content", None)

    if chunk_thinking:
      if not in_thinking:
        in_thinking = True
        print("Thinking:\n", end="", flush=True)
      print(chunk_thinking, end="", flush=True)
      thinking += chunk_thinking
      continue

    if chunk_content:
      if in_thinking:
        in_thinking = False
        print("\n\nAnswer:\n", end="", flush=True)
      print(chunk_content, end="", flush=True)
      content += chunk_content

  if thinking or content:
    print("\n", flush=True)
  return content


def build_messages(system_prompt, prompt, add_messages=[]):
  messages = []
  # History
  if add_messages != []:
    for mess in add_messages:
      if mess['role'] == 'user':
        messages.append({
            'role': 'user',
            'content': mess['content']
        })
      elif mess['role'] == 'assistant':
        messages.append({
            'role': 'assistant',
            'content': mess['content']
        })
  # Current Context
  messages.append({
      'role': 'system',
      'content': system_prompt,
  })

  messages.append({
    'role': 'user',
    'content': prompt,
  })
  return messages


def generate_system_prompt(model, goal):
  messages = [
    {
      'user': 'system',
      'content': 'Only output JSON for a system prompt that will guide a AI to achieve'
    },
    {
        'user': 'user',
        'content': f'Help me create a system prompt for an AI system.\nThe goal is:\n{goal}'
    }
  ]
  system_prompt = ask_model(model, messages)
  return system_prompt


def generate_user_prompt(model, goal):
  messages = [
      {
          'user': 'system',
          'content': 'Only output JSON for a user prompt that will guide a AI to achieve'
      },
      {
          'user': 'user',
          'content': f'Help me create a user prompt for an AI system.\nThe goal is:\n{goal}'
      }
  ]
  user_prompt = ask_model(model, messages)
  return user_prompt

while True:
  try:
    goal = input('> ')

    model = 'nemotron3:33b'
    messages = build_messages(generate_system_prompt(
        model, goal), generate_user_prompt(model, goal))
    content = ask_model(model, messages)
    messages.append({
        'role': 'assistant',
        'content': content
    })

  except KeyboardInterrupt:
      print("\nExiting via Ctrl+C...")
      break  # Stops the loop if they press Ctrl+C

print("Program has ended safely.")

```

## guide.md

```md
A **recursively self-improving Agentic Harness** is essentially an agent that can:

1. Execute tasks.
2. Observe its own performance.
3. Generate hypotheses for improvement.
4. Modify its own prompts, tools, workflows, code, and architecture.
5. Test those modifications.
6. Keep only improvements that outperform previous versions.
7. Repeat indefinitely.

The key distinction is that the agent is not just learning from data—it is improving the system that performs the learning.

---

### Architecture

```text
                    ┌─────────────────┐
                    │    USER GOAL    │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │    PLANNER      │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  EXECUTION LOOP │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ TOOL ECOSYSTEM  │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ OBSERVABILITY   │
                    │  + TELEMETRY    │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ SELF-ANALYSIS   │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ IMPROVEMENT LAB │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ A/B EVALUATION  │
                    └────────┬────────┘
                             │
                     Better? │
                      Yes    ▼
                    ┌─────────────────┐
                    │ DEPLOY PATCH    │
                    └─────────────────┘
```

---

### Core Components

#### 1. Blackboard Memory

This becomes the agent's working brain.

```python
class Blackboard:
    facts = []
    observations = []
    failures = []
    hypotheses = []
    open_questions = []
    next_actions = []
```

Every agent writes to it.

Every agent reads from it.

Nothing important exists only in context.

---

#### 2. Observability Layer

Every action is recorded.

```python
{
    "task": "...",
    "tool": "filesystem",
    "success": False,
    "runtime": 2.3,
    "error": "Permission denied",
    "tokens": 1340
}
```

Over time:

```text
Filesystem failures: 41%
Python tool failures: 12%
Planning mistakes: 24%
```

Patterns emerge.

---

#### 3. Self-Critic

After every task:

```python
What went wrong?
What succeeded?
Could another tool have been better?
Was the plan optimal?
```

Output:

```json
{
  "root_cause": "Wrong tool selected",
  "fix": "Improve tool routing"
}
```

---

#### 4. Improvement Generator

Uses findings to generate upgrades.

Example:

```text
Problem:
Tool selection accuracy low.

Hypothesis:
Add tool categories and routing layer.

Expected Gain:
+15%
```

Stored as:

```json
{
  "proposal_id": "IMP-127",
  "title": "Tool Categorization",
  "reason": "...",
  "expected_benefit": 15
}
```

---

#### 5. Sandbox

Critical for recursive improvement.

Never modify production directly.

```text
production/
sandbox/
```

Agent works inside:

```text
sandbox/
```

Creates:

```python
planner_v2.py
```

Runs tests.

Only promoted if successful.

---

### Improvement Targets

Most people think the agent only improves code.

Actually it should improve:

##### Prompts

```python
planner_prompt.txt
critic_prompt.txt
```

##### Workflows

```text
planner -> researcher -> critic
```

might become

```text
planner -> verifier -> researcher -> critic
```

##### Tool Selection

```text
filesystem
coding
research
memory
web
automation
diagnostics
```

##### Memory Structure

```text
json
→ sqlite
→ vector store
→ graph memory
```

##### Models

```python
llama3
→ qwen
→ gpt-oss
→ ensemble
```

---

### Automatic Experiment Pipeline

Every improvement becomes an experiment.

```python
class Experiment:
    hypothesis: str
    change: str
    metrics_before: dict
    metrics_after: dict
```

Example:

```text
Experiment #248

Change:
Added Error Recovery Agent

Success Rate:
71% → 84%

Result:
KEEP
```

or

```text
63% → 59%

Result:
REJECT
```

---

### Runtime Error Recovery

A powerful self-improvement mechanism.

When code fails:

```python
try:
    run_task()
except Exception as e:
    recovery_agent(e)
```

Recovery agent receives:

```text
Error
Stack trace
Relevant files
Recent changes
```

and generates:

```python
patch.diff
```

Then:

```text
Test
Validate
Deploy
```

---

### Meta-Agent Hierarchy

A mature system looks like:

```text
CEO Agent
│
├── Planner Agent
├── Research Agent
├── Coding Agent
├── Critic Agent
├── Evaluator Agent
├── Recovery Agent
├── Benchmark Agent
└── Improvement Agent
```

The Improvement Agent studies the others.

---

### Recursive Loop

The real loop:

```python
while True:

    perform_tasks()

    analyze_performance()

    generate_improvements()

    test_improvements()

    deploy_best()

    update_knowledge()
```

This is the recursive self-improvement cycle.

---

### What Would Make Flexi Lab Truly Recursive?

For your Flexi Lab architecture, the highest-impact additions would be:

##### Tier 1

* Blackboard memory
* Experiment database (SQLite)
* Tool categories
* Runtime error recovery
* Automatic benchmarking

##### Tier 2

* Sandbox code modification
* Prompt optimization
* Workflow optimization
* Autonomous experiment generation

##### Tier 3

* Agent-generated agents
* Dynamic workflow graphs
* Multi-model evaluation
* Self-generated benchmarks

---

### End-State Vision

The end state is not a chatbot.

It is a continuously evolving system:

```text
Goal
  ↓
Plan
  ↓
Act
  ↓
Observe
  ↓
Learn
  ↓
Improve Itself
  ↓
Test Improvement
  ↓
Deploy Improvement
  ↓
Repeat Forever
```

At that point, the harness becomes closer to an autonomous software engineering and research platform than a traditional AI agent. The bottleneck is no longer the model itself; it is the quality of the evaluation, benchmarking, and safety mechanisms that determine whether a self-generated change is actually an improvement.

```


```

