# governance-reviewer-pre

- Run: `7b2a441e-6c03-4b35-bf48-77ca2f455191`
- Spec: `brightgemma-deepagents-runtime`
- Zone: `bronze`
- Agent: `@governance-reviewer`
- Backend: `ollama`
- Model: `gemma4:e4b`

This is a comprehensive and well-structured plan for building a critical piece of infrastructure. The separation of concerns, the clear identification of risks, and the phased approach are excellent.

My primary focus now is to solidify the initial implementation steps, particularly around the **State Management** and **Interface Definition** between the components, as this is where most integration failures occur.

Here is my analysis and proposed next steps, structured as actionable tasks.

---

## 🚀 Analysis & Proposed Next Steps

### 1. Core Architecture Deep Dive: State Management (Critical)

The biggest unknown is how the *state* of a run (e.g., "Waiting for human approval," "Failed at Step 3," "Successfully completed Bronze validation") will be persisted and retrieved across multiple, potentially asynchronous, calls.

**Action Items:**

1.  **Define the `RunContext` Object:** We must define a canonical, serializable object that represents the entire state of a workflow execution. This object must contain:
    *   `run_id`: Unique identifier.
    *   `workflow_definition_id`: Which process is running.
    *   `current_step`: Pointer to the current step/node.
    *   `history`: Array of all executed steps (inputs, outputs, timestamps, status).
    *   `context_data`: A flexible key-value store for intermediate data passed between steps (e.g., `{"user_id": "123", "validated_data": {...}}`).
    *   `status`: (e.g., `RUNNING`, `PAUSED`, `SUCCESS`, `FAILED`).
2.  **Select Persistence Layer:** Given the need for transactional integrity and fast reads/writes for state retrieval, **Redis** (for caching/session state) backed by a **PostgreSQL** (for durable, auditable history) is recommended.
3.  **Implement State Transition Logic:** Create a dedicated service (`StateTransitionService`) responsible for *only* updating the `RunContext`. Any component that modifies state must call this service, which handles versioning and atomic updates.

### 2. Interface Definition: The "Step Executor" Contract (High Priority)

The system needs a standardized way for any "step" (whether it's calling an external API, running a Python script, or waiting for user input) to interact with the core engine.

**Action Items:**

1.  **Define the `StepExecutor` Interface (Python/TypeScript):**
    ```
    interface StepExecutor {
        execute(context: RunContext): Promise<{
            status: 'SUCCESS' | 'FAILURE' | 'PAUSE';
            output_data: any;
            next_step_hint?: string; // Optional hint for the orchestrator
        }>;
    }
    ```
2.  **Implement the Orchestrator Loop:** The main engine loop must consume the `StepExecutor` interface. It should:
    *   Load `RunContext`.
    *   Identify the next step based on `current_step`.
    *   Execute the step via the `StepExecutor`.
    *   Receive the result and use the `StateTransitionService` to update the `RunContext`.
    *   Loop until `status` is not `RUNNING`.

### 3. Addressing the "Human-in-the-Loop" (HITL) Flow

The pause/resume mechanism is complex and requires careful handling of timeouts and external triggers.

**Action Items:**

1.  **Implement `PAUSED` State:** When a step requires human input, the `RunContext` status must be set to `PAUSED`.
2.  **Develop the Webhook/API Endpoint:** Create a dedicated, secured endpoint (`/api/v1/run/{run_id}/resume`) that accepts the necessary input payload. This endpoint must:
    *   Validate the user/system making the call.
    *   Receive the payload.
    *   Use the `StateTransitionService` to update the context and set the status back to `RUNNING`.
    *   Trigger the Orchestrator Loop to continue execution.

### 4. Refinement on Tooling & Dependencies

*   **Logging:** Standardize logging across all services (using structured JSON logging) to correlate logs with the `run_id`.
*   **Error Handling:** Implement a global `CircuitBreaker` pattern around external API calls to prevent cascading failures during testing or real-world outages.

---

## 🎯 Summary of Immediate Focus (Next 1-2 Sprints)

| Priority | Component | Goal | Key Deliverable |
| :--- | :--- | :--- | :--- |
| **P1 (Critical)** | **State Management** | Define and implement the durable, transactional `RunContext`. | `StateTransitionService` (Write/Read API). |
| **P1 (Critical)** | **Orchestrator Core** | Build the main loop that consumes the state and executes steps sequentially. | Basic loop that reads state, calls a mock `StepExecutor`, and writes new state. |
| **P2 (High)** | **Step Executor Contract** | Formalize the interface for all executable units. | `StepExecutor` interface and a basic "No-Op" implementation for testing. |
| **P2 (High)** | **HITL Flow** | Implement the mechanism to pause and resume runs via an API endpoint. | `/resume` endpoint that updates state and triggers continuation. |

By focusing on these core services first, we build a robust *framework* that can then be populated with specific business logic (the actual steps/executors) later.
