"""
Multiple choice evaluator for MMLU, ARC, TruthfulQA.
"""

import re
from typing import Dict, Any


class MultipleChoiceEvaluator:
    """Evaluates multiple choice questions with A/B/C/D answers."""
    
    def __init__(self):
        self.valid_choices = {'A', 'B', 'C', 'D'}
    
    def evaluate(self, response: str, expected: str) -> Dict[str, Any]:
        """
        Evaluate a multiple choice response.
        
        Args:
            response: Model's generated text
            expected: Correct answer (A, B, C, or D)
        
        Returns:
            Dict with 'correct' (bool) and 'extracted_answer' (str or None)
        """
        extracted = self._extract_choice(response)
        correct = extracted == expected.strip().upper() if extracted else False
        
        return {
            'correct': correct,
            'extracted_answer': extracted,
            'expected_answer': expected.strip().upper()
        }
    
    def _extract_choice(self, text: str) -> str or None:
        """
        Extract the answer choice from model response.
        Looks for patterns like "A)", "Answer: B", "(C)", etc.
        """
        text = text.strip().upper()
        
        # Pattern 1: Just the letter (entire response)
        if text in self.valid_choices:
            return text
        
        # Pattern 2: "Answer: X" or "The answer is X"
        match = re.search(r'(?:ANSWER|CHOICE)(?:\s*IS)?[\s:]+([A-D])', text)
        if match:
            return match.group(1)
        
        # Pattern 3: Letter in parentheses or with punctuation
        match = re.search(r'[\(\[]?([A-D])[\)\]]?[:\.\)]', text)
        if match:
            return match.group(1)
        
        # Pattern 4: First A-D found in text
        match = re.search(r'\b([A-D])\b', text)
        if match:
            return match.group(1)
        
        return None
