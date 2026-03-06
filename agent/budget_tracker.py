"""
Budget tracker for monitoring resource usage and preventing overconsumption.

Tracks various resource budgets and provides early warning when approaching limits.
"""

import time
import logging
import json
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


@dataclass
class BudgetAllocation:
    """Represents a budget allocation for a specific resource."""
    
    name: str
    allocated: float
    used: float = 0.0
    unit: str = ""
    reset_interval: Optional[int] = None  # seconds
    last_reset: Optional[float] = None
    
    @property
    def remaining(self) -> float:
        return max(0.0, self.allocated - self.used)
    
    @property
    def usage_ratio(self) -> float:
        return self.used / max(self.allocated, 1.0)
    
    @property
    def is_exhausted(self) -> bool:
        return self.used >= self.allocated
    
    @property
    def is_near_limit(self, threshold: float = 0.8) -> bool:
        return self.usage_ratio >= threshold
    
    def consume(self, amount: float) -> bool:
        """Consume budget and return True if successful, False if insufficient."""
        if self.used + amount <= self.allocated:
            self.used += amount
            return True
        return False
    
    def reset_if_needed(self):
        """Reset budget if reset interval has passed."""
        if self.reset_interval and self.last_reset:
            now = time.time()
            if now - self.last_reset >= self.reset_interval:
                self.used = 0.0
                self.last_reset = now


class BudgetTracker:
    """
    Tracks multiple resource budgets and provides early warning system.
    
    Resources tracked:
    - API requests (rate limiting)
    - Context tokens (memory limits)
    - Processing time (timeout limits)
    - File operations (I/O limits)
    - Memory usage (system limits)
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.budgets: Dict[str, BudgetAllocation] = {}
        self.warnings_issued: set = set()
        self.callbacks: List[Callable] = []
        
        self._initialize_budgets()
        self.start_time = time.time()
        
    def _initialize_budgets(self):
        """Initialize all budget allocations from config."""
        budget_config = self.config.get('budgets', {})
        
        # API Request budget
        if budget_config.get('requests', {}).get('enabled', True):
            self.budgets['requests'] = BudgetAllocation(
                name='API Requests',
                allocated=budget_config.get('requests', {}).get('limit', 100),
                unit='requests',
                reset_interval=budget_config.get('requests', {}).get('reset_interval', 3600)  # 1 hour
            )
        
        # Context token budget
        if budget_config.get('context', {}).get('enabled', True):
            self.budgets['context'] = BudgetAllocation(
                name='Context Tokens',
                allocated=budget_config.get('context', {}).get('limit', 4000),
                unit='tokens'
            )
        
        # Processing time budget
        if budget_config.get('time', {}).get('enabled', True):
            self.budgets['time'] = BudgetAllocation(
                name='Processing Time',
                allocated=budget_config.get('time', {}).get('limit', 300),  # 5 minutes
                unit='seconds'
            )
        
        # File operations budget
        if budget_config.get('file_ops', {}).get('enabled', True):
            self.budgets['file_ops'] = BudgetAllocation(
                name='File Operations',
                allocated=budget_config.get('file_ops', {}).get('limit', 50),
                unit='operations'
            )
        
        # Network requests budget
        if budget_config.get('network', {}).get('enabled', True):
            self.budgets['network'] = BudgetAllocation(
                name='Network Requests',
                allocated=budget_config.get('network', {}).get('limit', 20),
                unit='requests'
            )
    
    def register_callback(self, callback: Callable):
        """Register a callback to be called when budgets are exceeded."""
        self.callbacks.append(callback)
    
    def consume_budget(self, budget_type: str, amount: float) -> bool:
        """Consume a specified amount of budget."""
        if budget_type not in self.budgets:
            logger.warning(f"Unknown budget type: {budget_type}")
            return True
        
        budget = self.budgets[budget_type]
        success = budget.consume(amount)
        
        if not success:
            logger.error(f"Budget exhausted for {budget_type}: {amount} requested, {budget.remaining} remaining")
            
            # Trigger callbacks
            for callback in self.callbacks:
                try:
                    callback(self, budget_type, 'exhausted', amount, budget)
                except Exception as e:
                    logger.error(f"Budget callback failed: {e}")
            
            return False
        
        # Check for warning thresholds
        self._check_warning_thresholds(budget_type)
        
        return True
    
    def _check_warning_thresholds(self, budget_type: str):
        """Check if budget usage has crossed warning thresholds."""
        budget = self.budgets[budget_type]
        
        # Check different warning levels
        warning_levels = [
            (0.9, 'critical'),
            (0.7, 'warning'),
            (0.5, 'caution')
        ]
        
        for threshold, level in warning_levels:
            warning_key = f"{budget_type}_{level}"
            
            if budget.usage_ratio >= threshold and warning_key not in self.warnings_issued:
                logger.warning(f"Budget warning - {budget_type}: {budget.usage_ratio:.1%} used ({level})")
                self.warnings_issued.add(warning_key)
                
                # Trigger callbacks
                for callback in self.callbacks:
                    try:
                        callback(self, budget_type, level, threshold, budget)
                    except Exception as e:
                        logger.error(f"Budget callback failed: {e}")
    
    def check_budget(self, budget_type: str) -> Optional[BudgetAllocation]:
        """Get current state of a specific budget."""
        return self.budgets.get(budget_type)
    
    def get_remaining_budget(self, budget_type: str) -> float:
        """Get remaining budget for a specific type."""
        budget = self.budgets.get(budget_type)
        return budget.remaining if budget else 0.0
    
    def get_budget_summary(self) -> Dict[str, Any]:
        """Get summary of all budgets."""
        summary = {
            'status': 'healthy',
            'total_budgets': len(self.budgets),
            'exhausted': [],
            'warning': [],
            'caution': []
        }
        
        for budget_type, budget in self.budgets.items():
            budget_type, budget in self.budgets.items():
            if budget.is_exhausted:
                summary['exhausted'].append(budget_type)
                summary['status'] = 'critical'
            elif budget.is_near_limit(0.9):
                summary['warning'].append(budget_type)
                if summary['status'] == 'healthy':
                    summary['status'] = 'warning'
            elif budget.is_near_limit(0.6):
                summary['caution'].append(budget_type)
        
        return summary
    
    def is_budget_exhausted(self, budget_type: str) -> bool:
        """Check if a specific budget is exhausted."""
        budget = self.budgets.get(budget_type)
        return budget.is_exhausted if budget else False
    
    def is_any_budget_critical(self, threshold: float = 0.9) -> bool:
        """Check if any budget is approaching critical levels."""
        for budget in self.budgets.values():
            if budget.is_near_limit(threshold):
                return True
        return False
    
    def get_affordable_operations(self, operation_types: List[str]) -> List[str]:
        """Get list of operations that can be performed within current budgets."""
        affordable = []
        
        for op_type in operation_types:
            # Map operation types to budget consumption
            costs = {
                'llm_call': {'requests': 1, 'context': 500, 'time': 10},
                'file_read': {'file_ops': 1, 'time': 1},
                'file_write': {'file_ops': 1, 'time': 2},
                'search': {'requests': 1, 'file_ops': 1, 'time': 5}
            }
            
            op_costs = costs.get(op_type, {})
            can_afford = True
            
            for budget_type, cost in op_costs.items():
                budget = self.budgets.get(budget_type)
                if budget and not budget.consume(0):  # Check only, don't consume
                    can_afford = False
                    break
            
            if can_afford:
                affordable.append(op_type)
        
        return affordable
    
    def estimate_operation_cost(self, operation_type: str, **kwargs) -> Dict[str, float]:
        """Estimate the cost of an operation."""
        base_costs = {
            'llm_call': {'requests': 1, 'context': 500, 'time': 10},
            'file_read': {'file_ops': 1, 'time': 1},
            'file_write': {'file_ops': 1, 'time': 2, 'context': 100},
            'search': {'requests': 1, 'file_ops': 1, 'time': 5},
            'compress': {'context': 200, 'time': 5},
            'checkpoint': {'file_ops': 2, 'time': 3}
        }
        
        costs = base_costs.get(operation_type, {}).copy()
        
        # Adjust based on parameters
        if operation_type == 'llm_call':
            token_count = kwargs.get('tokens', 500)
            costs['context'] = token_count
            costs['time'] = max(5, token_count // 100)
        
        elif operation_type == 'file_write' and 'size' in kwargs:
            size_kb = kwargs['size'] // 1024
            costs['file_ops'] += size_kb // 10
            costs['time'] += size_kb // 5
        
        return costs
    
    def plan_with_budget(self, tasks: List[str]) -> Tuple[List[str], List[str]]:
        """Plan which tasks can be completed within budget constraints."""
        feasible = []
        deferred = []
        
        for task in tasks:
            # Estimate task requirements
            task_cost = self._estimate_task_cost(task)
            
            can_complete = True
            for budget_type, cost in task_cost.items():
                budget = self.budgets.get(budget_type)
                if budget and not budget.consume(0):  # Check only
                    can_complete = False
                    break
            
            if can_complete:
                feasible.append(task)
            else:
                deferred.append(task)
        
        return feasible, deferred
    
    def _estimate_task_cost(self, task: str) -> Dict[str, float]:
        """Estimate the resource cost of a task."""
        task_lower = task.lower()
        
        # Estimate tokens based on task description length
        base_tokens = len(task) // 4
        
        # Estimate time based on complexity indicators
        time_estimate = 10  # Base 10 seconds
        if 'search' in task_lower or 'find' in task_lower:
            time_estimate += 15
        if 'edit' in task_lower or 'modify' in task_lower:
            time_estimate += 20
        if 'create' in task_lower or 'write' in task_lower:
            time_estimate += 30
        
        # Estimate requests based on complexity
        request_estimate = 2  # Base 2 requests
        if 'analyze' in task_lower or 'review' in task_lower:
            request_estimate += 1
        if 'large' in task_lower or 'multiple' in task_lower:
            request_estimate += 1
        
        return {
            'context': base_tokens + 200,  # Extra for internal processing
            'time': time_estimate,
            'requests': request_estimate
        }
    
    def record_usage(self, usage_type: str, amount: float, metadata: Optional[Dict] = None):
        """Record resource usage for later analysis."""
        entry = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'type': usage_type,
            'amount': amount,
            'metadata': metadata or {}
        }
        
        logger.info(f"Usage recorded: {usage_type}={amount}")
        
        # Could store this in a file for later analysis
        if self.config.get('enable_usage_logging', False):
            try:
                usage_file = self.config.get('usage_file', 'usage_log.jsonl')
                with open(usage_file, 'a') as f:
                    f.write(json.dumps(entry) + '\n')
            except Exception as e:
                logger.error(f"Failed to record usage: {e}")
    
    def get_cost_projections(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Get cost projections for executing a plan."""
        return {
            'estimated_requests': plan.get('required_requests', 0),
            'estimated_tokens': plan.get('required_tokens', 0),
            'estimated_time': plan.get('estimated_time', 0),
            'completion_probability': 0.8,  # Placeholder
            'budget_feasible': not self.is_any_budget_critical(0.8)
        }
    
    def emergency_conservation(self):
        """Activate emergency conservation mode."""
        logger.warning("Emergency conservation mode activated")
        
        # Reduce all budgets by 30%
        for budget in self.budgets.values():
            budget.allocated *= 0.7
        
        # Issue conservation warnings
        for callback in self.callbacks:
            try:
                callback(self, 'emergency', 'conservation', 0.7)
            except Exception as e:
                logger.error(f"Emergency callback failed: {e}")
    
    def restore_from_checkpoint(self, checkpoint_data: Dict[str, Any]):
        """Restore budget state from a checkpoint."""
        if 'budgets' in checkpoint_data:
            for budget_type, data in checkpoint_data['budgets'].items():
                if budget_type in self.budgets:
                    budget = self.budgets[budget_type]
                    budget.used = data.get('used', 0)
                    budget.allocated = data.get('allocated', budget.allocated)
    
    def create_checkpoint(self) -> Dict[str, Any]:
        """Create a checkpoint of current budget state."""
        checkpoint = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'budgets': {
                budget_type: {
                    'used': budget.used,
                    'allocated': budget.allocated,
                    'remaining': budget.remaining,
                    'usage_ratio': budget.usage_ratio
                }
                for budget_type, budget in self.budgets.items()
            }
        }
        
        return checkpoint