"""
Crowd Agent Tools

Minimal tool implementations for the agent. These are the tools the agent
can call during its build loop. The community can vote to add more tools.
"""

import os
import re

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


def search_files(pattern: str, case_sensitive: bool = False, max_results: int = 20) -> str:
    """
    Search for text patterns across the repository.
    
    Args:
        pattern: Text or regex pattern to search for
        case_sensitive: Whether to match case (default: False)
        max_results: Maximum number of results to return (default: 20)
    
    Returns:
        JSON string with search results including matches, total count, and metadata
    """
    import json
    
    # Compile regex pattern
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return json.dumps({
            'error': f'Invalid regex pattern: {str(e)}',
            'matches': [],
            'total_matches': 0
        })
    
    # Define exclusions
    skip_dirs = {'.git', '__pycache__', '.venv', 'node_modules', '.pytest_cache', '.github'}
    skip_extensions = {'.pyc', '.o', '.so', '.bin', '.jpg', '.png', '.gif', '.pdf', '.pyc', '.class'}
    
    matches = []
    total_matches = 0
    
    # Walk repository
    try:
        for root, dirs, files in os.walk(REPO_DIR):
            # Prune excluded directories
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            
            for file in files:
                if any(file.endswith(ext) for ext in skip_extensions):
                    continue
                
                filepath = os.path.join(root, file)
                rel_path = os.path.relpath(filepath, REPO_DIR)
                
                try:
                    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                        for line_num, line in enumerate(f, 1):
                            if regex.search(line):
                                total_matches += 1
                                if len(matches) < max_results:
                                    matches.append({
                                        'file': rel_path,
                                        'line_num': line_num,
                                        'snippet': line.rstrip()
                                    })
                except (IOError, OSError):
                    # Skip files we can't read
                    continue
    except Exception as e:
        return json.dumps({
            'error': f'Search failed: {str(e)}',
            'matches': matches,
            'total_matches': total_matches
        })
    
    return json.dumps({
        'matches': matches,
        'total_matches': total_matches,
        'search_pattern': pattern,
        'case_sensitive': case_sensitive,
        'max_results': max_results,
        'results_truncated': total_matches > max_results
    })


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
    },
    {
        "name": "search_files",
        "description": "Search for text patterns across the repository. Useful for discovering relevant code when you don't know the exact file location.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Text or regex pattern to search for. Supports regex syntax."
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Whether to match case. Default is false (case-insensitive).",
                    "default": False
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return. Default is 20.",
                    "default": 20
                }
            },
            "required": ["pattern"]
        }
    }
]

# Map tool names to functions
TOOL_FUNCTIONS = {
    "read_file": read_file,
    "write_file": write_file,
    "list_files": list_files,
    "search_files": search_files,
}


def execute_tool(name: str, inputs: dict) -> str:
    """Execute a tool by name with the given inputs."""
    if name not in TOOL_FUNCTIONS:
        return f"Error: Unknown tool: {name}"
    # Validate and filter inputs against schema
    schema = next(t for t in TOOL_DEFINITIONS if t["name"] == name)
    valid_keys = set(schema["input_schema"].get("properties", {}).keys())
    required_keys = set(schema["input_schema"].get("required", []))
    filtered = {k: v for k, v in inputs.items() if k in valid_keys}
    missing = required_keys - set(filtered.keys())
    if missing:
        return f"Error: Missing required parameters: {', '.join(sorted(missing))}"
    try:
        return TOOL_FUNCTIONS[name](**filtered)
    except Exception as e:
        return f"Error executing {name}: {e}"


def get_file_changes() -> dict[str, str]:
    """Return all file changes made during this build."""
    return dict(file_changes)


def reset_file_changes():
    """Reset tracked file changes (call at start of each build)."""
    file_changes.clear()
