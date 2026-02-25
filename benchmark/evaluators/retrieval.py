"""
Retrieval evaluator for Needle-in-a-Haystack and RULER tasks.
Checks if the model retrieved the correct information from long context.
"""

import re
from typing import Dict, Any


class RetrievalEvaluator:
    """Evaluates long-context retrieval accuracy."""
    
    def evaluate(self, response: str, expected_fact: str, keywords: list = None) -> Dict[str, Any]:
        """
        Evaluate whether the expected fact was retrieved.
        
        Args:
            response: Model's generated response
            expected_fact: The fact that should be retrieved
            keywords: Optional list of keywords that must appear
        
        Returns:
            Dict with 'correct' (bool) and 'found_keywords'
        """
        response_lower = response.lower()
        expected_lower = expected_fact.lower()
        
        # Check for exact or near-exact match
        exact_match = expected_lower in response_lower
        
        # Check for keyword presence
        found_keywords = []
        if keywords:
            for kw in keywords:
                if kw.lower() in response_lower:
                    found_keywords.append(kw)
        
        # Correct if exact match OR all keywords found
        if keywords:
            correct = exact_match or (len(found_keywords) == len(keywords))
        else:
            correct = exact_match
        
        return {
            'correct': correct,
            'exact_match': exact_match,
            'found_keywords': found_keywords,
            'expected_keywords': keywords or []
        }
