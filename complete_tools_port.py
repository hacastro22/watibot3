#!/usr/bin/env python3
"""
Complete Tools Port Script
Copies all tool definitions from openai_agent.py to vertex_agent.py
"""

import re

# Read openai_agent.py to extract tools
with open('/home/robin/watibot3/app/openai_agent.py', 'r') as f:
    openai_content = f.read()

# Extract tools array from openai_agent.py
tools_start = openai_content.find('tools = [')
tools_end = openai_content.find(']', tools_start) + 1
tools_section = openai_content[tools_start:tools_end]

print("Found tools section with", len(re.findall(r'"name":', tools_section)), "tools")

# Read current vertex_agent.py
with open('/home/robin/watibot3/app/vertex_agent.py', 'r') as f:
    vertex_content = f.read()

# Replace the TOOLS section in vertex_agent.py
old_tools_start = vertex_content.find('# Tool definitions')
old_tools_end = vertex_content.find(']', vertex_content.find('TOOLS = [')) + 1

new_vertex_content = (
    vertex_content[:old_tools_start] + 
    '# Tool definitions - complete port from openai_agent.py\n' +
    tools_section.replace('tools = [', 'TOOLS = [') +
    vertex_content[old_tools_end:]
)

# Write updated vertex_agent.py
with open('/home/robin/watibot3/app/vertex_agent.py', 'w') as f:
    f.write(new_vertex_content)

print("✓ Successfully ported all tools from openai_agent.py to vertex_agent.py")
print("✓ Total tools ported:", len(re.findall(r'"name":', tools_section)))
