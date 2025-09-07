import os
import logging
from typing import List, Dict

# Application-specific imports
from src.aura.services.ast_service import ASTService

logger = logging.getLogger(__name__)


class ContextRetrievalService:
    """
    Specialized service responsible for synthesizing context for AI tasks.
    This service decouples context gathering logic from LLM communication logic.
    """

    def __init__(self, ast_service: ASTService):
        """
        Initialize the ContextRetrievalService with the AST service dependency.
        
        Args:
            ast_service: The ASTService instance for retrieving contextual information
        """
        self.ast_service = ast_service
        logger.info("ContextRetrievalService initialized with AST service dependency")

    def get_context_for_task(self, task_description: str, target_file: str) -> List[Dict]:
        """
        Primary public method for retrieving contextual information for AI tasks.
        Performs a two-stage retrieval process: semantic and structural.
        
        Args:
            task_description: Description of the task to be performed
            target_file: The target file path for the task
            
        Returns:
            List of dictionaries, each containing 'path' and 'content' of context files
        """
        logger.info(f"Retrieving context for task: '{task_description}' targeting file: '{target_file}'")
        
        # Stage 1: Semantic Retrieval - Get conceptually relevant files
        semantic_files = []
        try:
            semantic_files = self.ast_service.search_semantic_context(task_description)
            logger.debug(f"Semantic retrieval found {len(semantic_files)} files")
        except Exception as e:
            logger.warning(f"Semantic retrieval failed: {e}")
        
        # Stage 2: Structural Retrieval - Get dependency-related files
        structural_files = []
        try:
            structural_files = self.ast_service.get_relevant_context(target_file)
            logger.debug(f"Structural retrieval found {len(structural_files)} files")
        except Exception as e:
            logger.warning(f"Structural retrieval failed: {e}")
        
        # Combine and deduplicate file paths
        all_files = list(set(semantic_files + structural_files))
        logger.info(f"Combined context includes {len(all_files)} unique files")
        
        # Read content for each unique file
        context_data = []
        for file_path in all_files:
            try:
                content = self._read_file_content(file_path)
                if content is not None:
                    context_data.append({
                        'path': file_path,
                        'content': content
                    })
                    logger.debug(f"Added context file: {file_path}")
            except Exception as e:
                logger.warning(f"Failed to read context file {file_path}: {e}")
                continue
        
        logger.info(f"Successfully retrieved context for {len(context_data)} files")
        return context_data

    def _read_file_content(self, file_path: str) -> str:
        """
        Read the content of a file, handling both absolute and relative paths.
        
        Args:
            file_path: Path to the file to read
            
        Returns:
            File content as string, or None if file cannot be read
        """
        try:
            # Handle both absolute and relative paths
            if os.path.isabs(file_path):
                full_path = file_path
            else:
                # Use AST service's project root for relative paths
                if hasattr(self.ast_service, 'project_root') and self.ast_service.project_root:
                    full_path = os.path.join(self.ast_service.project_root, file_path)
                else:
                    full_path = file_path
            
            if not os.path.exists(full_path):
                logger.debug(f"File does not exist: {full_path}")
                return None
            
            with open(full_path, 'r', encoding='utf-8') as f:
                return f.read()
                
        except Exception as e:
            logger.error(f"Error reading file {file_path}: {e}")
            return None