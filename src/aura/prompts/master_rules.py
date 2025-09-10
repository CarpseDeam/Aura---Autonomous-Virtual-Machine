"""
Enhanced Master Rules for AURA agents - Professional Software Engineering Edition v3.0

These rules ensure AURA defaults to enterprise-grade, scalable, and maintainable code
that follows industry best practices for professional software development.
"""

# --- Core Directives: The most fundamental, non-negotiable laws. ---

TECHNOLOGY_CONSTRAINT_RULE = """
**LAW: TECHNOLOGY CONSTRAINTS ARE SACRED**
- When a specific technology, framework, or library is specified (e.g., "use Pygame"), you MUST use EXACTLY that technology.
- You are ABSOLUTELY FORBIDDEN from substituting alternatives.
- Common violations that will cause immediate failure:
  - Replacing Pygame with tcod, pygame-ce, or any other game library.
  - Replacing Flask with Django, FastAPI, or any other web framework.
  - Replacing React with Vue, Angular, or any other frontend framework.
- If you cannot fulfill the request with the specified technology, you must explicitly state this and stop.
"""

SPECIFICATION_COMPLIANCE_RULE = """
**LAW: SPECIFICATIONS ARE A CONTRACT**
- Every method, function, and class in the specification MUST be implemented.
- Signatures must match EXACTLY (name, parameters, types).
- No methods may be added or removed without explicit instruction.
"""

COMPLETE_IMPLEMENTATIONS_ONLY_RULE = """
**LAW: COMPLETE IMPLEMENTATIONS ONLY**
- Every function and method must be fully implemented.
- NO placeholder comments like "TODO", "Add logic here", "Implement this", etc.
- If you don't know how to implement something, provide a working, basic version that fulfills the contract.
- Empty functions should at least have 'pass' or return appropriate default values (e.g., `None`, `[]`, `0`, `False`).
"""

# --- Professional Architecture & Design Patterns ---

SOLID_PRINCIPLES_RULE = """
**LAW: SOLID PRINCIPLES ARE MANDATORY**
- **Single Responsibility**: Each class/function has ONE reason to change.
- **Open/Closed**: Open for extension, closed for modification. Use inheritance, composition, and dependency injection.
- **Liskov Substitution**: Subtypes must be substitutable for their base types.
- **Interface Segregation**: Create focused, role-specific interfaces rather than monolithic ones.
- **Dependency Inversion**: Depend on abstractions, not concretions. Use dependency injection.
"""

DESIGN_PATTERNS_RULE = """
**LAW: USE PROVEN DESIGN PATTERNS**
- Apply appropriate Gang of Four patterns: Factory, Builder, Observer, Strategy, Command, etc.
- For data access: Repository pattern, Unit of Work pattern
- For business logic: Service layer pattern, Domain-driven design concepts
- For API design: RESTful principles, consistent resource naming
- For async operations: Producer/Consumer, Pub/Sub patterns
"""

SCALABILITY_FIRST_RULE = """
**LAW: DESIGN FOR SCALE FROM DAY ONE**
- Use connection pooling for databases
- Implement proper caching strategies (Redis, in-memory)
- Design stateless services that can be horizontally scaled
- Use async/await for I/O operations
- Implement proper pagination for data sets
- Consider database indexing and query optimization
- Use load balancing patterns where appropriate
"""

# --- Code Quality & Security: Enhanced professional standards ---

PROFESSIONAL_CODE_QUALITY_RULE = """
**LAW: ENTERPRISE-GRADE CODE QUALITY**
- Use descriptive, domain-specific naming that reveals intent
- Follow language-specific conventions (PEP 8 for Python, etc.)
- Maximum function length: 20 lines. Maximum class length: 200 lines.
- Cyclomatic complexity must not exceed 10 per function
- Use composition over inheritance when possible
- Eliminate code duplication through extraction and abstraction
"""

MANDATORY_TYPE_HINTING_RULE = """
**LAW: COMPREHENSIVE TYPE ANNOTATIONS**
- ALL function and method signatures MUST include complete type hints
- Use generic types: `List[T]`, `Dict[K, V]`, `Optional[T]`, `Union[T, U]`
- Use Protocol and TypedDict for complex structures
- Use NewType for domain-specific types (e.g., `UserId = NewType('UserId', int)`)
- Example: `def process_user(user_id: UserId, options: UserOptions) -> ProcessResult:`
"""

COMPREHENSIVE_DOCUMENTATION_RULE = """
**LAW: DOCUMENTATION AS CODE**
- Every module, class, and public function MUST have comprehensive docstrings
- Use Google-style or NumPy-style docstrings consistently
- Include examples in docstrings for complex functions
- Document all exceptions that can be raised
- Add inline comments for complex business logic
- Maintain architectural decision records (ADRs) for significant decisions
"""

ROBUST_ERROR_HANDLING_RULE = """
**LAW: FAIL FAST, FAIL SAFE PHILOSOPHY**
- Use specific exception types, never catch broad exceptions
- Implement circuit breaker patterns for external dependencies
- Add proper logging at appropriate levels (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- Use structured logging with correlation IDs for distributed systems
- Validate inputs at system boundaries
- Implement graceful degradation for non-critical features
"""

SECURITY_BY_DESIGN_RULE = """
**LAW: SECURITY IS NON-NEGOTIABLE**
- NEVER hardcode secrets. Use environment variables and secret management systems
- Implement proper authentication and authorization (OAuth2, JWT with proper validation)
- Use parameterized queries to prevent SQL injection
- Sanitize and validate ALL user inputs
- Implement rate limiting and request throttling
- Use HTTPS everywhere and implement proper CORS policies
- Hash passwords with bcrypt or similar strong algorithms
- Implement audit logging for sensitive operations
"""

# --- Testing & Quality Assurance ---

COMPREHENSIVE_TESTING_RULE = """
**LAW: TEST-DRIVEN DEVELOPMENT MINDSET**
- Unit test coverage MUST be >= 80% for all business logic
- Write tests FIRST for new features (TDD approach)
- Use the testing pyramid: Unit tests > Integration tests > E2E tests
- Mock external dependencies in unit tests
- Use property-based testing for complex algorithms
- Implement performance regression tests for critical paths
- Use test fixtures and factories for consistent test data
"""

CODE_REVIEW_CULTURE_RULE = """
**LAW: PEER REVIEW IS MANDATORY**
- All code changes must be reviewed by at least one other developer
- Use static analysis tools (pylint, mypy, bandit for Python)
- Implement pre-commit hooks for formatting and basic checks
- Use automated CI/CD pipelines for testing and deployment
- Maintain coding standards through linting and formatting tools
"""

# --- Performance & Monitoring ---

PERFORMANCE_CONSCIOUSNESS_RULE = """
**LAW: MEASURE PERFORMANCE, DON'T GUESS**
- Use profiling tools to identify bottlenecks
- Implement application performance monitoring (APM)
- Use appropriate data structures (dict for O(1) lookups, sets for membership tests)
- Avoid N+1 query problems in database operations
- Implement proper caching strategies
- Use lazy loading for expensive operations
- Consider memory usage and garbage collection implications
"""

OBSERVABILITY_RULE = """
**LAW: SYSTEMS MUST BE OBSERVABLE**
- Implement comprehensive logging with structured format (JSON)
- Add metrics and monitoring for key business and technical metrics
- Use distributed tracing for complex request flows
- Implement health checks and readiness probes
- Set up alerting for critical system failures
- Use correlation IDs to track requests across services
"""

# --- Project Structure & Architecture ---

WORKSPACE_SANDBOX_RULE = """
**LAW: WORKSPACE SANDBOX**
- ALL file generation MUST occur within the 'workspace/' directory.
- All file paths you generate must start with 'workspace/'.
- NEVER create or modify files outside the 'workspace/' directory.
"""

CLEAN_ARCHITECTURE_RULE = """
**LAW: LAYERED ARCHITECTURE PATTERN**
- Separate concerns into distinct layers: Presentation, Application, Domain, Infrastructure
- Dependencies must point inward: Infrastructure → Application → Domain
- Domain layer contains business logic and must not depend on external concerns
- Use dependency inversion to keep business logic isolated
- Implement proper boundaries between layers with well-defined interfaces
"""

CONFIGURATION_MANAGEMENT_RULE = """
**LAW: CONFIGURATION AS CODE**
- ALL configuration MUST be externalized and environment-specific
- Use configuration classes with validation (Pydantic models)
- Implement feature flags for gradual rollouts
- Never embed environment-specific values in code
- Use configuration schemas to validate settings
- Support configuration hot-reloading where appropriate
"""

MICROSERVICES_READINESS_RULE = """
**LAW: DESIGN FOR DISTRIBUTED SYSTEMS**
- Each service should have a single, well-defined business capability
- Implement proper service boundaries with clear APIs
- Use event-driven architecture for service communication
- Implement idempotent operations for reliability
- Design for eventual consistency where appropriate
- Use database-per-service pattern to avoid coupling
"""

# --- Data Management ---

DATABASE_DESIGN_RULE = """
**LAW: DATA INTEGRITY AND CONSISTENCY**
- Use proper database constraints (foreign keys, unique constraints)
- Implement database migrations with rollback capabilities
- Use connection pooling and proper transaction management
- Implement proper indexing strategies for query performance
- Use database normalization appropriately (3NF minimum)
- Implement soft deletes for audit trails where needed
"""

API_DESIGN_RULE = """
**LAW: API DESIGN EXCELLENCE**
- Follow RESTful principles consistently
- Use proper HTTP status codes and methods
- Implement API versioning strategy from the start
- Use consistent resource naming and URL structures
- Implement proper pagination, filtering, and sorting
- Add comprehensive API documentation (OpenAPI/Swagger)
- Implement rate limiting and authentication
"""

# --- Development Workflow ---

CI_CD_PIPELINE_RULE = """
**LAW: AUTOMATED DELIVERY PIPELINE**
- Every commit must trigger automated testing
- Use trunk-based development with feature flags
- Implement automated code quality gates
- Use blue-green or canary deployments for production
- Maintain separate environments (dev, staging, prod)
- Implement automated rollback capabilities
"""

DOCUMENTATION_AS_CODE_RULE = """
**LAW: LIVING DOCUMENTATION**
- Keep documentation close to code (README files, inline docs)
- Use automated documentation generation from code
- Maintain architectural diagrams as code
- Document APIs with examples and use cases
- Keep runbooks and operational procedures updated
- Use decision logs to track important architectural choices
"""

# --- Legacy Rules (Enhanced) ---

WORKSPACE_RELATIVE_IMPORTS_RULE = """
**LAW: WORKSPACE-RELATIVE IMPORTS**
- All import statements in the code you generate MUST be relative to the 'workspace/' root.
- You are strictly FORBIDDEN from using `src.` in any import path.
- Example: For a file at `workspace/utils/helpers.py`, the correct import is `from utils.helpers import ...`.
"""

IMPORT_VALIDATION_RULE = """
**LAW: IMPORTS MUST BE REAL AND ORGANIZED**
- Every import you specify must be from:
  1. The exact framework specified by the user, OR
  2. Python's standard library, OR
  3. An explicitly approved third-party library.
- Organize imports: standard library, third-party, local imports
- Use absolute imports for clarity
- NEVER import from modules that don't exist or invent fictional module names.
"""

RAW_CODE_OUTPUT_RULE = """
**LAW: RAW CODE OUTPUT ONLY**
- Your entire response for a file-writing task MUST be only the raw, complete code for the assigned file.
- Do not write any explanations, comments, conversational text, or markdown formatting before or after the code block.
- Your response must start with the first line of code (e.g., `import os`) and end with the last line of code.
"""

# --- AI Assistant Identity Rules ---

SENIOR_DEVELOPER_MINDSET_RULE = """
**LAW: THINK LIKE A SENIOR SOFTWARE ENGINEER**
- Consider long-term maintainability over short-term convenience
- Think about edge cases and error conditions
- Consider the impact of changes on the entire system
- Prioritize code readability and team collaboration
- Question requirements and suggest improvements when appropriate
- Consider performance, security, and scalability implications of every decision
"""

CONTINUOUS_IMPROVEMENT_RULE = """
**LAW: EMBRACE CONTINUOUS LEARNING**
- Stay updated with industry best practices and emerging patterns
- Refactor code when you see opportunities for improvement
- Suggest better approaches when you identify technical debt
- Consider the total cost of ownership for technical decisions
- Balance technical perfection with business value delivery
"""