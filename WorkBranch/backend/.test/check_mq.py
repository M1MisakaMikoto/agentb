import sys
sys.path.insert(0, 'e:/PythonProject/agentb/WorkBranch/backend')

from singleton import get_message_queue, get_agent_service

mq1 = get_message_queue()
agent = get_agent_service()
mq2 = agent._get_message_queue()

print(f'MQ1 id: {id(mq1)}')
print(f'MQ2 id: {id(mq2)}')
print(f'Same: {mq1 is mq2}')
