from dotenv import load_dotenv

from src.aura.app.aura_app import AuraApp

if __name__ == "__main__":
    """
    Main entry point for the AURA application.
    """
    # Load environment variables from .env file
    load_dotenv()

    aura_instance = AuraApp()
    aura_instance.run()