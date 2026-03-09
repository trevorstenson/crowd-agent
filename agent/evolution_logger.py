"""
Evolution Logger - Tracks and reports on the agent's behavioral evolution over time.

This module provides a lightweight way to capture significant changes in the agent's
capabilities, behaviors, and focus areas, making the evolution visible to humans.
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any


class EvolutionLogger:
    """
    Tracks agent evolution events and provides visibility into how the agent
    is changing over time.
    """
    
    def __init__(self, config_path: str = "agent/config.json"):
        self.config_path = Path(config_path)
        self.evolution_file = Path("agent/evolution_log.json")
        self.events = self._load_events()
        self.current_focus = self._get_current_focus()
        
    def _load_events(self) -> List[Dict[str, Any]]:
        """Load evolution events from disk."""
        if self.evolution_file.exists():
            try:
                with open(self.evolution_file, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return []
        return []
    
    def _save_events(self):
        """Save evolution events to disk."""
        try:
            with open(self.evolution_file, 'w') as f:
                json.dump(self.events, f, indent=2)
        except IOError as e:
            print(f"Warning: Could not save evolution log: {e}")
    
    def _get_current_focus(self) -> Optional[Dict[str, Any]]:
        """Get the current evolution focus from the roadmap."""
        try:
            roadmap_file = Path("agent/autonomous_roadmap.json")
            if roadmap_file.exists():
                with open(roadmap_file, 'r') as f:
                    roadmap = json.load(f)
                    for task in roadmap.get('tasks', []):
                        if task.get('status') == 'pending':
                            return {
                                'id': task['id'],
                                'title': task['title'],
                                'track': task['track'],
                                'priority': task['priority'],
                                'summary': task['summary']
                            }
        except (json.JSONDecodeError, IOError, KeyError):
            pass
        return None
    
    def log_mutation_selection(self, mutation_title: str, score: float, rationale: str):
        """Log when a new mutation is selected for implementation."""
        event = {
            'timestamp': datetime.now().isoformat(),
            'type': 'mutation_selected',
            'title': mutation_title,
            'score': score,
            'rationale': rationale[:200],  # Truncate for brevity
            'impact': 'medium'
        }
        self.events.append(event)
        self._save_events()
    
    def log_behavioral_change(self, change_type: str, description: str, 
                            before: Optional[str] = None, after: Optional[str] = None):
        """Log when agent behavior changes in a meaningful way."""
        event = {
            'timestamp': datetime.now().isoformat(),
            'type': 'behavioral_change',
            'change_type': change_type,
            'description': description,
            'before': before,
            'after': after,
            'impact': self._assess_impact(change_type)
        }
        self.events.append(event)
        self._save_events()
    
    def log_capability_growth(self, metric: str, old_value: Any, new_value: Any,
                             context: str = ""):
        """Log improvements in agent capabilities."""
        event = {
            'timestamp': datetime.now().isoformat(),
            'type': 'capability_growth',
            'metric': metric,
            'old_value': old_value,
            'new_value': new_value,
            'context': context,
            'impact': 'high' if self._is_significant_change(old_value, new_value) else 'low'
        }
        self.events.append(event)
        self._save_events()
    
    def log_tool_usage(self, tool_name: str, usage_count: int, success_rate: float):
        """Log tool usage patterns to track capability evolution."""
        event = {
            'timestamp': datetime.now().isoformat(),
            'type': 'tool_usage',
            'tool': tool_name,
            'usage_count': usage_count,
            'success_rate': success_rate,
            'impact': 'medium'
        }
        self.events.append(event)
        self._save_events()
    
    def log_error_recovery(self, error_type: str, recovery_method: str):
        """Log how the agent recovers from errors."""
        event = {
            'timestamp': datetime.now().isoformat(),
            'type': 'error_recovery',
            'error_type': error_type,
            'recovery_method': recovery_method,
            'impact': 'medium'
        }
        self.events.append(event)
        self._save_events()
    
    def _assess_impact(self, change_type: str) -> str:
        """Assess the impact level of a behavioral change."""
        high_impact_changes = [
            'planning_process', 'error_handling', 'tool_creation', 
            'workflow_change', 'architecture_change'
        ]
        return 'high' if change_type.lower() in high_impact_changes else 'medium'
    
    def _is_significant_change(self, old_value: Any, new_value: Any) -> bool:
        """Determine if a capability change is significant."""
        try:
            if isinstance(old_value, (int, float)) and isinstance(new_value, (int, float)):
                if old_value == 0:
                    return new_value > 0
                change_percentage = abs((new_value - old_value) / old_value) * 100
                return change_percentage > 20
        except (TypeError, ZeroDivisionError):
            pass
        return False
    
    def get_evolution_timeline(self, days: int = 30) -> List[Dict[str, Any]]:
        """Return evolution events for the specified time period."""
        cutoff_date = datetime.now() - timedelta(days=days)
        timeline = []
        
        for event in self.events:
            try:
                event_date = datetime.fromisoformat(event['timestamp'])
                if event_date >= cutoff_date:
                    timeline.append(event)
            except ValueError:
                continue
        
        # Sort by timestamp, most recent first
        timeline.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        return timeline
    
    def get_current_focus(self) -> Optional[Dict[str, Any]]:
        """Return current evolution focus with progress information."""
        return self.current_focus
    
    def get_capability_metrics(self) -> Dict[str, Any]:
        """Return current capability metrics."""
        try:
            # Try to load from memory.json if available
            memory_file = Path("agent/memory.json")
            if memory_file.exists():
                with open(memory_file, 'r') as f:
                    memory = json.load(f)
                    return {
                        'total_builds': memory.get('total_builds', 0),
                        'successful_builds': memory.get('successful_builds', 0),
                        'success_rate': memory.get('successful_builds', 0) / max(1, memory.get('total_builds', 0)),
                        'streak': memory.get('streak', 0),
                        'last_build_date': memory.get('last_build_date')
                    }
        except (json.JSONDecodeError, IOError, ZeroDivisionError):
            pass
        return {
            'total_builds': 0,
            'successful_builds': 0,
            'success_rate': 0,
            'streak': 0,
            'last_build_date': None
        }
    
    def get_evolution_summary(self, days: int = 7) -> Dict[str, Any]:
        """Get a summary of recent evolution activity."""
        timeline = self.get_evolution_timeline(days)
        
        # Count different types of events
        event_counts = {}
        impacts = {'high': 0, 'medium': 0, 'low': 0}
        
        for event in timeline:
            event_type = event.get('type', 'unknown')
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
            
            impact = event.get('impact', 'low')
            impacts[impact] = impacts.get(impact, 0) + 1
        
        return {
            'total_events': len(timeline),
            'event_breakdown': event_counts,
            'impact_breakdown': impacts,
            'current_focus': self.current_focus,
            'capability_metrics': self.get_capability_metrics()
        }
    
    def cleanup_old_events(self, days_to_keep: int = 180):
        """Remove old events to prevent the log from growing too large."""
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        
        old_count = len(self.events)
        self.events = [
            event for event in self.events
            if datetime.fromisoformat(event['timestamp']) >= cutoff_date
        ]
        
        if len(self.events) < old_count:
            print(f"Cleaned up {old_count - len(self.events)} old evolution events")
            self._save_events()