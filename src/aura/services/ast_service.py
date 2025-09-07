import ast
import os
import logging
from typing import List, Dict, Set
from pathlib import Path

# Application-specific imports
from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event

logger = logging.getLogger(__name__)


class ASTAnalyzer(ast.NodeVisitor):
    """
    AST visitor that extracts imports, functions, and classes from Python source code.
    """

    def __init__(self):
        self.imports = []
        self.functions = []
        self.classes = []

    def visit_Import(self, node: ast.Import):
        """Extract regular import statements."""
        for alias in node.names:
            self.imports.append({
                'type': 'import',
                'module': alias.name,
                'name': alias.asname or alias.name,
                'lineno': node.lineno
            })
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        """Extract from ... import ... statements."""
        module = node.module or ''
        level = node.level  # Number of dots for relative imports
        
        for alias in node.names:
            self.imports.append({
                'type': 'from_import',
                'module': module,
                'name': alias.name,
                'asname': alias.asname,
                'level': level,
                'lineno': node.lineno
            })
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        """Extract function definitions."""
        args = [arg.arg for arg in node.args.args]
        self.functions.append({
            'name': node.name,
            'args': args,
            'lineno': node.lineno,
            'is_async': False
        })
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        """Extract async function definitions."""
        args = [arg.arg for arg in node.args.args]
        self.functions.append({
            'name': node.name,
            'args': args,
            'lineno': node.lineno,
            'is_async': True
        })
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef):
        """Extract class definitions."""
        bases = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                bases.append(base.id)
            elif isinstance(base, ast.Attribute):
                bases.append(ast.unparse(base))
        
        self.classes.append({
            'name': node.name,
            'bases': bases,
            'lineno': node.lineno
        })
        self.generic_visit(node)


class ASTService:
    """
    Aura's code intelligence engine that parses Python projects into semantic maps
    for Retrieval-Augmented Generation (RAG) system for code.
    """

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.project_index: Dict[str, Dict] = {}
        self.project_root: str = ""
        self._register_event_handlers()
        logger.info("ASTService initialized with event-driven dynamic updates.")

    def index_project(self, project_path: str) -> None:
        """
        Main entry point to scan and parse a project into the semantic index.
        
        Args:
            project_path: Root path of the Python project to analyze
        """
        logger.info(f"Starting project indexing for: {project_path}")
        
        # Clear the old index
        self.project_index.clear()
        self.project_root = os.path.abspath(project_path)
        
        indexed_count = 0
        error_count = 0
        
        # Walk through every file in the project
        for root, dirs, files in os.walk(project_path):
            # Skip virtual environments and cache directories
            dirs[:] = [d for d in dirs if d not in {'.venv', '__pycache__', '.git', 'node_modules'}]
            
            for file in files:
                if file.endswith('.py'):
                    file_path = os.path.join(root, file)
                    relative_path = os.path.relpath(file_path, project_path)
                    
                    try:
                        # Read and parse the file
                        with open(file_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                        
                        tree = ast.parse(content)
                        analysis = self._analyze_ast(tree, file_path)
                        
                        # Store results keyed by relative path for consistency
                        self.project_index[relative_path] = analysis
                        indexed_count += 1
                        
                    except Exception as e:
                        logger.warning(f"Failed to parse {file_path}: {str(e)}")
                        error_count += 1
        
        logger.info(f"Project indexing complete. Indexed {indexed_count} files, {error_count} errors.")

    def _analyze_ast(self, tree: ast.AST, file_path: str) -> Dict:
        """
        Extract structured information from an AST tree.
        
        Args:
            tree: The parsed AST tree
            file_path: Full path to the source file
            
        Returns:
            Dictionary with imports, functions, and classes information
        """
        analyzer = ASTAnalyzer()
        analyzer.visit(tree)
        
        return {
            'file_path': file_path,
            'imports': analyzer.imports,
            'functions': analyzer.functions,
            'classes': analyzer.classes,
            'total_imports': len(analyzer.imports),
            'total_functions': len(analyzer.functions),
            'total_classes': len(analyzer.classes)
        }

    def _register_event_handlers(self):
        """Register event handlers for dynamic index updates."""
        self.event_bus.subscribe("CODE_GENERATED", self.update_index_for_file)
        logger.info("ASTService subscribed to CODE_GENERATED events for dynamic updates")

    def update_index_for_file(self, event: Event):
        """
        Dynamically update the AST index for a specific file when new code is generated.
        This ensures the knowledge graph stays perfectly synchronized with code changes.
        
        Args:
            event: Event containing file_path and code payload
        """
        file_path = event.payload.get("file_path")
        code = event.payload.get("code")
        
        if not file_path or not code:
            logger.warning("CODE_GENERATED event missing file_path or code payload")
            return
        
        logger.info(f"Dynamic update requested for: {file_path}")
        
        try:
            # Parse the new source code into an AST tree
            tree = ast.parse(code)
            
            # Analyze the AST to extract structured information
            analysis = self._analyze_ast(tree, file_path)
            
            # Update the master index with the new analysis
            # This handles both new files and updates to existing files
            normalized_path = self._normalize_path(file_path)
            self.project_index[normalized_path] = analysis
            
            logger.info(f"AST index successfully updated for: {file_path}")
            logger.debug(f"Updated analysis: {analysis['total_functions']} functions, "
                        f"{analysis['total_classes']} classes, {analysis['total_imports']} imports")
            
        except SyntaxError as e:
            logger.error(f"Syntax error in generated code for {file_path}: {str(e)}")
        except Exception as e:
            logger.error(f"Failed to update AST index for {file_path}: {str(e)}")    

    def get_relevant_context(self, target_file: str) -> List[str]:
        """
        Intelligent context retrieval using AST-powered dependency analysis.
        
        Args:
            target_file: The file to analyze for dependencies
            
        Returns:
            List of file paths that should be included as context
        """
        context_files: Set[str] = set()
        context_files.add(target_file)
        
        # Normalize target file path
        normalized_target = self._normalize_path(target_file)
        
        if normalized_target not in self.project_index:
            logger.warning(f"Target file {normalized_target} not found in project index")
            return [target_file]
        
        # Get imports from target file
        file_info = self.project_index[normalized_target]
        imports = file_info.get('imports', [])
        
        # Resolve each import to actual file paths
        for import_info in imports:
            resolved_paths = self._resolve_import(import_info, normalized_target)
            context_files.update(resolved_paths)
        
        # Remove any non-existent files
        valid_context_files = []
        for file_path in context_files:
            if file_path in self.project_index or os.path.exists(os.path.join(self.project_root, file_path)):
                valid_context_files.append(file_path)
        
        logger.info(f"Found {len(valid_context_files)} relevant context files for {target_file}")
        return valid_context_files

    def _resolve_import(self, import_info: Dict, current_file: str) -> List[str]:
        """
        Resolve an import statement to actual file paths within the project.
        
        Args:
            import_info: Dictionary containing import details
            current_file: The file containing this import
            
        Returns:
            List of resolved file paths
        """
        resolved_paths = []
        
        if import_info['type'] == 'import':
            # Regular import: import module
            module_path = self._module_to_path(import_info['module'])
            if module_path:
                resolved_paths.append(module_path)
                
        elif import_info['type'] == 'from_import':
            # From import: from module import name
            level = import_info.get('level', 0)
            module = import_info.get('module', '')
            
            if level > 0:
                # Relative import
                resolved_module = self._resolve_relative_import(module, current_file, level)
            else:
                # Absolute import
                resolved_module = module
            
            if resolved_module:
                module_path = self._module_to_path(resolved_module)
                if module_path:
                    resolved_paths.append(module_path)
        
        return resolved_paths

    def _resolve_relative_import(self, module: str, current_file: str, level: int) -> str:
        """
        Resolve relative imports like 'from ..models import User'.
        
        Args:
            module: The module name (can be empty for relative imports)
            current_file: Path to the file containing the import
            level: Number of dots in the relative import
            
        Returns:
            Resolved absolute module path
        """
        current_dir = os.path.dirname(current_file)
        
        # Go up 'level-1' directories
        for _ in range(level - 1):
            current_dir = os.path.dirname(current_dir)
        
        if module:
            resolved_path = os.path.join(current_dir, module.replace('.', os.sep))
        else:
            resolved_path = current_dir
        
        return resolved_path.replace(os.sep, '.')

    def _module_to_path(self, module_name: str) -> str:
        """
        Convert a module name to a file path within the project.
        
        Args:
            module_name: Python module name (e.g., 'src.aura.models')
            
        Returns:
            Relative file path or empty string if not found
        """
        # Convert module.name to path/name.py
        module_path = module_name.replace('.', os.sep)
        
        # Try different variations
        candidates = [
            f"{module_path}.py",
            f"{module_path}/__init__.py",
            f"{module_path}/main.py"
        ]
        
        for candidate in candidates:
            if candidate in self.project_index:
                return candidate
            # Also check if the actual file exists
            full_path = os.path.join(self.project_root, candidate)
            if os.path.exists(full_path):
                return candidate
        
        return ""

    def _normalize_path(self, file_path: str) -> str:
        """
        Normalize file path to be relative to project root.
        
        Args:
            file_path: Input file path (can be absolute or relative)
            
        Returns:
            Normalized relative path
        """
        if os.path.isabs(file_path):
            return os.path.relpath(file_path, self.project_root)
        return file_path

    def get_file_info(self, file_path: str) -> Dict:
        """
        Get detailed analysis information for a specific file.
        
        Args:
            file_path: Path to the file
            
        Returns:
            Dictionary containing file analysis or empty dict if not found
        """
        normalized_path = self._normalize_path(file_path)
        return self.project_index.get(normalized_path, {})

    def search_functions(self, function_name: str) -> List[Dict]:
        """
        Search for functions by name across the entire project.
        
        Args:
            function_name: Name of the function to search for
            
        Returns:
            List of dictionaries containing function info and file locations
        """
        results = []
        for file_path, file_info in self.project_index.items():
            for func in file_info.get('functions', []):
                if func['name'] == function_name:
                    results.append({
                        'file': file_path,
                        'function': func
                    })
        return results

    def search_classes(self, class_name: str) -> List[Dict]:
        """
        Search for classes by name across the entire project.
        
        Args:
            class_name: Name of the class to search for
            
        Returns:
            List of dictionaries containing class info and file locations
        """
        results = []
        for file_path, file_info in self.project_index.items():
            for cls in file_info.get('classes', []):
                if cls['name'] == class_name:
                    results.append({
                        'file': file_path,
                        'class': cls
                    })
        return results

    def get_project_stats(self) -> Dict:
        """
        Get overall statistics about the indexed project.
        
        Returns:
            Dictionary containing project statistics
        """
        total_files = len(self.project_index)
        total_functions = sum(len(info.get('functions', [])) for info in self.project_index.values())
        total_classes = sum(len(info.get('classes', [])) for info in self.project_index.values())
        total_imports = sum(len(info.get('imports', [])) for info in self.project_index.values())
        
        return {
            'total_files': total_files,
            'total_functions': total_functions,
            'total_classes': total_classes,
            'total_imports': total_imports,
            'project_root': self.project_root
        }