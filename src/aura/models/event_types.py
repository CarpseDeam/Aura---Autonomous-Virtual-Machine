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
