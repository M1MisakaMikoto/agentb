#!/usr/bin/env python3
"""
Parallel Test

测试并行执行多个测试用例
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Dict, List

from .base import (
    APIClient,
    TestResult,
    Colors,
    print_test_header,
    print_step,
    print_success,
    print_error,
    print_dim,
    collect_stream_output,
    wait_for_conversation_state,
    extract_response_text,
)


@dataclass
class ParallelTestResult:
    process_id: str
    user_id: int
    theme: str
    session_id: int
    conversation_id: str
    response_text: str
    keywords_found: List[str]
    keywords_missing: List[str]
    duration: float
    errors: List[str]


async def run_single_parallel_test(
    api: APIClient,
    test_case: dict,
    verbose: bool = True,
) -> ParallelTestResult:
    start_time = time.time()
    process_id = test_case.get("process_id", "unknown")
    user_id = test_case.get("user_id", 1)
    theme = test_case.get("theme", "general")
    question = test_case.get("question", "Hello")
    keywords = test_case.get("keywords", [])
    
    errors = []
    session_id = None
    conversation_id = None
    response_text = ""
    
    try:
        test_api = APIClient(api.config, user_id=user_id)
        
        session_result = await test_api.create_session(title=f"Parallel Test - {theme}")
        if not session_result.get("success", True):
            errors.append(f"create_session: {session_result.get('message')}")
        else:
            session_id = session_result.get("data", {}).get("id")
            
            conv_result = await test_api.create_conversation(session_id, question)
            if not conv_result.get("success", True):
                errors.append(f"create_conversation: {conv_result.get('message')}")
            else:
                conversation_id = conv_result.get("data", {}).get("conversation_id")
                
                await wait_for_conversation_state(test_api, conversation_id, "processing", timeout=10.0)
                
                result = TestResult(f"parallel_{process_id}", {})
                await collect_stream_output(test_api, conversation_id, result, verbose=False)
                
                final_result = await wait_for_conversation_state(test_api, conversation_id, "completed", timeout=120.0)
                response_text = extract_response_text(final_result)
    
    except Exception as e:
        errors.append(str(e))
    
    duration = time.time() - start_time
    
    keywords_found = [kw for kw in keywords if kw in response_text]
    keywords_missing = [kw for kw in keywords if kw not in response_text]
    
    return ParallelTestResult(
        process_id=process_id,
        user_id=user_id,
        theme=theme,
        session_id=session_id,
        conversation_id=conversation_id,
        response_text=response_text,
        keywords_found=keywords_found,
        keywords_missing=keywords_missing,
        duration=duration,
        errors=errors,
    )


async def run_parallel_test(api: APIClient, scenario_config: dict, verbose: bool = True) -> TestResult:
    result = TestResult("parallel", scenario_config)
    
    print_test_header(scenario_config.get("description", "Parallel Test"))
    
    test_cases = scenario_config.get("test_cases", [
        {"process_id": "math_user", "user_id": 10001, "theme": "数学", "question": "请计算 123 * 456 等于多少？", "keywords": ["56088", "计算"]},
        {"process_id": "programming_user", "user_id": 10002, "theme": "编程", "question": "Python中如何实现单例模式？", "keywords": ["单例", "Python"]},
        {"process_id": "greeting_user", "user_id": 10003, "theme": "问候", "question": "你好", "keywords": ["你好", "帮助"]},
    ])
    
    print_step(1, f"Starting {len(test_cases)} parallel tests...", Colors.CYAN)
    for tc in test_cases:
        print_dim(f"  - {tc.get('process_id')}: {tc.get('theme')} (user_id={tc.get('user_id')})")
    
    print_step(2, "Running tests in parallel...", Colors.CYAN)
    start_time = time.time()
    
    tasks = [
        run_single_parallel_test(api, tc, verbose)
        for tc in test_cases
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    total_duration = time.time() - start_time
    print_success(f"All tests completed in {total_duration:.2f}s")
    
    print_step(3, "Validating results...", Colors.CYAN)
    
    all_passed = True
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            print_error(f"Test {i} failed with exception: {res}")
            result.errors.append(f"test_{i}: {res}")
            all_passed = False
            continue
        
        print_dim(f"\n  [{res.process_id}] Theme: {res.theme}")
        print_dim(f"    Duration: {res.duration:.2f}s")
        
        if res.errors:
            print_error(f"    Errors: {res.errors}")
            all_passed = False
        else:
            print_success(f"    Completed successfully")
        
        if res.keywords_found:
            print_success(f"    Keywords found: {res.keywords_found}")
        if res.keywords_missing:
            print_error(f"    Keywords missing: {res.keywords_missing}")
        
        if res.response_text:
            print_dim(f"    Response length: {len(res.response_text)} chars")
    
    print_step(4, "Summary...", Colors.CYAN)
    
    successful = sum(1 for r in results if isinstance(r, ParallelTestResult) and not r.errors)
    failed = len(results) - successful
    
    print_dim(f"  Total tests: {len(results)}")
    print_success(f"  Successful: {successful}")
    if failed > 0:
        print_error(f"  Failed: {failed}")
    
    result.response_text = f"Parallel test completed: {successful}/{len(results)} successful"
    result.event_count = len(results)
    
    print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.GREEN}  Parallel Test Completed{Colors.ENDC}")
    print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}\n")
    
    return result
