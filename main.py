import sys
import os

from src.aura.app.aura_app import AuraApp

# This is the magic! It adds the 'src' directory to the Python path.
# This way, Python knows where to find the 'aura' package.
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SRC_DIR, 'src'))



if __name__ == "__main__":
    """
    Main entry point for the AURA application.
    """
    aura_instance = AuraApp()
    aura_instance.run()