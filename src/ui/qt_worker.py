from PySide6.QtCore import QRunnable, Slot
import logging

logger = logging.getLogger(__name__)

class Worker(QRunnable):
    """
    Worker thread for running a function in the background.
    """
    def __init__(self, fn, *args, **kwargs):
        super(Worker, self).__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

    @Slot()
    def run(self):
        """
        Your code goes in this function
        """
        try:
            self.fn(*self.args, **self.kwargs)
        except Exception as e:
            logger.error(f"Error in worker thread: {e}", exc_info=True)
