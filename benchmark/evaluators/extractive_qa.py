"""
Extractive QA evaluator for XQuAD (Cross-lingual Question Answering).
Computes F1 score and exact match for span-based answers.
"""

import re
import string
from collections import Counter
from typing import Dict, Any


class ExtractivevQAEvaluator:
    """Evaluates extractive question answering with F1 and EM metrics."""
    
    def evaluate(self, response: str, expected: str or list) -> Dict[str, Any]:
        """
        Evaluate extractive QA response.
        
        Args:
            response: Model's answer span
            expected: Expected answer(s) - can be string or list of acceptable answers
        
        Returns:
            Dict with 'f1', 'exact_match', and 'correct' (bool)
        """
        if isinstance(expected, str):
            expected = [expected]
        
        # Normalize response
        response_norm = self._normalize(response)
        
        # Calculate best F1 and EM across all acceptable answers
        best_f1 = 0.0
        exact_match = False
        
        for exp in expected:
            exp_norm = self._normalize(exp)
            
            f1 = self._compute_f1(response_norm, exp_norm)
            best_f1 = max(best_f1, f1)
            
            if response_norm == exp_norm:
                exact_match = True
        
        return {
            'f1': best_f1,
            'exact_match': exact_match,
            'correct': best_f1 > 0.5  # Consider correct if F1 > 0.5
        }
    
    def _normalize(self, text: str) -> str:
        """Normalize text for comparison."""
        if not text:
            return ""
        
        # Lowercase
        text = text.lower()
        
        # Remove articles
        text = re.sub(r'\b(a|an|the)\b', ' ', text)
        
        # Remove punctuation
        text = text.translate(str.maketrans('', '', string.punctuation))
        
        # Normalize whitespace
        text = ' '.join(text.split())
        
        return text
    
    def _compute_f1(self, prediction: str, reference: str) -> float:
        """Compute F1 score between prediction and reference."""
        pred_tokens = prediction.split()
        ref_tokens = reference.split()
        
        if not pred_tokens or not ref_tokens:
            return 0.0
        
        common = Counter(pred_tokens) & Counter(ref_tokens)
        num_same = sum(common.values())
        
        if num_same == 0:
            return 0.0
        
        precision = num_same / len(pred_tokens)
        recall = num_same / len(ref_tokens)
        
        f1 = 2 * (precision * recall) / (precision + recall)
        return f1
