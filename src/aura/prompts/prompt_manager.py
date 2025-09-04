import logging
import inspect
from jinja2 import Environment, FileSystemLoader, select_autoescape
from src.aura.config import ROOT_DIR
from src.aura.prompts import master_rules

logger = logging.getLogger(__name__)

class PromptManager:
    """
    Manages loading and rendering of Jinja2 prompt templates.
    """
    def __init__(self):
        """Initializes the PromptManager."""
        template_dir = ROOT_DIR / "src" / "aura" / "prompts" / "templates"
        if not template_dir.exists():
            logger.error(f"Prompt template directory not found at: {template_dir}")
            raise FileNotFoundError(f"Prompt template directory not found: {template_dir}")

        self.env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape()
        )
        self._load_master_rules_as_globals()
        logger.info("PromptManager initialized and master rules loaded.")

    def _load_master_rules_as_globals(self):
        """
        Inspects the master_rules module and loads all uppercase constants
        as global variables in the Jinja2 environment.
        """
        for name, value in inspect.getmembers(master_rules):
            if name.isupper() and isinstance(value, str):
                self.env.globals[name] = value

    def render(self, template_name: str, **kwargs) -> str:
        """
        Renders a prompt template with the given context.

        Args:
            template_name: The name of the template file (e.g., 'generate_code.jinja2').
            **kwargs: The context variables to pass to the template.

        Returns:
            The rendered prompt string.
        """
        try:
            template = self.env.get_template(template_name)
            return template.render(**kwargs)
        except Exception as e:
            logger.error(f"Failed to render prompt template '{template_name}': {e}", exc_info=True)
            return "" # Return empty string on failure