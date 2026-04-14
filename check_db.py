import asyncio
import aiomysql
import json

async def check_database():
    conn = await aiomysql.connect(
        host="localhost",
        port=3306,
        user="root",
        password="0502",
        db="agentb",
        charset="utf8mb4",
    )
    
    try:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("""
                SELECT id, session_id, state, 
                       LENGTH(assistant_content) as content_length,
                       LEFT(assistant_content, 200) as content_preview
                FROM conversations 
                ORDER BY created_at DESC 
                LIMIT 10
            """)
            rows = await cursor.fetchall()
            
            print("=" * 80)
            print("Recent conversations in database:")
            print("=" * 80)
            
            for row in rows:
                print(f"\nConversation ID: {row['id']}")
                print(f"Session ID: {row['session_id']}")
                print(f"State: {row['state']}")
                print(f"Assistant content length: {row['content_length']}")
                if row['content_preview']:
                    print(f"Content preview: {row['content_preview'][:200]}...")
                    
            print("\n" + "=" * 80)
            print("Summary:")
            print("=" * 80)
            
            completed_with_content = [r for r in rows if r['state'] == 'completed' and r['content_length'] > 0]
            completed_without_content = [r for r in rows if r['state'] == 'completed' and (r['content_length'] is None or r['content_length'] == 0)]
            failed_with_content = [r for r in rows if r['state'] == 'failed' and r['content_length'] > 0]
            failed_without_content = [r for r in rows if r['state'] == 'failed' and (r['content_length'] is None or r['content_length'] == 0)]
            
            print(f"Completed with content: {len(completed_with_content)}")
            print(f"Completed WITHOUT content: {len(completed_without_content)}")
            print(f"Failed with content: {len(failed_with_content)}")
            print(f"Failed WITHOUT content: {len(failed_without_content)}")
            
            if completed_without_content:
                print("\n⚠️ Found completed conversations WITHOUT content:")
                for r in completed_without_content:
                    print(f"  - {r['id']}")
            
            if failed_without_content:
                print("\n⚠️ Found failed conversations WITHOUT content:")
                for r in failed_without_content:
                    print(f"  - {r['id']}")
            
    finally:
        conn.close()

if __name__ == "__main__":
    asyncio.run(check_database())
