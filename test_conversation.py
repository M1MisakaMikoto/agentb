import asyncio
import json
import httpx
from jose import jwt

BASE_URL = "http://127.0.0.1:8000"

def create_test_token(user_id: int = 1, user_name: str = "test_user"):
    payload = {
        "id": user_id,
        "name": user_name
    }
    return jwt.encode(payload, "none", algorithm="HS256")

async def test_conversation():
    token = create_test_token()
    headers = {"Authorization": token}
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        print("=" * 50)
        print("Step 1: Create a session")
        print("=" * 50)
        
        resp = await client.post(
            f"{BASE_URL}/session/sessions",
            json={"title": "Test Session"},
            headers=headers
        )
        print(f"Status: {resp.status_code}")
        session_data = resp.json()
        print(f"Response: {json.dumps(session_data, indent=2, ensure_ascii=False)}")
        session_id = session_data["data"]["id"]
        print(f"Session ID: {session_id}")
        
        print("\n" + "=" * 50)
        print("Step 2: Create a conversation")
        print("=" * 50)
        
        resp = await client.post(
            f"{BASE_URL}/session/sessions/{session_id}/conversations",
            json={"user_content": "Hello, please introduce yourself briefly."},
            headers=headers
        )
        print(f"Status: {resp.status_code}")
        conv_data = resp.json()
        print(f"Response: {json.dumps(conv_data, indent=2, ensure_ascii=False)}")
        conversation_id = conv_data["data"]["conversation_id"]
        print(f"Conversation ID: {conversation_id}")
        
        print("\n" + "=" * 50)
        print("Step 3: Send message (stream)")
        print("=" * 50)
        
        all_messages = []
        try:
            async with client.stream(
                "POST",
                f"{BASE_URL}/session/conversations/{conversation_id}/messages/stream",
                headers=headers,
                timeout=120.0
            ) as response:
                print(f"Status: {response.status_code}")
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        try:
                            data = json.loads(data_str)
                            all_messages.append(data)
                            msg_type = data.get("type", "")
                            content = data.get("content", "")
                            
                            if msg_type == "done":
                                print(f"\n[Received DONE signal]")
                                break
                            elif msg_type == "error":
                                print(f"\n[Error: {data.get('content')}]")
                                break
                            elif msg_type in ["text_delta", "text_start", "text_end"]:
                                if content:
                                    print(content, end="", flush=True)
                            else:
                                print(f"\n[{msg_type}]: {content[:50] if content else ''}")
                        except json.JSONDecodeError:
                            print(f"\n[Invalid JSON]: {data_str[:100]}")
        except Exception as e:
            print(f"Stream error: {e}")
            import traceback
            traceback.print_exc()
        
        print(f"\n\nTotal messages received: {len(all_messages)}")
        
        print("\n" + "=" * 50)
        print("Step 4: Check conversation in database")
        print("=" * 50)
        
        try:
            resp = await client.get(
                f"{BASE_URL}/session/conversations/{conversation_id}",
                headers=headers,
                timeout=30.0
            )
            print(f"Status: {resp.status_code}")
            conv_detail = resp.json()
            
            data = conv_detail.get("data", {})
            state = data.get("state")
            assistant_content = data.get("assistant_content")
            
            print(f"State: {state}")
            print(f"Assistant content length: {len(assistant_content) if assistant_content else 0}")
            
            if assistant_content:
                try:
                    messages = json.loads(assistant_content)
                    print(f"Messages count in DB: {len(messages)}")
                    if messages:
                        print("\nFirst message:")
                        print(json.dumps(messages[0], indent=2, ensure_ascii=False))
                except json.JSONDecodeError:
                    print("Assistant content is not valid JSON")
                    print(f"Preview: {assistant_content[:500]}")
            
            print("\n" + "=" * 50)
            print("Test Result")
            print("=" * 50)
            
            if state == "completed" and assistant_content and len(json.loads(assistant_content)) > 0:
                print("✅ SUCCESS: Conversation completed and content saved to database!")
            elif state == "completed" and (not assistant_content or len(json.loads(assistant_content)) == 0):
                print("❌ FAILED: Conversation completed but NO content saved to database!")
            else:
                print(f"⚠️ Conversation state: {state}")
        except Exception as e:
            print(f"Error checking conversation: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_conversation())
