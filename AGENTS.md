Aura Coding Standards
Philosophy: "Structure reveals intent. Design systems like electrical panels - every component isolated, every connection explicit, every responsibility singular and obvious."
These standards capture how we architect and engineer software at Aura. Before touching the repository, study these patterns and the system design principles behind them.
The Architect's Mindset
We think in systems, not scripts. Code organization mirrors how you'd design an electrical panel:

Circuit isolation = Separation of concerns
Standardized connections = Interface contracts
Explicit wiring = Dependency injection
Panel zones = Module boundaries
Load calculations = Dependency management
Code compliance = Design patterns

Every module has one clear purpose. Every dependency flows explicitly through constructors. Every boundary is documented.
Core Principles
1. Separation of Concerns (Circuit Isolation)
Each module handles ONE responsibility. Like dedicated circuits, components don't share concerns.
Layers are sacred:

Services layer (src/aura/services/): Orchestration and coordination
Executor layer (src/aura/executor/): Action execution and side effects
Models layer (src/aura/models/): Data structures and domain entities
Infrastructure (file I/O, external APIs): Isolated in specific modules

Never mix:

Data access in UI logic
Business rules in infrastructure code
Orchestration in domain models

Study MemoryManager (domain logic) vs WorkspaceService (orchestration) to see clean separation.
2. Interface-Driven Design (Standardized Connections)
Define contracts before implementations. Electrical work requires spec sheets before wiring - same here.

Use Protocol classes or abstract base classes for swappable components
Dependencies declare what they need via interface, not concrete type
Consumers depend on abstractions, not implementations

Example pattern:
   from typing import Protocol
   
   class MessageBus(Protocol):
       """Contract for event distribution"""
       def dispatch(self, event: Event) -> None: ...
   
   class EmailService:
       def __init__(self, bus: MessageBus):  # Depends on interface
           self.bus = bus
   
See LLMService - accepts any provider via interface. See TerminalAgentService - takes EventBus as injected dependency.
3. Dependency Injection (Explicit Wiring)
All dependencies flow through constructors. No hidden wiring, no service locators, no globals.

If a class needs something, it's visible in __init__
Makes testing trivial - swap real dependencies for test doubles
Traces flow like reading a wiring diagram

Bad:
   class UserService:
    def get_user(self):
        db = get_global_database()  # Hidden dependency!
Good:
   class UserService:
    def __init__(self, database: Database):  # Explicit, testable
        self.database = database
```

Study constructor signatures in `src/aura/services/` - everything is injected.

### 4. Module Organization (Panel Layout)

**Related functionality groups into dedicated directories.** Clear zones, like panel sections.
```
src/aura/
├── services/      # Orchestration (main bus)
├── executor/      # Actions & side effects (load centers)
├── models/        # Data contracts (specs)
├── prompts/       # Templates (pre-fab components)
└── config.py      # System configuration (panel schedule)

Each module has singular purpose:

memory_manager.py - Memory operations only
terminal_agent_service.py - Terminal spawning only
file_operations.py - File I/O only

No dumping ground files. No utils.py with 47 unrelated functions.
5. Design Before Code (Spec Before Wire)
Complex changes need design documentation first. Don't wire before you have a diagram.
Before significant work:

What's this component's single responsibility?
What interfaces does it expose?
What dependencies does it need?
How does it fit existing architecture?

Add module-level docstrings explaining system context. Comment design tradeoffs, not obvious operations.
Naming & Structure

Imports: Use from __future__ import annotations when forward references simplify hints (terminal_agent_service.py, file_operations.py)
Classes: PascalCase (MemoryManager, TerminalAgentService)
Functions/methods: snake_case (get_memory, spawn_agent)
Data models: Inherit from pydantic.BaseModel, use Field for validation (agent_task.py, context_models.py)
Public APIs: Small and explicit - expose only what's necessary
Private details: Prefix with _ when internal implementation

Error Handling
Validate early, fail explicitly, log with context.

Input validation at boundaries - raise ValueError or RuntimeError with descriptive messages
Wrap external I/O (filesystem, network) in try/except
Log failures with identifiers (task IDs, file paths) using %s formatting and exc_info=True
Re-raise domain-specific errors after logging (FileOperations.execute_read_file:33-48)
Use custom exceptions from src/aura/models/exceptions.py when appropriate

Pattern:
   try:
    result = external_operation()
except ExternalError as exc:
    logger.error("Operation failed for task %s: %s", task_id, exc, exc_info=True)
    raise DomainSpecificError(f"Failed to process task {task_id}") from exc
Study error handling in file_operations.py and terminal_agent_service.py.
Logging
Log state changes, not noise. Include identifiers for traceability.

Module-level logger: logger = logging.getLogger(__name__)
logger.info for state changes and milestones
logger.debug for diagnostic context (verbose mode)
logger.error(..., exc_info=True) for failures with stack traces
Include identifiers (task IDs, user IDs, file paths) so operations are traceable

Examples:
logger.info("Terminal session started for task %s (pid=%s)", task_id, process_id)
logger.debug("Loading context from %d files", len(files))
logger.error("Failed to spawn agent for task %s", task_id, exc_info=True)

See workspace_service.py and terminal_agent_service.py for consistent patterns.
Type Hints & Documentation
Every signature annotated. Every public API documented.

Type hints on all functions, methods, and class attributes
Prefer concrete container types: List[str], Dict[str, Any], Optional[Path]
Use Protocol for interface definitions
Concise docstrings on modules, classes, and public methods
Google-style parameter sections when signatures need explanation
Comments explain WHY (design decisions), not WHAT (code is self-documenting)

Good docstring:
def spawn_agent(self, specification: AgentSpecification) -> TerminalSession:
    """
    Spawn a terminal agent with the given specification.
    
    Args:
        specification: Task details, files to watch, and completion criteria
        
    Returns:
        Active terminal session tracking the spawned process
        
    Raises:
        RuntimeError: If terminal spawn fails or workspace is invalid
    """

Study MemoryManager.get_memory and TerminalAgentService.spawn_agent.
Tests
Test through interfaces, not implementation details.

Add pytest cases in tests/ asserting observable behavior
Test public APIs - what consumers depend on
Mock external dependencies (filesystem, network), not internal helpers
Favor deterministic tests - no flaky time-dependent behavior
See tests/test_terminal_agent_service.py for patterns

Test what matters:

Does the interface contract hold?
Are errors handled correctly?
Do state changes persist as expected?

Don't test private implementation details that might change during refactoring.
Implementation Workflow
Understand the system before changing it.
Phase 1: Survey the Architecture

Understand system boundaries - Where does this component live? What's its responsibility? What are its interfaces?
Run LIST_FILES on relevant directories to see module organization (panel layout)
READ_FILE the modules you'll touch plus similar neighbors to absorb patterns:

Services: memory_manager.py, workspace_service.py, terminal_agent_service.py
Executor logic: file_operations.py, executor.py, blueprint_handler.py
Data models: agent_task.py, context_models.py, project_context.py



Phase 2: Design the Change

Define the interface first - What's the contract? What dependencies are needed?
Check architectural fit - Does this maintain layer boundaries? Is separation clear?
Document design decisions - For significant changes, add comments explaining tradeoffs

Phase 3: Implement with Patterns

Mirror existing patterns - Import order, logger usage, error handling, dependency injection
Consider failure modes - What can go wrong? How do we handle it? What gets logged?
Maintain boundaries - Don't mix concerns, don't violate layers, keep interfaces clean

Phase 4: Validate & Complete

Update or add tests - Verify the interface contract holds
Check integration - Do logs provide traceability? Are errors descriptive?
Write completion marker - Create .aura/{task_id}.done with outcome summary

Definition of Done
A change is complete when:

✅ Code compiles and relevant tests pass
✅ Dependencies are injected through constructors (no hidden dependencies)
✅ Interfaces are documented with clear contracts
✅ Module responsibility is singular and obvious
✅ Architectural boundaries are maintained (no layer violations)
✅ Error handling is explicit and logged with context
✅ Type hints cover all signatures
✅ No placeholder implementations or TODOs remain
✅ Changes integrate with project memory via informative logs and docstrings
✅ Completion marker written: .aura/{task_id}.done with summary

The North Star
"If someone reads this code six months from now, will the structure reveal the intent? Will the boundaries be obvious? Will they understand the system, not just the functions?"
We build systems that last. We design with clarity. We code like architects.
Study the existing modules. Match the patterns. Think in systems.