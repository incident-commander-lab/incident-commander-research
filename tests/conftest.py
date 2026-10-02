"""Keep the test suite offline: a developer .env may set IC_USE_LLM=1."""
import os

os.environ["IC_LOG_READER"] = "regex"
