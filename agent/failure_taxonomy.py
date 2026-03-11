"""
Failure taxonomy system for tracking and categorizing agent failures.

This module provides a structured way to track, categorize, and learn from
failures that occur during agent execution. It helps build operational
knowledge over time and makes failures more legible to maintainers.
"""

import json
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger(__name__)


class FailureCategory:
    """Defines a category of failures with metadata."""
    
    def __init__(self, code: str, name: str, description: str, severity: int, 
                 auto_detect_patterns: Optional[List[str]] = None):
        self.code = code
        self.name = name
        self.description = description
        self.severity = severity  # 1-5 scale (5 is most severe)
        self.auto_detect_patterns = auto_detect_patterns or []
        self.count = 0
        self.last_seen = None
        self.first_seen = None
        self.examples = []  # Store examples of this failure type
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'code': self.code,
            'name': self.name,
            'description': self.description,
            'severity': self.severity,
            'auto_detect_patterns': self.auto_detect_patterns,
            'count': self.count,
            'last_seen': self.last_seen,
            'first_seen': self.first_seen,
            'examples': self.examples[-5:]  # Keep last 5 examples
        }


class FailureEvent:
    """Represents a single failure occurrence."""
    
    def __init__(self, category_code: str, details: str, context: Optional[Dict[str, Any]] = None,
                 timestamp: Optional[datetime] = None):
        self.category_code = category_code
        self.details = details
        self.context = context or {}
        self.timestamp = timestamp or datetime.now()
        self.recovered = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'category_code': self.category_code,
            'details': self.details,
            'context': self.context,
            'timestamp': self.timestamp.isoformat(),
            'recovered': self.recovered
        }


class FailureTaxonomy:
    """Main failure tracking and categorization system."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.categories = self._initialize_categories()
        self.failures = []  # List of FailureEvent instances
        self.enabled = self.config.get('enabled', True)
        self.auto_categorize = self.config.get('auto_categorize', True)
        self.max_stored_failures = self.config.get('max_stored_failures', 1000)
    
    def _initialize_categories(self) -> Dict[str, FailureCategory]:
        """Initialize with common failure categories."""
        return {
            'TOOL_EXECUTION': FailureCategory(
                code='TOOL_EXECUTION',
                name='Tool Execution Failure',
                description='Failed to execute a tool or function call',
                severity=3,
                auto_detect_patterns=['Error executing tool', 'Tool failed', 'Function error', 
                                   'Error in tool', 'Tool execution failed']
            ),
            'PLANNING_TIMEOUT': FailureCategory(
                code='PLANNING_TIMEOUT',
                name='Planning Timeout',
                description='Agent exceeded time limit for planning phase',
                severity=2,
                auto_detect_patterns=['Planning timeout', 'Exceeded planning time', 
                                   'Planning took too long', 'Timeout during planning']
            ),
            'VALIDATION_ERROR': FailureCategory(
                code='VALIDATION_ERROR',
                name='Validation Error',
                description='Output validation failed',
                severity=4,
                auto_detect_patterns=['Validation failed', 'Invalid output', 'Schema error',
                                   'Output validation', 'Failed validation']
            ),
            'CONTEXT_OVERFLOW': FailureCategory(
                code='CONTEXT_OVERFLOW',
                name='Context Overflow',
                description='Context window exceeded or memory limit reached',
                severity=3,
                auto_detect_patterns=['Context too long', 'Token limit', 'Memory overflow',
                                   'Context overflow', 'Max tokens exceeded']
            ),
            'RETRY_EXHAUSTED': FailureCategory(
                code='RETRY_EXHAUSTED',
                name='Retry Exhausted',
                description='All retry attempts failed',
                severity=4,
                auto_detect_patterns=['Max retries', 'Retry failed', 'Attempts exhausted',
                                   'All retries failed', 'Retry limit reached']
            ),
            'API_ERROR': FailureCategory(
                code='API_ERROR',
                name='API Error',
                description='External API call failed',
                severity=3,
                auto_detect_patterns=['API error', 'API call failed', 'External service error',
                                   'Provider error', 'Service unavailable']
            ),
            'AUTHENTICATION_ERROR': FailureCategory(
                code='AUTHENTICATION_ERROR',
                name='Authentication Error',
                description='Authentication or authorization failed',
                severity=4,
                auto_detect_patterns=['Unauthorized', 'Authentication failed', 'Access denied',
                                   'Invalid credentials', 'Permission denied']
            ),
            'PARSE_ERROR': FailureCategory(
                code='PARSE_ERROR',
                name='Parse Error',
                description='Failed to parse or interpret content',
                severity=2,
                auto_detect_patterns=['Parse error', 'Failed to parse', 'Invalid format',
                                   'Could not parse', 'Syntax error']
            ),
            'FILE_OPERATION_ERROR': FailureCategory(
                code='FILE_OPERATION_ERROR',
                name='File Operation Error',
                description='File system operation failed',
                severity=3,
                auto_detect_patterns=['File not found', 'Permission denied', 
                                   'Failed to read', 'Failed to write', 'IO error']
            ),
            'UNKNOWN_ERROR': FailureCategory(
                code='UNKNOWN_ERROR',
                name='Unknown Error',
                description='Unclassified or unexpected error',
                severity=2,
                auto_detect_patterns=['Unknown error', 'Unexpected error', 'Unclassified']
            )
        }
    
    def categorize_failure(self, error_message: str, context: Optional[Dict[str, Any]] = None) -> str:
        """Categorize a failure based on error message and context."""
        if not self.auto_categorize or not error_message:
            return 'UNKNOWN_ERROR'
        
        error_lower = error_message.lower()
        
        # Try pattern matching first
        for category in self.categories.values():
            for pattern in category.auto_detect_patterns:
                if pattern.lower() in error_lower:
                    logger.info(f"Auto-categorized failure as {category.code}")
                    return category.code
        
        # If no pattern matches, try to infer from context
        if context:
            if context.get('function') or context.get('tool'):
                return 'TOOL_EXECUTION'
            if 'timeout' in str(context).lower():
                return 'PLANNING_TIMEOUT'
            if 'context' in str(context).lower() or 'token' in str(context).lower():
                return 'CONTEXT_OVERFLOW'
        
        return 'UNKNOWN_ERROR'
    
    def record_failure(self, category_code: str, details: str, context: Optional[Dict[str, Any]] = None,
                      timestamp: Optional[datetime] = None) -> FailureEvent:
        """Record a failure occurrence."""
        if not self.enabled:
            return FailureEvent('DISABLED', 'Failure tracking disabled', context, timestamp)
        
        # Auto-categorize if needed
        if category_code == 'UNKNOWN_ERROR' and self.auto_categorize:
            category_code = self.categorize_failure(details, context)
        
        # Create failure event
        failure = FailureEvent(category_code, details, context, timestamp)
        self.failures.append(failure)
        
        # Update category statistics
        if category_code in self.categories:
            category = self.categories[category_code]
            category.count += 1
            category.last_seen = datetime.now().isoformat()
            if category.first_seen is None:
                category.first_seen = category.last_seen
            
            # Store example (keep last 5)
            example = {
                'details': details,
                'context': context,
                'timestamp': failure.timestamp.isoformat()
            }
            category.examples.append(example)
            if len(category.examples) > 5:
                category.examples.pop(0)
        
        logger.info(f"Recorded failure: {category_code} - {details[:100]}...")
        
        # Maintain storage limits
        if len(self.failures) > self.max_stored_failures:
            self.failures = self.failures[-self.max_stored_failures:]
        
        return failure
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get failure statistics for reporting."""
        total_failures = len(self.failures)
        total_categories = sum(1 for cat in self.categories.values() if cat.count > 0)
        
        category_stats = {}
        for category in self.categories.values():
            if category.count > 0:
                category_stats[category.code] = {
                    'name': category.name,
                    'count': category.count,
                    'severity': category.severity,
                    'last_seen': category.last_seen,
                    'rate': category.count / max(total_failures, 1) * 100
                }
        
        return {
            'total_failures': total_failures,
            'total_categories': total_categories,
            'categories_with_failures': total_categories,
            'category_stats': category_stats,
            'recent_failures': [f.to_dict() for f in self.failures[-10:]],  # Last 10
            'top_categories': sorted(
                [(cat.code, cat.count) for cat in self.categories.values() if cat.count > 0],
                key=lambda x: x[1],
                reverse=True
            )[:5]
        }
    
    def get_human_readable_report(self) -> str:
        """Generate a human-readable failure report."""
        stats = self.get_statistics()
        
        if stats['total_failures'] == 0:
            return "No failures recorded yet."
        
        lines = [
            "# Failure Analysis Report",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            f"**Total Failures:** {stats['total_failures']}",
            f"**Categories Affected:** {stats['total_categories']}",
            "",
            "## Failure Categories",
            ""
        ]
        
        # Sort categories by count (descending)
        sorted_categories = sorted(
            stats['category_stats'].items(),
            key=lambda x: x[1]['count'],
            reverse=True
        )
        
        for code, info in sorted_categories:
            severity_indicator = "★" * info['severity']
            lines.append(f"### {info['name']} ({code})")
            lines.append(f"- **Severity:** {info['severity']} {severity_indicator}")
            lines.append(f"- **Count:** {info['count']}")
            lines.append(f"- **Rate:** {info['rate']:.1f}%")
            lines.append(f"- **Last Seen:** {info['last_seen']}")
            lines.append("")
        
        # Add recent failures
        if stats['recent_failures']:
            lines.extend([
                "## Recent Failures",
                ""
            ])
            for failure in stats['recent_failures'][-5:]:  # Show last 5
                lines.extend([
                    f"### {failure['category_code']} - {failure['timestamp']}",
                    f"**Details:** {failure['details']}",
                    ""
                ])
        
        return "\n".join(lines)
    
    def export_taxonomy(self) -> Dict[str, Any]:
        """Export taxonomy for documentation or persistence."""
        return {
            'config': self.config,
            'categories': {code: cat.to_dict() for code, cat in self.categories.items()},
            'statistics': self.get_statistics(),
            'total_failures': len(self.failures)
        }
    
    def save_to_file(self, filepath: str):
        """Save taxonomy data to a file."""
        data = self.export_taxonomy()
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info(f"Failure taxonomy saved to {filepath}")
    
    def load_from_file(self, filepath: str):
        """Load taxonomy data from a file."""
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
            
            # Restore categories
            for code, cat_data in data.get('categories', {}).items():
                if code in self.categories:
                    cat = self.categories[code]
                    cat.count = cat_data.get('count', 0)
                    cat.first_seen = cat_data.get('first_seen')
                    cat.last_seen = cat_data.get('last_seen')
                    cat.examples = cat_data.get('examples', [])
            
            logger.info(f"Failure taxonomy loaded from {filepath}")
        except FileNotFoundError:
            logger.warning(f"Failure taxonomy file not found: {filepath}")
        except Exception as e:
            logger.error(f"Error loading failure taxonomy: {e}")
    
    def add_category(self, category: FailureCategory):
        """Add a new failure category."""
        self.categories[category.code] = category
        logger.info(f"Added new failure category: {category.code}")
    
    def get_category(self, code: str) -> Optional[FailureCategory]:
        """Get a specific failure category."""
        return self.categories.get(code)


# Convenience functions for integration
def create_failure_taxonomy(config: Optional[Dict[str, Any]] = None) -> FailureTaxonomy:
    """Create a new failure taxonomy instance."""
    return FailureTaxonomy(config)


def format_failure_summary(taxonomy: FailureTaxonomy, max_categories: int = 3) -> str:
    """Format a brief failure summary for reporting."""
    stats = taxonomy.get_statistics()
    if stats['total_failures'] == 0:
        return "No failures recorded."
    
    total = stats['total_failures']
    top_categories = stats['top_categories']
    
    summary = f"Recorded {total} failures"
    if top_categories:
        top_3 = top_categories[:max_categories]
        category_summary = ", ".join(f"{code}({count})" for code, count in top_3)
        summary += f" - Top: {category_summary}"
    
    return summary