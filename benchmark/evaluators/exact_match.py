"""
Exact match evaluator for GSM8K, MATH, MGSM.
Extracts numerical answers and compares them.
"""

import re
from typing import Dict, Any


class ExactMatchEvaluator:
    """Evaluates mathematical and numerical answers with exact matching."""
    
    def evaluate(self, response: str, expected: str) -> Dict[str, Any]:
        """
        Evaluate by extracting and comparing numerical answers.
        
        Args:
            response: Model's generated text
            expected: Expected numerical answer
        
        Returns:
            Dict with 'correct' (bool) and 'extracted_answer'
        """
        extracted = self._extract_number(response)
        expected_num = self._extract_number(expected)
        
        correct = False
        if extracted is not None and expected_num is not None:
            # Compare with small tolerance for floating point
            try:
                correct = abs(float(extracted) - float(expected_num)) < 1e-6
            except (ValueError, TypeError):
                correct = str(extracted).strip() == str(expected_num).strip()
        
        return {
            'correct': correct,
            'extracted_answer': extracted,
            'expected_answer': expected_num
        }
    
    def _extract_number(self, text: str) -> str or None:
        """
        Extract the final numerical answer from text.
        Looks for patterns like "####", "answer:", last number in text.
        """
        if not text:
            return None
        
        # Pattern 1: GSM8K format with ####
        match = re.search(r'####\s*(-?[\d,\.]+)', text)
        if match:
            return match.group(1).replace(',', '')
        
        # Pattern 2: "answer: X" or "answer is X"
        match = re.search(r'(?:answer|result)(?:\s*is)?[\s:=]+(-?[\d,\.]+)', text, re.IGNORECASE)
        if match:
            return match.group(1).replace(',', '')
        
        # Pattern 3: Number in box or emphasized
        match = re.search(r'\\boxed\{(-?[\d,\.]+)\}', text)
        if match:
            return match.group(1).replace(',', '')
        
        # Pattern 4: Last number in the text
        numbers = re.findall(r'-?[\d,]+\.?\d*', text)
        if numbers:
            return numbers[-1].replace(',', '')
        
        return None
