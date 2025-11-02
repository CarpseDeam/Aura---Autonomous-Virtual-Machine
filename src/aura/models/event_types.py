"""
Event Type Constants

Centralized definitions for all event types used in Aura's event bus.
"""

# User interaction events
SEND_USER_MESSAGE = "SEND_USER_MESSAGE"
USER_MESSAGE_SENT = "USER_MESSAGE_SENT"

# LLM response events
MODEL_CHUNK_RECEIVED = "MODEL_CHUNK_RECEIVED"
MODEL_STREAM_ENDED = "MODEL_STREAM_ENDED"

# Conversation lifecycle events
CONVERSATION_SESSION_STARTED = "CONVERSATION_SESSION_STARTED"
"""
Dispatched when a conversation session becomes active.

Payload:
    session_id (str): Identifier for the active session
    project_name (str): Name of the project in context
    started_at (str, optional): Timestamp when the session began
"""

CONVERSATION_MESSAGE_ADDED = "CONVERSATION_MESSAGE_ADDED"
"""
Dispatched whenever a message is appended to the active conversation history.

Payload:
    session_id (str): Identifier for the conversation session
    project_name (str): Project associated with the session
    role (str): Message author role ('user', 'assistant', etc.)
    content (str): Message content (may be empty)
    token_usage (dict|int, optional): Provider-reported token usage metadata
"""

CONVERSATION_THREAD_SWITCHED = "CONVERSATION_THREAD_SWITCHED"
"""
Dispatched when the user switches to a different conversation thread.

Payload:
    session_id (str): Identifier for the conversation session being switched to
    project_name (str): Project associated with the session
    previous_session_id (str, optional): ID of the previous active session
    message_count (int): Number of messages in the conversation
"""

# Token usage events
TOKEN_USAGE_UPDATED = "TOKEN_USAGE_UPDATED"
"""
Dispatched whenever the running token counter is updated.

Payload:
    session_id (str): Active conversation session identifier
    current_tokens (int): Tokens consumed in the active session
    token_limit (int): Maximum token allowance for the session
    percent_used (float): Fraction of the limit consumed (0-1 range)
"""

TOKEN_THRESHOLD_CROSSED = "TOKEN_THRESHOLD_CROSSED"
"""
Dispatched when token usage crosses a configured threshold.

Payload:
    session_id (str): Active conversation session identifier
    threshold (float): Threshold ratio that was crossed (e.g., 0.7)
    current_tokens (int): Tokens consumed in the active session
    token_limit (int): Maximum token allowance for the session
    percent_used (float): Fraction of the limit consumed (0-1 range)
"""

# Blueprint and specification events
BLUEPRINT_GENERATED = "BLUEPRINT_GENERATED"
AGENT_SPEC_READY = "AGENT_SPEC_READY"

# Project and workspace events
PROJECT_ACTIVATED = "PROJECT_ACTIVATED"
FILE_DIFF_READY = "FILE_DIFF_READY"
FILE_CHANGES_APPLIED = "FILE_CHANGES_APPLIED"
FILE_CHANGES_REJECTED = "FILE_CHANGES_REJECTED"

# Build and generation progress events
BUILD_STARTED = "BUILD_STARTED"
BUILD_COMPLETED = "BUILD_COMPLETED"
GENERATION_PROGRESS = "GENERATION_PROGRESS"

# Terminal session lifecycle events
TERMINAL_SESSION_STARTED = "TERMINAL_SESSION_STARTED"
"""
Dispatched when a new terminal agent session is spawned.

Payload:
    task_id (str): Unique identifier for the task
    process_id (int): OS process ID
    command (list): Command that was executed
    started_at (str): ISO timestamp of session start
"""

TERMINAL_SESSION_PROGRESS = "TERMINAL_SESSION_PROGRESS"
"""
Dispatched periodically to report progress on an active session.

Payload:
    task_id (str): Unique identifier for the task
    changes_detected (int): Number of workspace changes detected
    status (str): Current status (running, checking, etc.)
"""

TERMINAL_SESSION_COMPLETED = "TERMINAL_SESSION_COMPLETED"
"""
Dispatched when a terminal session completes successfully.

Payload:
    task_id (str): Unique identifier for the task
    completion_reason (str): Why the session was considered complete
    duration_seconds (float): Total session duration
    changes_made (int): Total number of changes detected
    exit_code (int, optional): Process exit code if available
"""

TERMINAL_SESSION_FAILED = "TERMINAL_SESSION_FAILED"
"""
Dispatched when a terminal session fails.

Payload:
    task_id (str): Unique identifier for the task
    failure_reason (str): Why the session failed
    exit_code (int, optional): Process exit code if available
    error_message (str, optional): Error details
"""

TERMINAL_SESSION_TIMEOUT = "TERMINAL_SESSION_TIMEOUT"
"""
Dispatched when a terminal session exceeds its timeout.

Payload:
    task_id (str): Unique identifier for the task
    timeout_seconds (int): The timeout threshold that was exceeded
    duration_seconds (float): Actual duration
"""

TERMINAL_SESSION_ABORTED = "TERMINAL_SESSION_ABORTED"
"""
Dispatched when a user manually aborts a session.

Payload:
    task_id (str): Unique identifier for the task
    aborted_by (str): User or system identifier
"""

# Terminal I/O streaming events
TERMINAL_OUTPUT_RECEIVED = "TERMINAL_OUTPUT_RECEIVED"
"""
Dispatched whenever new terminal output is buffered from an active session.

Payload:
    task_id (str): Unique identifier for the task
    text (str): The line of output captured (without trailing newline)
    stream_type (str): 'stdout' or 'stderr'
    timestamp (str): ISO timestamp when line was captured
"""

# Application lifecycle events
APP_START = "APP_START"
APP_SHUTDOWN = "APP_SHUTDOWN"

# Automation trigger events
TRIGGER_AUTO_INTEGRATE = "TRIGGER_AUTO_INTEGRATE"
"""
Dispatched to trigger automatic result integration after session completion.

Payload:
    task_id (str): Task ID of the completed session
"""
