#!/usr/bin/env python3
"""
测试 LLM 服务
"""

import sys
from pathlib import Path

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
            print(f"    Chunk: {chunk}")
        
        print(f"\n    Full response: {response[:200]}")
        
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
