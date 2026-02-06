"""
CrowdPilot Agent Tools

Minimal tool implementations for the agent. These are the tools the agent
can call during its build loop. The community can vote to add more tools.
"""

import os

# Base directory of the repository (set at runtime)
REPO_DIR = os.environ.get("GITHUB_WORKSPACE", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Track file changes made by the agent during a build
file_changes: dict[str, str] = {}


def read_file(path: str) -> str:
    """Read a file from the repository."""
    full_path = os.path.join(REPO_DIR, path)
    if not os.path.isfile(full_path):
        return f"Error: File not found: {path}"
    try:
        with open(full_path, "r") as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {e}"


def write_file(path: str, content: str) -> str:
    """Write or overwrite a file in the repository. Tracks changes for the PR."""
    full_path = os.path.join(REPO_DIR, path)
    try:
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w") as f:
            f.write(content)
        file_changes[path] = content
        return f"Successfully wrote {path}"
    except Exception as e:
        return f"Error writing file: {e}"


def list_files(directory: str = ".") -> str:
    """List files in a directory of the repository."""
    full_path = os.path.join(REPO_DIR, directory)
    if not os.path.isdir(full_path):
        return f"Error: Directory not found: {directory}"
    try:
        entries = []
        for entry in sorted(os.listdir(full_path)):
            entry_path = os.path.join(full_path, entry)
            if os.path.isdir(entry_path):
                entries.append(f"{entry}/")
            else:
                entries.append(entry)
        return "\n".join(entries) if entries else "(empty directory)"
    except Exception as e:
        return f"Error listing directory: {e}"


# Tool definitions for the Claude API
TOOL_DEFINITIONS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file in the repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file relative to the repository root."
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "description": "Write or overwrite a file in the repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file relative to the repository root."
                },
                "content": {
                    "type": "string",
                    "description": "The full content to write to the file."
                }
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "list_files",
        "description": "List files and directories in a given directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Path to the directory relative to the repository root. Defaults to root.",
                    "default": "."
                }
            },
            "required": []
        }
    }
]

# Map tool names to functions
TOOL_FUNCTIONS = {
    "read_file": read_file,
    "write_file": write_file,
    "list_files": list_files,
}


def execute_tool(name: str, inputs: dict) -> str:
    """Execute a tool by name with the given inputs."""
    if name not in TOOL_FUNCTIONS:
        return f"Error: Unknown tool: {name}"
    return TOOL_FUNCTIONS[name](**inputs)


def get_file_changes() -> dict[str, str]:
    """Return all file changes made during this build."""
    return dict(file_changes)


def reset_file_changes():
    """Reset tracked file changes (call at start of each build)."""
    file_changes.clear()
