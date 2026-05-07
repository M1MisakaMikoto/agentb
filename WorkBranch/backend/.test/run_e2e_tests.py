#!/usr/bin/env python3
"""
E2E Tests Runner

统一的E2E测试入口，支持配置文件和命令行参数
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from test_cases import (
    APIClient,
    TestResult,
    Colors,
    load_config,
    wait_for_backend,
    start_backend,
    stop_backend,
    get_timestamp,
    safe_print,
)

from test_cases.direct_mode import run_direct_mode_test
from test_cases.plan_mode import run_plan_mode_test
from test_cases.search_mode import run_search_mode_test
from test_cases.serial_mode import run_serial_mode_test
from test_cases.workspace_upload import (
    run_workspace_upload_extract_write_test,
    run_workspace_upload_read_document_test,
    run_workspace_upload_image_understanding_test,
)
from test_cases.rag_search import run_rag_search_test
from test_cases.sql_query import run_sql_query_test, run_sql_agent_bridge_test
from test_cases.cross_lifecycle import run_cross_lifecycle_test
from test_cases.mq_resume import run_mq_resume_test
from test_cases.parallel import run_parallel_test


SCENARIO_RUNNERS = {
    "direct_mode": run_direct_mode_test,
    "plan_mode": run_plan_mode_test,
    "search_mode": run_search_mode_test,
    "serial_mode": run_serial_mode_test,
    "workspace_upload_extract_write": run_workspace_upload_extract_write_test,
    "workspace_upload_read_document": run_workspace_upload_read_document_test,
    "workspace_upload_image_understanding": run_workspace_upload_image_understanding_test,
    "rag_search": run_rag_search_test,
    "sql_query": run_sql_query_test,
    "sql_agent_bridge": run_sql_agent_bridge_test,
    "cross_lifecycle": run_cross_lifecycle_test,
    "mq_resume": run_mq_resume_test,
    "parallel": run_parallel_test,
}


def print_banner():
    banner = f"""
{Colors.CYAN}{'='*60}{Colors.ENDC}
{Colors.CYAN}  E2E Tests Runner{Colors.ENDC}
{Colors.CYAN}  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{Colors.ENDC}
{Colors.CYAN}{'='*60}{Colors.ENDC}
"""
    print(banner)


def print_summary(results: List[TestResult], total_duration: float):
    print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}  Test Summary{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")
    
    passed = 0
    failed = 0
    
    for result in results:
        status = f"{Colors.GREEN}PASS{Colors.ENDC}" if not result.errors else f"{Colors.RED}FAIL{Colors.ENDC}"
        if result.errors:
            failed += 1
        else:
            passed += 1
        
        print(f"  {status} - {result.scenario}")
        if result.errors:
            for error in result.errors:
                print(f"         {Colors.RED}Error: {error}{Colors.ENDC}")
    
    print(f"\n{Colors.CYAN}{'-'*60}{Colors.ENDC}")
    print(f"  Total: {len(results)} tests")
    print(f"  {Colors.GREEN}Passed: {passed}{Colors.ENDC}")
    if failed > 0:
        print(f"  {Colors.RED}Failed: {failed}{Colors.ENDC}")
    print(f"  Duration: {total_duration:.2f}s")
    print(f"{Colors.CYAN}{'='*60}{Colors.ENDC}\n")
    
    return failed == 0


def parse_args():
    parser = argparse.ArgumentParser(
        description="E2E Tests Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_e2e_tests.py                           # Run all tests
  python run_e2e_tests.py --suite basic             # Run basic suite
  python run_e2e_tests.py --scenario direct_mode    # Run specific scenario
  python run_e2e_tests.py --no-server               # Don't start backend
  python run_e2e_tests.py --config my_config.yaml   # Use custom config
  python run_e2e_tests.py --output results.log      # Save output to file
        """
    )
    
    parser.add_argument(
        "--suite",
        type=str,
        default=None,
        help="Test suite to run (basic, workspace, tools, advanced, all)"
    )
    
    parser.add_argument(
        "--scenario",
        type=str,
        default=None,
        help="Specific scenario to run"
    )
    
    parser.add_argument(
        "--no-server",
        action="store_true",
        help="Don't start backend server (assume it's already running)"
    )
    
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config file (default: test_config.yaml)"
    )
    
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Path to save output log"
    )
    
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output"
    )
    
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available suites and scenarios"
    )
    
    return parser.parse_args()


def list_suites_and_scenarios(config: Dict):
    print(f"\n{Colors.CYAN}Available Suites:{Colors.ENDC}")
    for suite_name, suite_config in config.get("suites", {}).items():
        print(f"  {Colors.GREEN}{suite_name}{Colors.ENDC}: {suite_config.get('description', '')}")
        for scenario in suite_config.get("scenarios", []):
            scenario_config = config.get("scenarios", {}).get(scenario, {})
            print(f"    - {scenario}: {scenario_config.get('description', '')}")
    
    print(f"\n{Colors.CYAN}Available Scenarios:{Colors.ENDC}")
    for scenario_name in SCENARIO_RUNNERS.keys():
        scenario_config = config.get("scenarios", {}).get(scenario_name, {})
        print(f"  {Colors.GREEN}{scenario_name}{Colors.ENDC}: {scenario_config.get('description', '')}")


def get_scenarios_to_run(config: Dict, suite: Optional[str], scenario: Optional[str]) -> List[str]:
    if scenario:
        if scenario not in SCENARIO_RUNNERS:
            print(f"{Colors.RED}Error: Unknown scenario '{scenario}'{Colors.ENDC}")
            print(f"Available scenarios: {list(SCENARIO_RUNNERS.keys())}")
            sys.exit(1)
        return [scenario]
    
    if suite:
        if suite not in config.get("suites", {}):
            print(f"{Colors.RED}Error: Unknown suite '{suite}'{Colors.ENDC}")
            print(f"Available suites: {list(config.get('suites', {}).keys())}")
            sys.exit(1)
        return config["suites"][suite].get("scenarios", [])
    
    return config.get("suites", {}).get("all", {}).get("scenarios", list(SCENARIO_RUNNERS.keys()))


class OutputDuplicator:
    def __init__(self, file_path: Optional[str]):
        self.file = None
        if file_path:
            self.file = open(file_path, "w", encoding="utf-8")
    
    def write(self, text: str):
        print(text)
        if self.file:
            self.file.write(text + "\n")
            self.file.flush()
    
    def close(self):
        if self.file:
            self.file.close()


async def run_tests(
    config: Dict,
    scenarios: List[str],
    start_server: bool,
    verbose: bool,
    output: Optional[OutputDuplicator] = None,
) -> List[TestResult]:
    results = []
    backend_process = None
    
    try:
        if start_server:
            backend_process = start_backend()
            api_config = config.get("api", {})
            host = api_config.get("host", "127.0.0.1")
            port = api_config.get("port", 8000)
            if not wait_for_backend(host, port, timeout=30.0):
                print(f"{Colors.RED}Failed to start backend{Colors.ENDC}")
                return results
        else:
            print(f"{Colors.CYAN}Using existing backend{Colors.ENDC}")
        
        api = APIClient(config)
        
        for scenario_name in scenarios:
            if scenario_name not in SCENARIO_RUNNERS:
                print(f"{Colors.YELLOW}Warning: Unknown scenario '{scenario_name}', skipping{Colors.ENDC}")
                continue
            
            scenario_config = config.get("scenarios", {}).get(scenario_name, {})
            runner = SCENARIO_RUNNERS[scenario_name]
            
            try:
                result = await runner(api, scenario_config, verbose=verbose)
                results.append(result)
            except Exception as e:
                print(f"{Colors.RED}Error running {scenario_name}: {e}{Colors.ENDC}")
                error_result = TestResult(scenario_name, scenario_config)
                error_result.errors.append(str(e))
                results.append(error_result)
    
    finally:
        if backend_process:
            stop_backend(backend_process)
    
    return results


def main():
    args = parse_args()
    
    config_path = Path(__file__).parent / "test_config.yaml"
    if args.config:
        config_path = Path(args.config)
    
    try:
        config = load_config(str(config_path))
    except FileNotFoundError:
        print(f"{Colors.RED}Error: Config file not found: {config_path}{Colors.ENDC}")
        sys.exit(1)
    except Exception as e:
        print(f"{Colors.RED}Error loading config: {e}{Colors.ENDC}")
        sys.exit(1)
    
    if args.list:
        list_suites_and_scenarios(config)
        return
    
    print_banner()
    
    scenarios = get_scenarios_to_run(config, args.suite, args.scenario)
    
    print(f"{Colors.CYAN}Running scenarios:{Colors.ENDC}")
    for s in scenarios:
        print(f"  - {s}")
    print()
    
    output = OutputDuplicator(args.output) if args.output else None
    
    start_time = time.time()
    results = asyncio.run(run_tests(
        config,
        scenarios,
        start_server=not args.no_server,
        verbose=args.verbose,
        output=output,
    ))
    total_duration = time.time() - start_time
    
    all_passed = print_summary(results, total_duration)
    
    if output:
        output.close()
    
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
