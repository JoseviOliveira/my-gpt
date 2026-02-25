"""
Instruction following evaluator for IFEval.
Validates constraint adherence (format, length, content rules).
"""

import re
from typing import Dict, Any, List


class InstructionEvaluator:
    """Evaluates instruction following with verifiable constraints."""
    
    def evaluate(self, response: str, constraints: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Evaluate whether response meets all specified constraints.
        
        Args:
            response: Model's generated text
            constraints: List of constraint dicts with 'type' and parameters
        
        Returns:
            Dict with 'correct' (bool), 'satisfied_constraints', 'violations'
        """
        violations = []
        satisfied = []
        
        for constraint in constraints:
            constraint_type = constraint.get('type')
            
            if constraint_type == 'length':
                satisfied_c, violation = self._check_length(response, constraint)
            elif constraint_type == 'format':
                satisfied_c, violation = self._check_format(response, constraint)
            elif constraint_type == 'keyword':
                satisfied_c, violation = self._check_keyword(response, constraint)
            elif constraint_type == 'forbidden':
                satisfied_c, violation = self._check_forbidden(response, constraint)
            elif constraint_type == 'structure':
                satisfied_c, violation = self._check_structure(response, constraint)
            else:
                satisfied_c, violation = False, f"Unknown constraint type: {constraint_type}"
            
            if satisfied_c:
                satisfied.append(constraint_type)
            else:
                violations.append(violation)
        
        correct = len(violations) == 0
        
        return {
            'correct': correct,
            'satisfied_constraints': satisfied,
            'violations': violations
        }
    
    def _check_length(self, text: str, constraint: dict) -> tuple:
        """Check word or character length constraints."""
        unit = constraint.get('unit', 'words')
        min_val = constraint.get('min', 0)
        max_val = constraint.get('max', float('inf'))
        
        if unit == 'words':
            count = len(text.split())
        else:  # characters
            count = len(text)
        
        if min_val <= count <= max_val:
            return True, None
        else:
            return False, f"Length {count} {unit} outside range [{min_val}, {max_val}]"
    
    def _check_format(self, text: str, constraint: dict) -> tuple:
        """Check format constraints (JSON, bullet points, numbered list, etc.)."""
        format_type = constraint.get('format_type')
        
        if format_type == 'json':
            import json
            try:
                json.loads(text)
                return True, None
            except json.JSONDecodeError as e:
                return False, f"Invalid JSON: {str(e)}"
        
        elif format_type == 'bullet_list':
            lines = text.strip().split('\n')
            bullet_lines = [l for l in lines if l.strip().startswith(('*', '-', '•'))]
            if bullet_lines:
                return True, None
            return False, "No bullet points found"
        
        elif format_type == 'numbered_list':
            if re.search(r'^\d+\.', text, re.MULTILINE):
                return True, None
            return False, "No numbered list found"
        
        return False, f"Unknown format type: {format_type}"
    
    def _check_keyword(self, text: str, constraint: dict) -> tuple:
        """Check for required keyword presence."""
        keywords = constraint.get('keywords', [])
        text_lower = text.lower()
        
        missing = [kw for kw in keywords if kw.lower() not in text_lower]
        
        if not missing:
            return True, None
        return False, f"Missing keywords: {missing}"
    
    def _check_forbidden(self, text: str, constraint: dict) -> tuple:
        """Check that forbidden words/phrases are absent."""
        forbidden = constraint.get('forbidden', [])
        text_lower = text.lower()
        
        found = [word for word in forbidden if word.lower() in text_lower]
        
        if not found:
            return True, None
        return False, f"Forbidden words found: {found}"
    
    def _check_structure(self, text: str, constraint: dict) -> tuple:
        """Check structural requirements (paragraphs, sections, etc.)."""
        structure_type = constraint.get('structure_type')
        
        if structure_type == 'paragraphs':
            min_paragraphs = constraint.get('min', 1)
            paragraphs = [p for p in text.split('\n\n') if p.strip()]
            if len(paragraphs) >= min_paragraphs:
                return True, None
            return False, f"Found {len(paragraphs)} paragraphs, need {min_paragraphs}"
        
        return False, f"Unknown structure type: {structure_type}"
