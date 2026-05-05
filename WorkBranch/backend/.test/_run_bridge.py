import sys, os
sys.path.insert(0, r'e:\PythonProject\agentb\WorkBranch\backend\.test')
os.chdir(r'e:\PythonProject\agentb\WorkBranch\backend\.test')

import asyncio
from test_cases.bridge_predict import run_bridge_predict_test
from test_cases.base import APIClient, load_config

async def main():
    config = load_config()
    api = APIClient(config)
    scenario_cfg = config.get('scenarios', {}).get('bridge_predict', {})
    scenario_cfg['prediction_timeout'] = 900.0
    
    result = await run_bridge_predict_test(api, scenario_cfg, verbose=True)
    
    print()
    print('='*60)
    print(f'Errors: {result.errors}')
    print(f'Tool calls: {result.tool_calls}')
    print(f'Response length: {len(result.response_text)}')
    print(f'Done: {result.done}')
    if not result.errors:
        print('\n*** TEST PASSED ***')
    else:
        print('\n*** TEST COMPLETED WITH ISSUES ***')
    print('='*60)

asyncio.run(main())
