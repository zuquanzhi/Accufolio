"""Absolute-path entrypoint for desktop MCP client configurations."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.mcp.server import mcp
if __name__ == '__main__':
    mcp.run(transport='stdio')
