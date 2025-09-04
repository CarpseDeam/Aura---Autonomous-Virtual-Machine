import os
import logging
from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger(__name__)

class PromptManager:
    """
    Manages loading and rendering of Jinja2 prompts from the filesystem.
    """
    def __init__(self):
        """
        Initializes the PromptManager and sets up the Jinja2 environment.
        It assumes templates are located in a 'templates' subdirectory
        relative to this file's location.
        """
        # The base directory for templates is <current_file_path>/templates
        templates_dir = os.path.join(os.path.dirname(__file__), 'templates')

        if not os.path.isdir(templates_dir):
            # Let's try to create it
            try:
                os.makedirs(templates_dir)
                logger.info(f"Created templates directory at {templates_dir}")
            except OSError as e:
                logger.warning(f"Could not create templates directory at {templates_dir}: {e}. Prompt rendering will fail.")
                self.env = None
                return


        self.env = Environment(
            loader=FileSystemLoader(templates_dir),
            autoescape=select_autoescape(['html', 'xml', 'jinja2'])
        )
        logger.info(f"PromptManager initialized. Loading templates from: {templates_dir}")

    def render(self, template_name: str, **kwargs) -> str:
        """
        Renders a specified Jinja2 template with the given context.

        Args:
            template_name: The filename of the template to render.
            **kwargs: The context variables to pass to the template.

        Returns:
            The rendered prompt as a string, or an empty string if rendering fails.
        """
        if not self.env:
            logger.error("Jinja2 environment not initialized. Cannot render prompt.")
            return ""
        try:
            template = self.env.get_template(template_name)
            return template.render(**kwargs)
        except Exception as e:
            logger.error(f"Failed to render prompt template '{template_name}': {e}", exc_info=True)
            return ""
