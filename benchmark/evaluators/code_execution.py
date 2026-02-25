"""
Code execution evaluator for HumanEval and MBPP.
Executes generated code against test cases.
"""

import ast
import sys
import io
import contextlib
import multiprocessing
import queue
from typing import Dict, Any

def _run_test_case_process(code, test, result_queue):
    """Helper function to run code in a separate process."""
    try:
        # Create isolated namespace
        namespace = {}
        
        # Standard imports to be safe?
        # Usually models import what they need.
        
        # Execute the code
        exec(code, namespace)
        
        # Execute the test
        exec(test, namespace)
        
        result_queue.put(True)
    except Exception as e:
        result_queue.put(str(e))

class CodeExecutionEvaluator:
    """Evaluates Python code by executing test cases."""
    
    def __init__(self, timeout_seconds: int = 5):
        self.timeout = timeout_seconds
    
    def evaluate(self, response: str, test_cases: list, entry_point: str = None) -> Dict[str, Any]:
        """
        Evaluate generated code by running test cases.
        
        Args:
            response: Model's generated code
            test_cases: List of test case strings to execute
            entry_point: Function name to test (for HumanEval)
        
        Returns:
            Dict with 'correct' (bool), 'passed_tests', 'total_tests', 'error'
        """
        code = self._extract_code(response)
        
        if not code:
            return {
                'correct': False,
                'passed_tests': 0,
                'total_tests': len(test_cases),
                'error': 'No code block found'
            }
        
        passed = 0
        error_msg = None
        
        for i, test in enumerate(test_cases):
            q = multiprocessing.Queue()
            p = multiprocessing.Process(target=_run_test_case_process, args=(code, test, q))
            p.start()
            
            p.join(self.timeout)
            
            if p.is_alive():
                p.terminate()
                p.join()
                if error_msg is None:
                    error_msg = f"Test {i+1} failed: Timed out after {self.timeout}s"
            else:
                if not q.empty():
                    result = q.get()
                    if result is True:
                        passed += 1
                    else:
                        if error_msg is None:
                            error_msg = f"Test {i+1} failed: {result}"
                else:
                    if error_msg is None:
                        error_msg = f"Test {i+1} failed: Process crashed or produced no result"
        
        correct = passed == len(test_cases)
        
        return {
            'correct': correct,
            'passed_tests': passed,
            'total_tests': len(test_cases),
            'error': error_msg
        }
    
    def _extract_code(self, text: str) -> str or None:
        """Extract Python code from markdown code blocks."""
        # Look for ```python or ``` code blocks
        import re
        
        # Try python code block first
        match = re.search(r'```python\n(.*?)\n```', text, re.DOTALL)
        if match:
            return match.group(1)
        
        # Try generic code block
        match = re.search(r'```\n(.*?)\n```', text, re.DOTALL)
        if match:
            code = match.group(1)
            # Check if it looks like Python
            if 'def ' in code or 'import ' in code or 'return ' in code:
                return code
        
        # If no code block, return the whole response (might be direct code)
        if 'def ' in text:
            return text
        
        return None
