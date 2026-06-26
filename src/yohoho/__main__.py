"""Enables `python -m yohoho ...` (used by the LaunchAgent's ProgramArguments)."""
import sys
from yohoho.core.cli import main
sys.exit(main())
