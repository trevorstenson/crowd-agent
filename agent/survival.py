"""
Survival mechanisms for constraint pressure and workflow limits.

This module provides adaptive strategies for handling:
- Context window limitations
- Request rate limits and timeouts
- Workflow timeouts
- Memory/CPU constraints
"""

import json
import time
import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class ConstraintState:
    """Current state of resource constraints."""
    
    context_tokens: int = 0
    context_limit: int = 4000
    requests_made: int = 0
    request_budget: int = 100
    elapsed_time: float = 0.0
    timeout_threshold: float = 300.0
    memory_usage_mb: float = 0.0
    memory_limit_mb: float = 1024.0
    
    @property
    def context_pressure(self) -> float:
        """Context usage as percentage of limit."""
        return self.context_tokens / max(self.context_limit, 1)
    
    @property
    def request_pressure(self) -> float:
        """Request usage as percentage of budget."""
        return self.requests_made / max(self.request_budget, 1)
    
    @property  
    def time_pressure(self) -> float:
        """Time usage as percentage of timeout."""
        return self.elapsed_time / max(self.timeout_threshold, 1.0)
    
    @property
    def memory_pressure(self) -> float:
        """Memory usage as percentage of limit."""
        return self.memory_usage_mb / max(self.memory_limit_mb, 1.0)
    
    def is_under_pressure(self, threshold: float = 0.8) -> bool:
        """Check if any constraint is approaching its limit."""
        return (
            self.context_pressure > threshold or
            self.request_pressure > threshold or
            self.time_pressure > threshold or
            self.memory_pressure > threshold
        )


@dataclass
class SurvivalMode:
    """Represents a survival mode with its triggering conditions and strategies."""
    
    name: str
    trigger_pressure: float
    trigger_constraint: str  # 'context', 'requests', 'time', 'memory'
    strategies: List[str]  # List of available strategies
    priority: int = 100


class SurvivalManager:
    """
    Manages survival strategies and constraint adaptation.
    
    Implements a three-tier survival strategy:
    1. Immediate Response (0-5s): Context compression, rate limiting
    2. Mid-term Adaptation (5-60s): Task decomposition, memory prioritization  
    3. Long-term Evolution (60s+): Progressive summarization, knowledge indexing
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config.get('survival', {})
        self.context_limit = self.config.get('context_limit', 4000)
        self.request_budget = self.config.get('request_budget', 100)
        self.timeout_threshold = self.config.get('timeout_threshold', 300)
        self.compression_threshold = self.config.get('compression_threshold', 0.8)
        self.recovery_strategies = self.config.get('recovery_strategies', ['compress', 'decompose', 'summarize'])
        
        self.survival_modes = self._initialize_survival_modes()
        self.current_mode = None
        self.constraints = ConstraintState(
            context_limit=self.context_limit,
            request_budget=self.request_budget,
            timeout_threshold=self.timeout_threshold
        )
        
        # Track request timing for rate limiting
        self.request_times: List[float] = []
        self.last_checkpoint_time = time.time()
    
    def _initialize_survival_modes(self) -> List[SurvivalMode]:
        """Initialize available survival modes."""
        return [
            SurvivalMode(
                name="conservation",
                trigger_pressure=0.7,
                trigger_constraint="any",
                strategies=["compress_context", "reduce_detail", "batch_requests"],
                priority=90
            ),
            SurvivalMode(
                name="compression",
                trigger_pressure=0.8,
                trigger_constraint="context",
                strategies=["compress_context", "summarize_content", "prioritize_info"],
                priority=80
            ),
            SurvivalMode(
                name="decomposition", 
                trigger_pressure=0.85,
                trigger_constraint="any",
                strategies=["decompose_task", "reduce_scope", "create_milestones"],
                priority=70
            ),
            SurvivalMode(
                name="emergency",
                trigger_pressure=0.95,
                trigger_constraint="any",
                strategies=["minimal_response", "checkpoint_and_exit", "fail_gracefully"],
                priority=50
            )
        ]
    
    def update_constraints(self, **kwargs):
        """Update constraint state with new measurements."""
        for key, value in kwargs.items():
            if hasattr(self.constraints, key):
                setattr(self.constraints, key, value)
        
        # Check if we need to activate survival mode
        self._evaluate_survival_mode()
    
    def check_rate_limit(self, min_interval: float = 1.0) -> bool:
        """Check if we should delay to respect rate limits."""
        now = time.time()
        
        # Remove old request times outside the window
        self.request_times = [
            req_time for req_time in self.request_times
            if now - req_time < 60  # Keep last 60 seconds
        ]
        
        # Check if we're approaching rate limits
        if len(self.request_times) >= self.request_budget * 0.8:
            # Exponential backoff
            oldest_request = min(self.request_times) if self.request_times else now
            time_since_oldest = now - oldest_request
            
            if time_since_oldest < 60:
                wait_time = min_interval * (2 ** len([t for t in self.request_times if now - t < 10]))
                logger.info(f"Rate limiting: waiting {wait_time:.1f}s")
                time.sleep(wait_time)
                return True
        
        self.request_times.append(now)
        return False
    
    def _evaluate_survival_mode(self):
        """Evaluate if we should enter a different survival mode."""
        current_pressure = max(
            self.constraints.context_pressure,
            self.constraints.request_pressure,
            self.constraints.time_pressure,
            self.constraints.memory_pressure
        )
        
        # Find the highest priority mode that should trigger
        triggered_modes = []
        for mode in self.survival_modes:
            if current_pressure >= mode.trigger_pressure:
                triggered_modes.append(mode)
        
        if triggered_modes:
            # Sort by priority (higher priority first)
            triggered_modes.sort(key=lambda m: m.priority, reverse=True)
            new_mode = triggered_modes[0]
            
            if self.current_mode != new_mode:
                logger.info(f"Entering survival mode: {new_mode.name} (pressure: {current_pressure:.2f})")
                self.current_mode = new_mode
    
    def adapt_task(self, task: str) -> str:
        """Adapt a task description based on current constraints."""
        if not self.current_mode:
            return task
        
        if self.current_mode.name == "decomposition":
            return f"Break this task into smaller subtasks: {task}"
        elif self.current_mode.name == "compression":
            return f"Focus on the essential parts: {task}"
        elif self.current_mode.name == "emergency":
            return f"Create minimal viable solution: {task}"
        
        return task
    
    def compress_context(self, context: str, target_size: int) -> str:
        """Intelligently compress context to fit within limits."""
        if len(context) <= target_size:
            return context
        
        # Simple compression strategies
        lines = context.split('\n')
        if len(lines) > 20:
            # Keep first and last portions, summarize middle
            head_lines = lines[:5]
            tail_lines = lines[-5:]
            
            # Summarize middle section
            middle_lines = lines[5:-5]
            if middle_lines:
                middle_summary = f"[... {len(middle_lines)} lines summarized ...]"
                compressed_lines = head_lines + [middle_summary] + tail_lines
                compressed = '\n'.join(compressed_lines)
                
                if len(compressed) <= target_size:
                    return compressed
        
        # If still too large, truncate with notice
        if len(context) > target_size:
            truncated = context[:target_size-100]
            return f"{truncated}\n[... context truncated due to size limits ...]"
        
        return context
    
    def prioritize_content(self, contents: List[Dict[str, Any]], max_tokens: int) -> List[Dict[str, Any]]:
        """Prioritize content items based on importance scores."""
        # Simple prioritization: prioritize by relevance and recency
        scored_contents = []
        
        for item in contents:
            # Score based on content type and metadata
            score = 1.0
            
            if 'error' in item.get('content', '').lower():
                score += 2.0
            if 'important' in item.get('metadata', '').lower():
                score += 1.0
            if item.get('type') == 'config':
                score += 0.5
            
            scored_contents.append((score, item))
        
        # Sort by score (descending)
        scored_contents.sort(key=lambda x: x[0], reverse=True)
        
        # Select items that fit within token limit
        selected = []
        total_tokens = 0
        
        # Simple token estimation
        for score, item in scored_contents:
            item_tokens = len(str(item)) // 4  # Rough estimate: 4 chars per token
            
            if total_tokens + item_tokens <= max_tokens:
                selected.append(item)
                total_tokens += item_tokens
            else:
                break
        
        return selected
    
    def decompose_task(self, task: str, complexity_threshold: int = 500) -> List[str]:
        """Break a complex task into smaller subtasks."""
        if len(task) <= complexity_threshold:
            return [task]
        
        # Simple heuristic decomposition
        subtasks = []
        
        # Split by natural breakpoints
        sentences = task.split('. ')
        if len(sentences) > 3:
            # Group sentences into subtasks
            for i in range(0, len(sentences), 2):
                subtask = '. '.join(sentences[i:i+2])
                if subtask:
                    subtasks.append(subtask)
        else:
            # Split by components
            components = task.split('\n')
            if len(components) > 1:
                for component in components:
                    if component.strip():
                        subtasks.append(component.strip())
        
        return subtasks if subtasks else [task]
    
    def create_survival_checkpoint(self, task_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
        """Create a lightweight checkpoint for survival recovery."""
        return {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'task_id': task_id,
            'constraints': asdict(self.constraints),
            'current_mode': self.current_mode.name if self.current_mode else None,
            'state': state,
            'version': 'survival_v1'
        }
    
    def get_survival_metrics(self) -> Dict[str, float]:
        """Get current survival metrics for monitoring."""
        return {
            'context_pressure': self.constraints.context_pressure,
            'request_pressure': self.constraints.request_pressure,
            'time_pressure': self.constraints.time_pressure,
            'memory_pressure': self.constraints.memory_pressure,
            'current_mode': self.current_mode.name if self.current_mode else 'normal',
            'requests_remaining': max(0, self.request_budget - self.constraints.requests_made),
            'time_remaining': max(0.0, self.timeout_threshold - self.constraints.elapsed_time)
        }
    
    def handle_constraint_violation(self, violation: Exception, task: str) -> str:
        """Handle constraint violations gracefully."""
        logger.error(f"Constraint violation: {violation}")
        
        error_msg = str(violation).lower()
        
        if 'timeout' in error_msg or 'time' in error_msg:
            self.update_constraints(time_pressure=1.0)
            return f"Task aborted due to timeout constraint. Completed: {task}"
        
        elif 'context' in error_msg or 'token' in error_msg:
            self.update_constraints(context_tokens=self.context_limit)
            return f"Task truncated due to context limit. Attempted: {task}"
        
        elif 'rate' in error_msg or 'request' in error_msg:
            self.update_constraints(requests_made=self.request_budget)
            return f"Task limited due to request budget. Attempted: {task}"
        
        else:
            return f"Task failed due to constraint: {violation}"
    
    def check_feasibility(self, task: str) -> bool:
        """Check if a task is feasible within current constraints."""
        if self.constraints.is_under_pressure(0.9):
            return False
        
        # Simple heuristic based on task length
        task_tokens = len(task) // 4  # Rough estimate
        future_tokens = self.constraints.context_tokens + task_tokens
        
        return future_tokens < self.constraints.context_limit
    
    def should_create_checkpoint(self) -> bool:
        """Determine if we should create a checkpoint for recovery."""
        # Create checkpoint when under significant pressure
        return (
            self.constraints.context_pressure > 0.7 or
            self.constraints.time_pressure > 0.6 or
            self.constraints.request_pressure > 0.7
        )