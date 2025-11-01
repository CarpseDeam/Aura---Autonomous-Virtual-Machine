# Aura Project Memory System

## Overview

The Project Memory System transforms Aura from a stateless code generator into a true coding companion that remembers architectural decisions, patterns, and project history across sessions. This system maintains living memory documents that capture the "why" behind your project's design and evolution.

## Why This Matters

**Before Memory System:**
- Aura forgets context between sessions
- Architectural decisions are lost
- Each session feels like starting from scratch
- No consistency in patterns across features

**After Memory System:**
- Aura remembers architectural choices (JWT vs sessions, FastAPI vs Flask)
- Code patterns are consistently applied
- Project timeline tracks feature evolution
- Known issues are documented and tracked
- Context is preserved across weeks and months

## Architecture

### Components

```
┌─────────────────────────────────────────────────────────┐
│                     AuraApp                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │ ProjectMgr   │  │ MemoryMgr    │  │ ContextMgr   │ │
│  │              │◄─┤              │◄─┤              │ │
│  └──────────────┘  └──────┬───────┘  └──────────────┘ │
└─────────────────────────────┼──────────────────────────┘
                              │
                              ▼
                      ┌───────────────┐
                      │   EventBus    │
                      └───────┬───────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│  BLUEPRINT_   │   │  TERMINAL_    │   │  TRIGGER_     │
│  GENERATED    │   │  SESSION_     │   │  AUTO_        │
│               │   │  COMPLETED    │   │  INTEGRATE    │
└───────────────┘   └───────────────┘   └───────────────┘
```

### Key Services

#### MemoryManager (`src/aura/services/memory_manager.py`)

**Responsibilities:**
- Subscribe to completion events from the event bus
- Maintain structured memory in Project.metadata
- Generate human-readable PROJECT_MEMORY.md files
- Provide memory context to ContextManager

**Key Methods:**
- `get_memory()` - Load memory from current project
- `save_memory(memory)` - Save memory to project metadata + markdown file
- `add_architecture_decision()` - Record architectural choices
- `add_code_pattern()` - Record code conventions
- `add_timeline_entry()` - Track project milestones
- `add_known_issue()` - Document technical debt
- `update_project_state()` - Update current project state
- `get_memory_context()` - Get formatted memory for AI context

#### Event Handlers

**BLUEPRINT_GENERATED:**
- Triggered when a new design blueprint is created
- Records timeline entry for blueprint creation
- Future: Parse blueprint to extract architecture decisions

**TERMINAL_SESSION_COMPLETED:**
- Triggered when a terminal session completes successfully
- Records timeline entry with completion reason and file changes
- Updates project state with last session info

**TRIGGER_AUTO_INTEGRATE:**
- Triggered after session completion for result integration
- Updates project state with integration timestamp

**PROJECT_ACTIVATED:**
- Triggered when a project is activated
- Ensures memory is initialized for the project

### Data Models

All models use Pydantic for validation and type safety:

#### ProjectMemory
```python
class ProjectMemory(BaseModel):
    project_name: str
    created_at: datetime
    last_updated: datetime
    architecture_decisions: List[ArchitectureDecision]
    code_patterns: List[CodePattern]
    timeline: List[TimelineEntry]
    known_issues: List[KnownIssue]
    current_state: Dict[str, Any]
```

#### ArchitectureDecision
```python
class ArchitectureDecision(BaseModel):
    category: str  # framework, database, auth, etc.
    decision: str  # What was chosen
    rationale: str  # Why it was chosen
    timestamp: datetime
```

#### CodePattern
```python
class CodePattern(BaseModel):
    category: str  # validation, error handling, etc.
    pattern: str  # The pattern description
    example: Optional[str]  # Optional code example
```

#### TimelineEntry
```python
class TimelineEntry(BaseModel):
    timestamp: datetime
    task_id: str
    description: str
    files_modified: List[str]
    outcome: str  # success, failure, partial
    notes: Optional[str]
```

#### KnownIssue
```python
class KnownIssue(BaseModel):
    description: str
    severity: str  # low, medium, high, critical
    discovered_at: datetime
    resolved_at: Optional[datetime]
```

### Storage

**Primary Storage:** Project metadata
`~/.aura/projects/<project_name>/project.json`
```json
{
  "name": "blog_api",
  "metadata": {
    "project_memory": {
      "project_name": "blog_api",
      "architecture_decisions": [...],
      "code_patterns": [...],
      "timeline": [...],
      "known_issues": [...],
      "current_state": {...}
    }
  }
}
```

**Human-Readable File:**
`~/.aura/projects/<project_name>/PROJECT_MEMORY.md`

Generated markdown file with sections:
- Architecture Decisions (grouped by category)
- Code Patterns We Follow (grouped by category)
- Project Timeline (most recent 20 entries)
- Current State
- Known Issues (grouped by severity)

## Integration with Context

The MemoryManager integrates with ContextManager to include project memory in AI context:

```python
# In ContextManager._build_context_window()
if self.memory_manager:
    memory_context = self.memory_manager.get_memory_context()
    metadata["project_memory"] = memory_context
```

This ensures that every time Aura loads context for a task, it includes:
1. Relevant files (based on semantic similarity)
2. Dependencies (if enabled)
3. **Project memory** (architectural decisions, patterns, timeline)

The AI now has consistent context across sessions!

## Usage Examples

### Example 1: New Project with Architecture Decisions

```python
# When blueprint is generated
# MemoryManager automatically adds timeline entry

# Later, manually record an architecture decision
memory_manager.add_architecture_decision(
    category="framework",
    decision="FastAPI with async/await",
    rationale="Need high-performance async for WebSocket support"
)

memory_manager.add_architecture_decision(
    category="database",
    decision="PostgreSQL + SQLAlchemy ORM",
    rationale="Need ACID compliance and complex query support"
)
```

**Result in PROJECT_MEMORY.md:**
```markdown
## Architecture Decisions

### database
- **PostgreSQL + SQLAlchemy ORM**: Need ACID compliance and complex query support

### framework
- **FastAPI with async/await**: Need high-performance async for WebSocket support
```

### Example 2: Recording Code Patterns

```python
memory_manager.add_code_pattern(
    category="validation",
    pattern="All request models use Pydantic with Field() descriptions",
    example="class UserCreate(BaseModel):\n    email: str = Field(..., description='User email')"
)

memory_manager.add_code_pattern(
    category="error_handling",
    pattern="Custom HTTPException classes for domain errors"
)
```

### Example 3: Tracking Timeline

```python
# Automatically tracked when terminal sessions complete
# Or manually add significant milestones

memory_manager.add_timeline_entry(
    task_id="feature-auth",
    description="Implemented JWT authentication with refresh tokens",
    outcome="success",
    files_modified=["auth/jwt.py", "auth/models.py", "tests/test_auth.py"],
    notes="Added 2FA support"
)
```

### Example 4: Documenting Known Issues

```python
memory_manager.add_known_issue(
    description="Rate limiter needs better error handling for edge cases",
    severity="medium"
)

memory_manager.add_known_issue(
    description="Password reset flow not implemented yet",
    severity="high"
)
```

### Example 5: Updating Project State

```python
memory_manager.update_project_state({
    "total_files": 47,
    "lines_of_code": 3200,
    "test_coverage": "87%",
    "next_milestone": "Add image upload for posts"
})
```

## Generated PROJECT_MEMORY.md Example

```markdown
# Project Memory: blog_api

*Last updated: 2025-01-15 14:30 UTC*

## Architecture Decisions

### database
- **PostgreSQL + SQLAlchemy ORM**: Need ACID compliance and complex query support

### framework
- **FastAPI with async/await**: Need high-performance async for WebSocket support

### auth
- **JWT tokens**: Stateless API design for horizontal scaling

## Code Patterns We Follow

### validation
- All request models use Pydantic with Field() descriptions

### error_handling
- Custom HTTPException classes for domain errors

## Project Timeline

- **2025-01-15** ✅: Implemented JWT authentication with refresh tokens
  - *Added 2FA support*
- **2025-01-10** ✅: Added rate limiting to auth endpoints
- **2025-01-05** ✅: Design blueprint generated
  - *Initial project architecture defined*

## Current State

- **total_files**: 47
- **lines_of_code**: 3200
- **test_coverage**: 87%
- **next_milestone**: Add image upload for posts

## Known Issues

### 🔴 High
- Password reset flow not implemented yet

### 🟡 Medium
- Rate limiter needs better error handling for edge cases

---

*This file is auto-generated by Aura's Project Memory System.*
*Last sync: 2025-01-15 14:30:45 UTC*
```

## How It Works: End-to-End

### Scenario: Creating a New Feature

1. **User:** "Add JWT authentication"

2. **Blueprint Generation:**
   - AuraApp dispatches `BLUEPRINT_GENERATED` event
   - MemoryManager receives event → adds timeline entry

3. **Terminal Session:**
   - Terminal agent implements auth
   - Session completes → dispatches `TERMINAL_SESSION_COMPLETED`
   - MemoryManager receives event → adds timeline entry with file changes
   - Updates project state with last session info

4. **Auto-Integration:**
   - AuraApp dispatches `TRIGGER_AUTO_INTEGRATE`
   - MemoryManager updates integration state

5. **Memory Saved:**
   - ProjectMemory updated in project.json metadata
   - PROJECT_MEMORY.md regenerated on disk

6. **Next Session:**
   - User reopens Aura tomorrow
   - ContextManager loads context for new request
   - MemoryManager provides memory context
   - AI receives:
     - Relevant files
     - **Project memory** (knows JWT auth exists, patterns used, etc.)
   - AI builds on existing architecture instead of starting fresh!

## Configuration

### Disabling Memory System

If you need to disable the memory system:

```python
# In aura_app.py, comment out memory manager initialization:
# self.memory_manager = MemoryManager(...)
```

Context will still work, just without memory context.

### Adjusting Event Handlers

To customize which events trigger memory updates:

```python
# In MemoryManager._register_event_handlers()
self.event_bus.subscribe(YOUR_CUSTOM_EVENT, self._your_handler)
```

## Performance Considerations

**Storage:**
- Memory stored in project.json (lightweight JSON)
- Markdown file generated on save (negligible overhead)

**Context Loading:**
- Memory context included in ContextWindow metadata
- No token budget impact (metadata doesn't count toward file token budget)
- Cached in memory once loaded

**Event Processing:**
- Event handlers run asynchronously
- No blocking operations in critical path
- All file I/O wrapped in try-except for resilience

## Error Handling

The memory system is designed to never break Aura:

```python
# All event handlers wrapped in try-except
try:
    self.add_timeline_entry(...)
except Exception as exc:
    logger.error(f"Error processing event: {exc}")
    # Continue without memory update
```

If memory fails to load or save:
- Logs warning
- Continues without memory
- Aura remains fully functional

## Future Enhancements

### Planned Features

1. **Blueprint Parsing:** Extract architecture decisions from blueprint text automatically
2. **Memory Search:** Query memory by keyword or category
3. **Memory Diff:** Compare memory across project versions
4. **Memory Export:** Export memory to shareable formats (JSON, PDF)
5. **Memory Analytics:** Visualize project evolution over time
6. **Cross-Project Memory:** Learn patterns across multiple projects
7. **Smart Suggestions:** AI suggests patterns based on memory

### Extensibility

The system is designed for easy extension:

```python
# Add custom memory types
class CustomMemoryType(BaseModel):
    your_field: str
    timestamp: datetime

# Extend ProjectMemory
class ExtendedProjectMemory(ProjectMemory):
    custom_data: List[CustomMemoryType] = Field(default_factory=list)

# Use in MemoryManager
def add_custom_entry(self, data: str):
    memory = self.get_memory()
    entry = CustomMemoryType(your_field=data)
    memory.custom_data.append(entry)
    self.save_memory(memory)
```

## Troubleshooting

### Memory Not Loading

**Check:**
1. Is MemoryManager initialized? (`logging.info("MemoryManager initialized successfully")`)
2. Is project.json readable? (`~/.aura/projects/<project_name>/project.json`)
3. Any parsing errors in logs?

**Solution:**
- Delete corrupted memory: Remove `"project_memory"` from project.json
- Memory will reinitialize on next load

### PROJECT_MEMORY.md Not Generated

**Check:**
1. Permissions on `~/.aura/projects/<project_name>/`
2. Disk space available
3. File I/O errors in logs

**Solution:**
- Memory still works (stored in project.json)
- Markdown is just human-readable view

### Events Not Triggering Updates

**Check:**
1. Is EventBus working? (Other events dispatching?)
2. Are event handlers registered? (Check logs for "Registered event handlers")
3. Is event payload correct? (Check event_types.py for payload structure)

**Solution:**
- Manually call memory methods:
  ```python
  app.memory_manager.add_timeline_entry(...)
  ```

## Testing

### Manual Testing

```python
# Create test project
project_manager = ProjectManager()
project = project_manager.create_project("test_memory", "/tmp/test")

# Initialize memory manager
memory_manager = MemoryManager(project_manager=project_manager)

# Add test data
memory_manager.add_architecture_decision(
    category="test",
    decision="Test decision",
    rationale="Testing memory system"
)

# Verify
memory = memory_manager.get_memory()
assert len(memory.architecture_decisions) == 1

# Check markdown generated
memory_file = Path("~/.aura/projects/test_memory/PROJECT_MEMORY.md").expanduser()
assert memory_file.exists()
```

### Unit Tests

See `tests/test_memory_manager.py` (TODO: create comprehensive test suite)

## API Reference

See inline documentation in `src/aura/services/memory_manager.py` for complete API reference.

### Quick Reference

**Loading Memory:**
```python
memory = memory_manager.get_memory()  # Returns ProjectMemory or None
```

**Saving Memory:**
```python
memory_manager.save_memory(memory)  # Saves to metadata + generates markdown
```

**Adding Entries:**
```python
memory_manager.add_architecture_decision(category, decision, rationale)
memory_manager.add_code_pattern(category, pattern, example=None)
memory_manager.add_timeline_entry(task_id, description, outcome, files_modified, notes)
memory_manager.add_known_issue(description, severity)
memory_manager.update_project_state(state_updates)
```

**Getting Context:**
```python
context_str = memory_manager.get_memory_context()  # Returns markdown string
```

## Contributing

When adding new memory types or features:

1. Add Pydantic model to `memory_manager.py`
2. Update `ProjectMemory` to include new field
3. Add public method to `MemoryManager` for adding entries
4. Update `_generate_markdown_content()` to render new section
5. Update this documentation
6. Add tests

## Summary

The Project Memory System is a game-changer for Aura:

✅ **Persistent Context:** Remembers across sessions
✅ **Architectural Consistency:** Records decisions and patterns
✅ **Project Evolution:** Tracks timeline and milestones
✅ **Technical Debt:** Documents known issues
✅ **AI Context:** Includes memory in every context load
✅ **Human-Readable:** Generated markdown files
✅ **Production-Ready:** Full error handling, logging, type safety
✅ **Event-Driven:** Automatic updates on completion events

This transforms Aura from a stateless tool into a true coding companion that grows with your projects! 🚀
