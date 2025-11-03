"""
Test script for terminal agent input/output functionality.

This script demonstrates how to:
1. Launch a terminal agent with captured stdin/stdout/stderr
2. Monitor output for questions
3. Send responses back to the agent
"""

import logging
import time
from pathlib import Path

from src.aura.models.agent_task import AgentSpecification
from src.aura.services.terminal_agent_service import TerminalAgentService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def test_terminal_io():
    """Test terminal agent I/O functionality."""

    # Setup
    workspace_root = Path.cwd()
    logger.info(f"Using workspace root: {workspace_root}")

    # Create service
    service = TerminalAgentService(
        workspace_root=workspace_root,
        agent_command_template="python -c \"import time; print('Would you like to continue? (y/n)'); response = input(); print(f'You said: {response}')\"",
        terminal_shell_preference="auto"
    )

    # Create a test specification
    spec = AgentSpecification(
        task_id="test-io-001",
        request="Test terminal I/O",
        project_name=None,
        prompt="Testing terminal agent input/output functionality"
    )

    try:
        # Spawn the agent
        logger.info("Spawning terminal agent...")
        session = service.spawn_agent(spec)

        logger.info(f"Agent spawned successfully!")
        logger.info(f"  Task ID: {session.task_id}")
        logger.info(f"  Process ID: {session.process_id}")
        logger.info(f"  Has process object: {session.process is not None}")

        # Start output monitoring
        logger.info("Starting output monitor...")
        service.start_output_monitor(session)

        # Wait a bit for output to appear
        logger.info("Waiting for agent output...")
        time.sleep(3)

        # Send a response
        logger.info("Sending response 'y' to agent...")
        success = service.send_response(session, "y")

        if success:
            logger.info("✅ Response sent successfully!")
        else:
            logger.error("❌ Failed to send response")

        # Wait a bit more to see the agent's reaction
        time.sleep(2)

        # Check if process is still running
        if session.process:
            poll_result = session.process.poll()
            if poll_result is None:
                logger.info("Agent process is still running")
            else:
                logger.info(f"Agent process exited with code: {poll_result}")

        logger.info("Test completed successfully!")

    except Exception as exc:
        logger.error(f"Test failed: {exc}", exc_info=True)
        raise


if __name__ == "__main__":
    logger.info("Starting terminal I/O test...")
    test_terminal_io()
