import sys, os, re
sys.path.insert(0, r'e:\PythonProject\agentb\WorkBranch\backend\.test')
os.chdir(r'e:\PythonProject\agentb\WorkBranch\backend\.test')

import asyncio
import time
import argparse
from test_cases.bridge_predict import run_bridge_predict_test, run_all_bridges_test, BRIDGE_CONFIGS
from test_cases.base import APIClient, load_config, start_backend, stop_backend


def merge_delta_logs(log_file):
    if not os.path.exists(log_file) or os.path.getsize(log_file) == 0:
        return

    with open(log_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    blocks = []
    current_block = []
    for line in lines:
        if '=== 🔍 DIAGNOSTIC:' in line and current_block:
            blocks.append(current_block)
            current_block = [line]
        else:
            current_block.append(line)
    if current_block:
        blocks.append(current_block)

    merged = []
    buffer = []
    buffer_type = None

    for block in blocks:
        is_diagnostic = any('MQ PUBLISH_SYNC' in b for b in block)

        if not is_diagnostic:
            if buffer:
                merged.append(_format_merged(buffer_type, buffer))
                buffer = []
                buffer_type = None
            merged.extend(block)
            continue

        ts = ''
        for b in block:
            if '[' in b and ']' in b:
                ts = b.split(']')[0] + ']'
                break

        msg_type = None
        content = None

        for b in block:
            stripped = b.strip()
            if '- type:' in stripped:
                t = stripped.split('type:')[1].strip()
                if t in ('thinking_delta', 'chat_delta'):
                    msg_type = t
            if '- content preview:' in stripped and msg_type:
                content = stripped.split('content preview:')[1].strip()

        if msg_type and content:
            if buffer_type == msg_type and buffer:
                buffer.append((ts, content))
            else:
                if buffer:
                    merged.append(_format_merged(buffer_type, buffer))
                buffer = [(ts, content)]
                buffer_type = msg_type
        else:
            if buffer:
                merged.append(_format_merged(buffer_type, buffer))
                buffer = []
                buffer_type = None
            merged.extend(block)

    if buffer:
        merged.append(_format_merged(buffer_type, buffer))

    with open(log_file, 'w', encoding='utf-8') as f:
        f.writelines(merged)


def _format_merged(msg_type, entries):
    ts_start = entries[0][0]
    seq_end = len(entries) - 1
    combined_content = ''.join(e[1] for e in entries)

    return (
        f'{ts_start} === 🔍 DIAGNOSTIC: MQ PUBLISH_SYNC ===\n'
        f'{ts_start} 📤 Message details:\n'
        f'  - type: {msg_type} (merged {len(entries)} deltas)\n'
        f'  - content length: {len(combined_content)} chars\n'
        f'  - content preview: {combined_content}\n\n'
    )

def _save_llm_responses(log_file):
    summary_file = log_file.replace('.log', '_responses_summary.txt')

    if not os.path.exists(log_file) or os.path.getsize(log_file) == 0:
        with open(summary_file, 'w', encoding='utf-8') as f:
            f.write('No LLM responses found\n')
        return 0

    with open(log_file, 'r', encoding='utf-8') as f:
        content = f.read()

    blocks = content.split('=== 🤖 LLM RAW RESPONSE ===')

    if len(blocks) <= 1:
        with open(summary_file, 'w', encoding='utf-8') as f:
            f.write('No LLM responses detected\n')
        return 0

    with open(summary_file, 'w', encoding='utf-8') as f:
        f.write('='*60 + '\n')
        f.write('LLM Raw Response Summary\n')
        f.write('='*60 + '\n\n')

        for idx, block in enumerate(blocks[1:], 1):
            lines = block.strip().split('\n')
            ts_line = lines[0] if lines else ''

            response_lines = []
            history_lines = []
            in_response = False
            in_history = False

            for line in lines[1:]:
                if 'Raw response (' in line:
                    in_response = True
                    in_history = False
                    response_lines.append(line)
                elif 'Tool history in prompt:' in line:
                    in_response = False
                    in_history = True
                    history_lines.append(line)
                elif in_response:
                    response_lines.append(line)
                elif in_history:
                    history_lines.append(line)

            f.write(f'\n--- Decision #{idx} ---\n')
            f.write(f'{ts_line}\n')

            if response_lines:
                header = response_lines[0]
                f.write(f'  {header}\n')
                resp_text = '\n'.join(response_lines[1:])
                if len(resp_text) > 500:
                    f.write(f'  Content (first 500 chars): {resp_text[:500]}...\n')
                    f.write(f'  ... (total {len(resp_text)} chars)\n')
                else:
                    for rline in response_lines[1:]:
                        f.write(f'  {rline}\n')

            if history_lines:
                for hline in history_lines:
                    f.write(f'  {hline}\n')

        f.write('\n' + '='*60 + '\n')

    return len(blocks) - 1


async def run_single_bridge_test(api, scenario_cfg, bridge_name, verbose=True):
    """运行单桥梁测试"""
    result = await run_bridge_predict_test(api, scenario_cfg, verbose=verbose, bridge_name=bridge_name)

    print()
    print('='*60)
    print(f'Errors: {result.errors}')
    print(f'Tool calls: {result.tool_calls}')
    print(f'Response length: {len(result.response_text)}')
    print(f'Done: {result.done}')
    print(f'Ground Truth Grade: {result.ground_truth_grade or "N/A"}级')
    print(f'Predicted Grade: {result.predicted_grade or "N/A"}级')
    print(f'Grade Score: {result.grade_score}/100')

    # 综合测试判定
    base_pass = not result.errors
    grade_pass = result.grade_score >= 70 if hasattr(result, 'grade_score') else True

    if base_pass and grade_pass:
        print('\n*** TEST PASSED ***')
    elif base_pass and not grade_pass:
        print('\n*** TEST PASSED WITH GRADE DEVIATION ***')
        print(f'    ⚠️ 预测评级偏差较大: {result.predicted_grade or "N/A"}级 vs 真实{result.ground_truth_grade or "N/A"}级')
        print(f'    评级得分: {result.grade_score}/100')
    else:
        print('\n*** TEST FAILED ***')
    print('='*60)

    return result


async def run_multi_bridge_test(api, scenario_cfg, bridges, verbose=True):
    """运行多桥梁批量测试"""
    multi_result = await run_all_bridges_test(api, scenario_cfg, verbose=verbose, bridges=bridges)
    summary = multi_result.get_summary()

    print()
    print('='*60)
    print('MULTI-BRIDGE BATCH TEST RESULTS')
    print('='*60)

    # 打印每座桥的结果
    for bridge_name, result in multi_result.bridge_results.items():
        status = "✅" if not result.errors else "❌"
        grade_info = ""
        if result.ground_truth_grade and result.predicted_grade:
            grade_info = f" | Grade: {result.predicted_grade}级 vs {result.ground_truth_grade}级 (score: {result.grade_score})"
        elif result.ground_truth_grade:
            grade_info = f" | Grade: N/A vs {result.ground_truth_grade}级"
        print(f'{status} {bridge_name}{grade_info}')

    print()
    print(f'Total Bridges:     {summary["total_bridges"]}')
    print(f'Passed (no error): {summary["passed_bridges"]}/{summary["total_bridges"]}')
    print(f'Grade Matched:     {summary["grade_matched"]}/{summary["total_bridges"]}')
    print(f'Grade Adjacent:    {summary["grade_adjacent"]}/{summary["total_bridges"]}')
    print(f'Overall Accuracy: {summary["overall_accuracy_rate"]:.1f}%')
    print(f'Overall Grade Score: {summary["overall_grade_score"]:.1f}/100')

    # 综合测试判定
    overall_pass = summary["overall_accuracy_rate"] >= 75 and summary["overall_grade_score"] >= 70
    overall_marginal = summary["overall_accuracy_rate"] >= 50

    if overall_pass:
        print('\n*** BATCH TEST PASSED ***')
    elif overall_marginal:
        print('\n*** BATCH TEST MARGINAL ***')
        print('    ⚠️ 整体正确率可接受，但评级得分需提升')
    else:
        print('\n*** BATCH TEST FAILED ***')
    print('='*60)

    return multi_result


async def main():
    parser = argparse.ArgumentParser(description='Bridge Prediction Test Runner')
    parser.add_argument('--mode', '-m', choices=['single', 'multi', 'batch'], default='single',
                        help='Test mode: single (single bridge), multi/batch (all bridges)')
    parser.add_argument('--bridge', '-b', type=str, default='陈家阁大桥',
                        help='Bridge name for single mode (default: 陈家阁大桥)')
    parser.add_argument('--bridges', action='store_true',
                        help='Run all configured bridges (equivalent to --mode batch)')
    args = parser.parse_args()

    log_file = r'e:\PythonProject\agentb\WorkBranch\backend\llm_decision_trace.log'
    if os.path.exists(log_file):
        open(log_file, 'w').close()

    print('='*60)
    print('Starting backend server...')
    print('='*60)

    backend_process = start_backend()

    if not backend_process:
        print('❌ Failed to start backend')
        return

    await asyncio.sleep(3)

    config = load_config()
    api = APIClient(config)

    max_retries = 10
    for attempt in range(max_retries):
        try:
            health = await api._request('GET', '/health')
            if health.get('status') == 'ok':
                print(f'✅ Backend ready (attempt {attempt + 1})')
                break
        except:
            pass

        if attempt < max_retries - 1:
            print(f'⏳ Waiting for backend... ({attempt + 1}/{max_retries})')
            await asyncio.sleep(2)
    else:
        print('❌ Backend failed to start after retries')
        stop_backend(backend_process)
        return

    scenario_cfg = config.get('scenarios', {}).get('bridge_predict', {})
    scenario_cfg['prediction_timeout'] = 900.0

    # 根据模式运行测试
    if args.mode == 'multi' or args.mode == 'batch' or args.bridges:
        # 多桥梁批量测试
        bridges = list(BRIDGE_CONFIGS.keys())
        print(f'\nRunning batch test for {len(bridges)} bridges: {", ".join(bridges)}\n')
        result = await run_multi_bridge_test(api, scenario_cfg, bridges, verbose=True)
    else:
        # 单桥梁测试
        result = await run_single_bridge_test(api, scenario_cfg, args.bridge, verbose=True)

    merge_delta_logs(log_file)
    print(f'\n✅ 日志已合并: {log_file}')

    response_count = _save_llm_responses(log_file)
    summary_file = log_file.replace('.log', '_responses_summary.txt')
    print(f'📝 LLM决策记录: {response_count}次 (详见 {summary_file})')

    print('\nStopping backend...')
    stop_backend(backend_process)

asyncio.run(main())