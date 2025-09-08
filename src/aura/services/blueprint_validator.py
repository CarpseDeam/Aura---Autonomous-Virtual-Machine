import logging
import json
from typing import Dict, List, Tuple, Any

logger = logging.getLogger(__name__)


class BlueprintValidator:
    """
    The Guardian Protocol: Deterministic validation service for architect blueprints.
    
    This validator ensures that architect-generated blueprints comply with 
    non-negotiable project constraints defined in mission contracts.
    
    Prevents architectural drift by programmatically enforcing rules before
    any tasks are created or code is generated.
    """
    
    def __init__(self):
        """Initialize the Blueprint Validator."""
        logger.info("Guardian Protocol: BlueprintValidator initialized")
    
    def validate(self, blueprint: Dict[str, Any], contract: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Primary validation method that enforces all contract rules against the blueprint.
        
        Args:
            blueprint: The architect's generated blueprint dictionary
            contract: The mission contract dictionary with validation rules
            
        Returns:
            Tuple of (is_valid: bool, error_messages: List[str])
            - is_valid: True if blueprint passes all contract rules, False otherwise
            - error_messages: List of human-readable validation failure messages
        """
        logger.info("Guardian Protocol: Starting blueprint validation")
        
        errors = []
        
        # Get validation rules from contract
        rules = contract.get('validation_rules', {})
        
        # Execute file_check rules
        if 'file_check' in rules:
            file_errors = self._validate_file_requirements(blueprint, rules['file_check'])
            errors.extend(file_errors)
        
        # Execute dependency_check rules
        if 'dependency_check' in rules:
            dependency_errors = self._validate_dependency_restrictions(blueprint, rules['dependency_check'])
            errors.extend(dependency_errors)
        
        # Determine overall validation result
        is_valid = len(errors) == 0
        
        if is_valid:
            logger.info("Guardian Protocol: Blueprint validation PASSED")
        else:
            logger.warning(f"Guardian Protocol: Blueprint validation FAILED with {len(errors)} violations")
            for error in errors:
                logger.warning(f"Guardian Protocol Violation: {error}")
        
        return is_valid, errors
    
    def _validate_file_requirements(self, blueprint: Dict[str, Any], file_check_rules: Dict[str, Any]) -> List[str]:
        """
        Validates that required files are present in the blueprint.
        
        Args:
            blueprint: The architect's generated blueprint
            file_check_rules: Dictionary containing file validation rules
            
        Returns:
            List of error messages for any file requirement violations
        """
        errors = []
        
        # Check for must_exist files
        must_exist = file_check_rules.get('must_exist', [])
        blueprint_files = set(blueprint.keys())
        
        for required_file in must_exist:
            if required_file not in blueprint_files:
                errors.append(f"REQUIRED FILE MISSING: '{required_file}' must be included in the blueprint")
        
        # Check for must_not_exist files
        must_not_exist = file_check_rules.get('must_not_exist', [])
        for forbidden_file in must_not_exist:
            if forbidden_file in blueprint_files:
                errors.append(f"FORBIDDEN FILE PRESENT: '{forbidden_file}' must not be included in the blueprint")
        
        return errors
    
    def _validate_dependency_restrictions(self, blueprint: Dict[str, Any], dependency_check_rules: Dict[str, Any]) -> List[str]:
        """
        Validates that forbidden dependencies are not present anywhere in the blueprint.
        
        This method converts the entire blueprint to a string and searches for
        forbidden keywords, ensuring no prohibited dependencies slip through.
        
        Args:
            blueprint: The architect's generated blueprint
            dependency_check_rules: Dictionary containing dependency validation rules
            
        Returns:
            List of error messages for any dependency restriction violations
        """
        errors = []
        
        # Convert entire blueprint to searchable string
        blueprint_string = json.dumps(blueprint, indent=2).lower()
        
        # Check for forbidden dependencies
        must_not_include = dependency_check_rules.get('must_not_include', [])
        for forbidden_dep in must_not_include:
            if forbidden_dep.lower() in blueprint_string:
                errors.append(f"FORBIDDEN DEPENDENCY DETECTED: '{forbidden_dep}' is not allowed in this project")
        
        # Check for required dependencies
        must_include = dependency_check_rules.get('must_include', [])
        for required_dep in must_include:
            if required_dep.lower() not in blueprint_string:
                errors.append(f"REQUIRED DEPENDENCY MISSING: '{required_dep}' must be present in the blueprint")
        
        return errors
