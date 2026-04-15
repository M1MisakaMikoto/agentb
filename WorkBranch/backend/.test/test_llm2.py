#!/usr/bin/env python3
"""
测试 LLM 服务 - 绕过控制台编码问题
"""

import sys
import os
from pathlib import Path

os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).parent.parent))


def main():
    from singleton import get_llm_service

    print("LLM Service Test")
    print("=" * 60)

    llm = get_llm_service()
    
    print("\n[1] Testing LLM service...")
    
    messages = [{"role": "user", "content": "Hello, please reply briefly"}]
    
    try:
        print("    Sending: Hello, please reply briefly")
        
        response = ""
        for chunk in llm.chat_stream(messages, "You are a friendly assistant. Please reply briefly."):
            response += chunk
        
        print(f"\n    Full response: {response[:200] if response else 'EMPTY'}")
        
        if response:
            print("\nLLM service test PASSED!")
            return True
        else:
            print("\nLLM service returned empty response")
            return False
            
    except Exception as e:
        print(f"\nLLM service test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    main()
