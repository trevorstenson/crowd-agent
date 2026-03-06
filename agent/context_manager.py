"""
Context management and compression utilities.

Helps the agent stay within context window limits while preserving
the most important information for the task at hand.
"""

import re
import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ContentPriority:
    """Represents the priority of content for retention during compression."""
    score: float
    content_type: str  # 'error', 'config', 'result', 'plan', 'meta'
    relevance: float = 1.0
    
    def __lt__(self, other):
        return self.score < other.score


class ContextManager:
    """Manages context compression and prioritization for large contexts."""
    
    def __init__(self, max_tokens: int = 4000, preserve_ratio: float = 0.7):
        self.max_tokens = max_tokens
        self.preserve_ratio = preserve_ratio
        self.token_counts: Dict[str, int] = {}
    
    def estimate_tokens(self, text: str) -> int:
        """Estimate token count for text (rough approximation)."""
        if not text:
            return 0
        
        # Remove excess whitespace
        text = re.sub(r'\s+', ' ', text.strip())
        
        # Rough estimation: 1 token ≈ 4 characters
        return len(text) // 4
    
    def compress_by_sections(self, content: str, target_tokens: int) -> str:
        """Compress content by prioritizing important sections."""
        lines = content.split('\n')
        
        # Score lines by importance
        scored_lines = []
        in_error_section = False
        in_config_section = False
        
        for i, line in enumerate(lines):
            score = 1.0
            
            # Boost error and configuration sections
            if 'error' in line.lower() and ('###' in line or '##' in line):
                score += 3.0
                in_error_section = True
            elif ('config' in line.lower() or 'configuration' in line.lower()) and ('###' in line or '##' in line):
                score += 2.0
                in_config_section = True
            elif line.startswith('#') and 'important' in line.lower():
                score += 1.5
            elif 'summary' in line.lower() and line.startswith('##'):
                score += 1.2
            elif 'constraint' in line.lower() and line.startswith('###'):
                score += 2.0
            
            # Apply section context
            if in_error_section and '```' in line:
                score += 1.0
            elif in_config_section and ('{' in line or '[' in line):
                score += 0.8
            
            scored_lines.append((score, line))
            
            # Reset section context
            if in_error_section and line.startswith('#') and 'error' not in line.lower():
                in_error_section = False
            if in_config_section and line.startswith('#') and 'config' not in line.lower():
                in_config_section = False
        
        # Sort by score (descending)
        scored_lines.sort(key=lambda x: x[0], reverse=True)
        
        # Select lines that fit within target tokens
        selected_lines = []
        total_tokens = 0
        
        for score, line in scored_lines:
            line_tokens = self.estimate_tokens(line)
            if total_tokens + line_tokens <= target_tokens:
                # Maintain original order for readability
                insert_pos = len(selected_lines)
                for j, (_, existing_line) in enumerate(selected_lines):
                    if j >= len(lines):
                        break
                    if existing_line == line:
                        insert_pos = j
                        break
                
                selected_lines.insert(insert_pos, (score, line))
                total_tokens += line_tokens
        
        # Sort by original line position
        final_lines = []
        for line_num, (score, line) in enumerate(selected_lines):
            try:
                original_pos = lines.index(line)
                if original_pos >= 0:
                    final_lines.append((original_pos, line))
            except ValueError:
                pass
        
        final_lines.sort(key=lambda x: x[0])
        result = '\n'.join([line for _, line in final_lines])
        
        # Add compression notice
        original_tokens = self.estimate_tokens(content)
        if original_tokens <= target_tokens:
            return content
        
        return result
    
    def create_condensed_summary(self, content: str, max_tokens: int) -> str:
        """Create a condensed summary of content."""
        if self.estimate_tokens(content) <= max_tokens:
            return content
        
        lines = content.split('\n')
        sections = {}
        current_section = "general"
        
        # Group lines by sections
        for line in lines:
            if line.startswith('#'):
                current_section = line.lstrip('#').strip().lower()
                if current_section not in sections:
                    sections[current_section] = []
            elif current_section:
                sections[current_section].append(line)
        
        # Create summary by section
        summary_parts = []
        high_priority_sections = ['error', 'summary', 'overview', 'result', 'config']
        
        # Include high priority sections
        for section in high_priority_sections:
            if section in sections:
                section_content = '\n'.join(sections[section])
                summary_parts.append(f"## {section.title()}\n{section_content[:200]}...")
        
        # Add notice about truncated content
        if not summary_parts:
            summary_parts.append("## Summary")
            summary_parts.append("Content truncated due to context limits.")
        
        result = '\n\n'.join(summary_parts)
        result_tokens = self.estimate_tokens(result)
        
        # Further trim if needed
        if result_tokens > max_tokens:
            # Keep only the most important parts
            lines = result.split('\n')
            compressed_lines = []
            total_tokens = 0
            
            for line in lines:
                line_tokens = self.estimate_tokens(line)
                if total_tokens + line_tokens <= max_tokens:
                    compressed_lines.append(line)
                    total_tokens += line_tokens
            
            result = '\n'.join(compressed_lines)
        
        return result
    
    def prioritize_file_contents(self, file_paths: List[str], context_hint: str = "") -> List[str]:
        """Prioritize which file contents to include in context."""
        if not context_hint:
            return file_paths
        
        scored_files = []
        
        for file_path in file_paths:
            score = 0.0
            
            # Score based on file type
            if file_path.endswith('.py'):
                score += 0.3
            elif file_path.endswith(('.md', '.txt')):
                score += 0.2
            elif 'config' in file_path:
                score += 0.4
            
            # Score based on relevance to context hint
            path_lower = file_path.lower()
            hint_lower = context_hint.lower()
            
            if any(word in path_lower for word in hint_lower.split()):
                score += 0.5
            
            if 'error' in hint_lower and ('error' in path_lower or 'debug' in path_lower):
                score += 0.3
            
            if 'config' in hint_lower and ('config' in path_lower or 'setup' in path_lower):
                score += 0.3
            
            if 'main' in path_lower or 'core' in path_lower:
                score += 0.2
            
            scored_files.append((score, file_path))
        
        # Sort by score
        scored_files.sort(key=lambda x: x[0], reverse=True)
        
        return [file_path for _, file_path in scored_files]
    
    def create_context_mask(self, content_priority: Dict[str, ContentPriority], 
                          target_tokens: int) -> Dict[str, bool]:
        """Create a mask indicating which content to keep based on priority."""
        mask = {}
        total_tokens = 0
        
        # Sort content by priority score
        sorted_content = sorted(content_priority.items(), 
                               key=lambda x: x[1].score * x[1].relevance, 
                               reverse=True)
        
        for key, priority in sorted_content:
            estimated_tokens = target_tokens // len(content_priority)  # Even distribution
            
            if total_tokens + estimated_tokens <= target_tokens:
                mask[key] = True
                total_tokens += estimated_tokens
            else:
                mask[key] = False
        
        return mask
    
    def progressive_compression(self, content: str, stages: int = 3) -> List[str]:
        """Perform progressive compression of content through multiple stages."""
        current_tokens = self.estimate_tokens(content)
        target_tokens = int(current_tokens * self.preserve_ratio)
        compression_stages = []
        
        current_content = content
        for stage in range(stages):
            # Reduce target progressively
            stage_target = target_tokens * (1 - 0.3 * stage)
            
            if self.estimate_tokens(current_content) <= stage_target:
                compression_stages.append(current_content)
                break
            
            # Apply different compression strategies
            if stage == 0:
                current_content = self.compress_by_sections(current_content, stage_target)
            elif stage == 1:
                current_content = self.create_condensed_summary(current_content, stage_target)
            else:
                # Final stage: brutal truncation with notice
                if len(current_content) > stage_target * 4:
                    truncated = current_content[:int(stage_target * 4)]
                    current_content = f"{truncated}\n[... {self.estimate_tokens(current_content)} -> {stage_target} tokens compressed ...]"
            
            compression_stages.append(current_content)
        
        return compression_stages
    
    def smart_truncate(self, text: str, max_tokens: int, preserve_sentences: bool = True) -> str:
        """Intelligently truncate text while preserving meaning."""
        if self.estimate_tokens(text) <= max_tokens:
            return text
        
        if preserve_sentences:
            sentences = text.split('. ')
            current_text = ""
            
            for sentence in sentences:
                test_text = current_text + (" " if current_text else "") + sentence + "."
                if self.estimate_tokens(test_text) > max_tokens:
                    break
                current_text = test_text
            
            return current_text.strip() if current_text else text[:max_tokens * 3]
        else:
            # Simple truncation with ellipsis
            truncated_length = max_tokens * 4 - 20  # Leave room for suffix
            if truncated_length > 0:
                return f"{text[:truncated_length]} [truncated]"
            return text
    
    def get_compression_ratio(self, original: str, compressed: str) -> float:
        """Calculate the compression ratio achieved."""
        original_tokens = self.estimate_tokens(original)
        compressed_tokens = self.estimate_tokens(compressed)
        return (original_tokens - compressed_tokens) / max(original_tokens, 1)
    
    def estimate_file_tokens(self, file_path: str, file_content: str) -> Dict[str, int]:
        """Estimate tokens for different parts of a file."""
        estimates = {
            'total': self.estimate_tokens(file_content),
            'header': 0,
            'imports': 0,
            'functions': 0,
            'comments': 0
        }
        
        lines = file_content.split('\n')
        
        for line in lines:
            if line.startswith('#') and estimates['header'] < 50:
                estimates['header'] += self.estimate_tokens(line)
            elif line.startswith('import') or line.startswith('from'):
                estimates['imports'] += self.estimate_tokens(line)
            elif line.strip().startswith('#'):
                estimates['comments'] += self.estimate_tokens(line)
            elif line.startswith('def ') and estimates['functions'] < 200:
                estimates['functions'] += self.estimate_tokens(line)
        
        return estimates