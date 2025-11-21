# Workflow Diagrams and Visual Aids

This document provides visual representations of the spec-driven development process, including complete workflow diagrams, decision trees, and phase transition flows.

## Complete Process Flow

The following diagram shows the complete spec-driven development workflow from initial idea to implementation:

```mermaid
stateDiagram-v2
    [*] --> Idea : Feature Request
    
    Idea --> Requirements : Begin Spec Process
    
    state Requirements {
        [*] --> Draft_Req : Create Initial Requirements
        Draft_Req --> Review_Req : Complete Draft
        Review_Req --> Revise_Req : Feedback/Changes
        Revise_Req --> Review_Req : Updated Draft
        Review_Req --> [*] : Explicit Approval
    }
    
    Requirements --> Design : Requirements Approved
    
    state Design {
        [*] --> Research : Identify Research Needs
        Research --> Draft_Design : Create Design Document
        Draft_Design --> Review_Design : Complete Draft
        Review_Design --> Revise_Design : Feedback/Changes
        Revise_Design --> Review_Design : Updated Draft
        Review_Design --> [*] : Explicit Approval
    }
    
    Design --> Tasks : Design Approved
    
    state Tasks {
        [*] --> Draft_Tasks : Create Task Breakdown
        Draft_Tasks --> Review_Tasks : Complete Draft
        Review_Tasks --> Revise_Tasks : Feedback/Changes
        Revise_Tasks --> Review_Tasks : Updated Draft
        Review_Tasks --> [*] : Explicit Approval
    }
    
    Tasks --> Implementation : Tasks Approved
    
    state Implementation {
        [*] --> Execute_Task : Select Next Task
        Execute_Task --> Test_Task : Complete Task
        Test_Task --> More_Tasks : Task Complete
        More_Tasks --> Execute_Task : Yes
        More_Tasks --> [*] : No
    }
    
    Implementation --> [*] : Feature Complete
    
    note right of Requirements : EARS format\nUser stories\nAcceptance criteria
    note right of Design : Architecture\nComponents\nData models
    note right of Tasks : Coding tasks\nTest-driven\nIncremental
```

## Phase Transition Decision Tree

This decision tree helps determine when to move between phases and when to iterate:

```mermaid
flowchart TD
    A[Phase Complete] --> B{User Review}
    B -->|Explicit Approval| C[Move to Next Phase]
    B -->|Feedback/Changes| D[Revise Current Phase]
    B -->|Major Issues| E{Scope of Changes}
    
    E -->|Minor Adjustments| D
    E -->|Fundamental Changes| F{Which Phase to Revisit?}
    
    F -->|Requirements Issues| G[Return to Requirements]
    F -->|Design Issues| H[Return to Design]
    F -->|Task Issues| I[Revise Tasks]
    
    D --> J[Update Document]
    G --> K[Update Requirements]
    H --> L[Update Design]
    I --> M[Update Tasks]
    
    J --> A
    K --> A
    L --> A
    M --> A
    
    C --> N{Final Phase?}
    N -->|No| O[Begin Next Phase]
    N -->|Yes| P[Begin Implementation]
    
    O --> A
    P --> Q[Execute Tasks]
```

## Requirements Phase Flow

Detailed workflow for the requirements gathering phase:

```mermaid
flowchart TD
    A[Feature Idea] --> B[Analyze Initial Request]
    B --> C[Create Initial Requirements Draft]
    C --> D[Format with EARS Syntax]
    D --> E[Add User Stories]
    E --> F[Define Acceptance Criteria]
    F --> G[Consider Edge Cases]
    G --> H[Present to User]
    
    H --> I{User Feedback}
    I -->|Approved| J[Requirements Complete]
    I -->|Changes Needed| K[Identify Change Areas]
    I -->|Unclear Requirements| L[Ask Clarifying Questions]
    
    K --> M[Update Requirements]
    L --> N[Gather Additional Info]
    
    M --> H
    N --> M
    
    J --> O[Move to Design Phase]
    
    style A fill:#e1f5fe
    style J fill:#c8e6c9
    style O fill:#fff3e0
```

## Design Phase Flow

Detailed workflow for the design phase:

```mermaid
flowchart TD
    A[Requirements Approved] --> B[Identify Research Needs]
    B --> C[Conduct Research]
    C --> D[Analyze Findings]
    D --> E[Create Architecture Overview]
    E --> F[Define Components]
    F --> G[Design Data Models]
    G --> H[Plan Error Handling]
    H --> I[Define Testing Strategy]
    I --> J[Present Design to User]
    
    J --> K{User Feedback}
    K -->|Approved| L[Design Complete]
    K -->|Changes Needed| M[Update Design]
    K -->|Requirements Gap| N[Return to Requirements]
    
    M --> J
    N --> O[Update Requirements]
    O --> P[Update Design]
    P --> J
    
    L --> Q[Move to Tasks Phase]
    
    style A fill:#fff3e0
    style L fill:#c8e6c9
    style Q fill:#f3e5f5
```

## Tasks Phase Flow

Detailed workflow for breaking down design into implementation tasks:

```mermaid
flowchart TD
    A[Design Approved] --> B[Analyze Design Components]
    B --> C[Identify Implementation Order]
    C --> D[Create Task Hierarchy]
    D --> E[Define Task Dependencies]
    E --> F[Add Requirement References]
    F --> G[Ensure Test-Driven Approach]
    G --> H[Validate Task Completeness]
    H --> I[Present Tasks to User]
    
    I --> J{User Feedback}
    J -->|Approved| K[Tasks Complete]
    J -->|Changes Needed| L[Update Tasks]
    J -->|Design Issues| M[Return to Design]
    J -->|Requirements Issues| N[Return to Requirements]
    
    L --> I
    M --> O[Update Design]
    N --> P[Update Requirements]
    
    O --> Q[Update Tasks]
    P --> R[Update Design & Tasks]
    
    Q --> I
    R --> I
    
    K --> S[Begin Implementation]
    
    style A fill:#f3e5f5
    style K fill:#c8e6c9
    style S fill:#ffecb3
```

## Implementation Execution Flow

Workflow for executing individual tasks from the implementation plan:

```mermaid
flowchart TD
    A[Tasks Approved] --> B[Select Next Task]
    B --> C{Has Sub-tasks?}
    C -->|Yes| D[Execute Sub-tasks First]
    C -->|No| E[Read Requirements & Design]
    
    D --> F[Complete All Sub-tasks]
    F --> G[Mark Parent Task Complete]
    
    E --> H[Implement Task]
    H --> I[Write Tests]
    I --> J[Verify Against Requirements]
    J --> K[Mark Task Complete]
    
    G --> L{More Tasks?}
    K --> L
    
    L -->|Yes| B
    L -->|No| M[Feature Complete]
    
    style A fill:#ffecb3
    style M fill:#c8e6c9
```

## Feedback Loop Patterns

Common patterns for handling feedback and iterations:

```mermaid
flowchart LR
    subgraph "Positive Feedback Loop"
        A1[Complete Phase] --> B1[User Review]
        B1 --> C1[Approval]
        C1 --> D1[Next Phase]
    end
    
    subgraph "Revision Loop"
        A2[Complete Phase] --> B2[User Review]
        B2 --> C2[Feedback]
        C2 --> D2[Revise]
        D2 --> A2
    end
    
    subgraph "Backtrack Loop"
        A3[Current Phase] --> B3[Major Issue]
        B3 --> C3[Previous Phase]
        C3 --> D3[Fix Issue]
        D3 --> E3[Update Current]
        E3 --> A3
    end
    
    style C1 fill:#c8e6c9
    style C2 fill:#fff3e0
    style B3 fill:#ffcdd2
```

## Entry Points and Context

Different ways users can enter the spec workflow:

```mermaid
flowchart TD
    A[User Intent] --> B{Entry Point}
    
    B -->|New Feature| C[Create New Spec]
    B -->|Update Existing| D[Modify Spec]
    B -->|Execute Tasks| E[Implementation Mode]
    
    C --> F[Requirements Phase]
    D --> G{Which Phase?}
    E --> H[Task Execution]
    
    G -->|Requirements| I[Update Requirements]
    G -->|Design| J[Update Design]
    G -->|Tasks| K[Update Tasks]
    
    F --> L[Complete Workflow]
    I --> L
    J --> L
    K --> L
    H --> M[Single Task Focus]
    
    style C fill:#e1f5fe
    style D fill:#fff3e0
    style E fill:#ffecb3
```

## Quality Gates and Validation Points

Key validation checkpoints throughout the process:

```mermaid
flowchart TD
    subgraph "Requirements Quality Gates"
        A1[EARS Format Check]
        A2[User Story Validation]
        A3[Acceptance Criteria Complete]
        A4[Edge Cases Considered]
    end
    
    subgraph "Design Quality Gates"
        B1[Architecture Clarity]
        B2[Component Definition]
        B3[Data Model Completeness]
        B4[Error Handling Plan]
    end
    
    subgraph "Tasks Quality Gates"
        C1[Task Granularity]
        C2[Dependency Order]
        C3[Requirement Traceability]
        C4[Test-Driven Structure]
    end
    
    subgraph "Implementation Quality Gates"
        D1[Code Quality]
        D2[Test Coverage]
        D3[Requirement Compliance]
        D4[Integration Success]
    end
    
    A1 --> A2 --> A3 --> A4
    B1 --> B2 --> B3 --> B4
    C1 --> C2 --> C3 --> C4
    D1 --> D2 --> D3 --> D4
    
    A4 --> B1
    B4 --> C1
    C4 --> D1
```

## Common Workflow Scenarios

### Scenario 1: Smooth Linear Progression
```mermaid
sequenceDiagram
    participant U as User
    participant A as AI Agent
    participant R as Requirements
    participant D as Design
    participant T as Tasks
    
    U->>A: Feature Request
    A->>R: Create Requirements
    A->>U: Review Requirements
    U->>A: Approve
    A->>D: Create Design
    A->>U: Review Design
    U->>A: Approve
    A->>T: Create Tasks
    A->>U: Review Tasks
    U->>A: Approve
    Note over U,T: Ready for Implementation
```

### Scenario 2: Iterative Refinement
```mermaid
sequenceDiagram
    participant U as User
    participant A as AI Agent
    participant R as Requirements
    participant D as Design
    
    U->>A: Feature Request
    A->>R: Create Requirements v1
    A->>U: Review Requirements
    U->>A: Changes Needed
    A->>R: Update Requirements v2
    A->>U: Review Requirements
    U->>A: Approve
    A->>D: Create Design v1
    A->>U: Review Design
    U->>A: Requirements Gap Found
    A->>R: Update Requirements v3
    A->>D: Update Design v2
    A->>U: Review Design
    U->>A: Approve
```

### Scenario 3: Implementation Feedback
```mermaid
sequenceDiagram
    participant U as User
    participant A as AI Agent
    participant T as Tasks
    participant I as Implementation
    
    Note over U,I: Spec Complete, Begin Implementation
    U->>A: Execute Task 1
    A->>I: Implement Task 1
    A->>U: Task 1 Complete
    U->>A: Execute Task 2
    A->>I: Implement Task 2
    A->>U: Issue Found
    U->>A: Fix Approach
    A->>T: Update Task 2
    A->>I: Re-implement Task 2
    A->>U: Task 2 Complete
```

These visual aids provide comprehensive guidance for understanding and navigating the spec-driven development process, supporting both newcomers learning the methodology and experienced practitioners looking for quick reference materials.