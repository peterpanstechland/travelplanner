#!/usr/bin/env python
"""
MCP Client for Amap API

This client script uses the MCPClient class to interact with the Amap MCP server.
"""

import asyncio
import sys
import json
import time
import random
import re
from typing import Optional, List, Dict, Any
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from anthropic import Anthropic, RateLimitError, APIError
from dotenv import load_dotenv
import os

load_dotenv()  # load environment variables from .env

class MCPClient:
    def __init__(self):
        # Initialize session and client objects
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()
        self.anthropic = Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))
        
        # Rate limiting parameters
        self.last_api_call_time = 0
        self.min_delay_between_calls = 2  # seconds
        self.max_retries = 5
        self.backoff_factor = 1.5
        
        # Cache for tool calls to reduce duplicate API calls
        self.tool_cache = {}
        self.cache_ttl = 3600  # Cache valid for 1 hour
        
        # Tracking information state for early termination
        self.info_state = {
            "has_location_info": False,
            "has_route_info": False, 
            "has_weather_info": False,
            "has_poi_info": False
        }
        
        # 会话记忆 - 存储上下文信息
        self.memory = {
            "current_locations": {},  # 当前会话涉及的位置信息
            "current_pois": [],       # 当前会话涉及的POI信息
            "current_plans": {},      # 当前会话的行程计划
            "last_query": "",         # 上一次查询内容
            "query_count": 0,         # 查询计数
            "conversation_history": [] # 简化的对话历史
        }
        
        # 路线规划细节模板
        self.route_template = {
            "高速路线": [],  # 经过的主要高速
            "收费站": [],    # 经过的主要收费站
            "服务区": [],    # 推荐的服务区
            "景点": [],     # 路线周边的景点
            "美食": []      # 路线周边的美食
        }
        
        # Define system prompt for Claude - optimized for token efficiency
        self.system_prompt = """你是高级旅行助手，利用高德地图API提供精准的旅行和地理信息。
使用工具查询位置、路线、天气和POI，保持多轮对话的连贯性和信息关联。

你的回答风格:
- 专业且亲切，像一位经验丰富的旅行顾问
- 信息详尽且结构清晰，使用适当的emoji和格式增强可读性
- 根据用户需求灵活调整详细程度
- 提供个性化的建议，而非简单的事实陈述

对于行程规划，应提供:
1. 详细的路线描述（经过的主要道路、收费站、交通枢纽）
2. 合理的时间安排（考虑交通状况、用餐时间、景点游览时长）
3. 景点和餐饮推荐（结合当地特色和用户偏好）
4. 费用估算（交通费、门票、餐饮等）
5. 针对性建议（季节性因素、临时活动、特殊准备）

在处理多轮查询时，应积极利用历史对话收集的信息，为用户创造连贯且高效的旅行规划体验。"""

    async def connect_to_server(self, server_script_path: str):
        """Connect to an MCP server
        
        Args:
            server_script_path: Path to the server script (.py or .js)
        """
        is_python = server_script_path.endswith('.py')
        is_js = server_script_path.endswith('.js')
        if not (is_python or is_js):
            raise ValueError("Server script must be a .py or .js file")
            
        command = "python" if is_python else "node"
        server_params = StdioServerParameters(
            command=command,
            args=[server_script_path],
            env=None
        )
        
        stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
        self.stdio, self.write = stdio_transport
        self.session = await self.exit_stack.enter_async_context(ClientSession(self.stdio, self.write))
        
        await self.session.initialize()
        
        # List available tools
        response = await self.session.list_tools()
        tools = response.tools
        print("\nConnected to server with tools:", [tool.name for tool in tools])

    def compress_messages(self, messages, max_tokens=8000):
        """压缩会话历史以减少token消耗，保持工具调用对应关系"""
        # 如果消息数量不多，无需压缩
        if len(messages) <= 5:
            return messages
            
        # 估算当前token数
        curr_tokens = sum(len(json.dumps(m, ensure_ascii=False)) // 4 for m in messages)
        if curr_tokens <= max_tokens:
            return messages
            
        print(f"压缩会话历史: 从约{curr_tokens}tokens减少到{max_tokens}以内")
        
        # 提取工具调用和结果的关系，记录哪些需要保留
        tool_use_ids = {}
        tool_result_indices = {}
        
        # 第一遍扫描：找出所有工具调用ID
        for i, msg in enumerate(messages):
            # 查找assistant消息中的工具调用
            if msg["role"] == "assistant" and isinstance(msg["content"], list):
                for item in msg["content"]:
                    if item.get("type") == "tool_use":
                        tool_id = item.get("id")
                        if tool_id:
                            tool_use_ids[tool_id] = i
                            
            # 查找user消息中的工具结果
            if msg["role"] == "user" and isinstance(msg["content"], list) and msg["content"]:
                for item in msg["content"]:
                    if item.get("type") == "tool_result":
                        tool_id = item.get("tool_use_id")
                        if tool_id:
                            tool_result_indices[i] = tool_id
        
        # 确定必须保留的消息索引
        must_keep_indices = set()
        
        # 添加第一条用户消息
        must_keep_indices.add(0)
        
        # 添加最近的消息
        recent_count = min(5, len(messages) // 2)
        for i in range(max(0, len(messages) - recent_count), len(messages)):
            must_keep_indices.add(i)
            
        # 汇总提取的信息
        info_summary = []
        
        # 处理不需要保留的消息
        simplified_messages = []
        
        for i, msg in enumerate(messages):
            # 必须保留的消息
            if i in must_keep_indices:
                simplified_messages.append(msg)
                continue
                
            # 需要保留的工具调用
            tool_id_to_keep = None
            for tool_id, idx in tool_use_ids.items():
                if idx == i:
                    tool_id_to_keep = tool_id
                    break
                    
            # 需要保留的工具结果
            result_id_to_keep = tool_result_indices.get(i)
            
            # 如果是需要保留的工具调用或结果
            if tool_id_to_keep or result_id_to_keep:
                simplified_messages.append(msg)
            # 否则提取信息加入摘要
            elif msg["role"] == "assistant" and isinstance(msg["content"], str):
                # 提取助手文本中的关键信息
                text = msg["content"]
                # 只保留信息性语句，忽略过程性描述
                if "查询" in text or "结果" in text or "信息" in text:
                    key_points = self._extract_key_points(text)
                    if key_points:
                        info_summary.extend(key_points)
        
        # 如果我们有提取的信息，添加一个总结消息
        if info_summary:
            summary_msg = {
                "role": "user",
                "content": "根据已收集的信息，我们知道：\n• " + "\n• ".join(info_summary)
            }
            # 插入到第二个位置（第一条之后）
            simplified_messages.insert(1, summary_msg)
            
        return simplified_messages
    
    def _extract_key_points(self, text):
        """从文本中提取关键信息点"""
        key_points = []
        
        # 尝试提取位置信息
        location_patterns = [
            r'位置[:：是在]+([\u4e00-\u9fa5a-zA-Z0-9]+)',
            r'地址[:：是在]+([\u4e00-\u9fa5a-zA-Z0-9]+)',
            r'([\u4e00-\u9fa5]+)位于([\u4e00-\u9fa5a-zA-Z0-9]+)'
        ]
        
        for pattern in location_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                if isinstance(match, tuple):
                    key_points.append(f"{match[0]}位于{match[1]}")
                else:
                    key_points.append(f"位置: {match}")
        
        # 提取天气信息
        weather_patterns = [
            r'天气[:：是为]+([\u4e00-\u9fa5]+)',
            r'气温[:：是为]+([\u4e00-\u9fa5\d-~]+度)'
        ]
        
        for pattern in weather_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                key_points.append(f"天气: {match}")
        
        # 提取路线信息
        route_patterns = [
            r'距离[:：是约为]+([\d\.]+公里)',
            r'时间[:：需要约为]+([\d\.]+小时)',
            r'费用[:：是约为]+([\d\.]+元)'
        ]
        
        for pattern in route_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                key_points.append(f"路线: {match}")
                
        return key_points

    def _extract_location_from_result(self, content):
        """从地理编码结果中提取位置信息"""
        if isinstance(content, str):
            try:
                # 尝试解析JSON字符串
                content = json.loads(content)
            except:
                pass
                
        if isinstance(content, dict):
            # 高德地图地理编码结果格式
            if "geocodes" in content and content["geocodes"]:
                geocode = content["geocodes"][0]
                return f"{geocode.get('formatted_address', '')} ({geocode.get('location', '')})"
            elif "regeocode" in content:
                regeo = content["regeocode"]
                return f"{regeo.get('formatted_address', '')}"
        
        # 如果无法解析，返回简短摘要
        content_str = str(content)
        if len(content_str) > 100:
            return content_str[:100] + "..."
        return content_str
        
    def _extract_route_from_result(self, content):
        """从路线规划结果中提取路线信息"""
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except:
                pass
                
        if isinstance(content, dict):
            # 提取路线距离和时间
            if "route" in content and "paths" in content["route"] and content["route"]["paths"]:
                path = content["route"]["paths"][0]
                distance = path.get("distance", "未知")
                duration = path.get("duration", "未知")
                return f"距离:{distance}米, 时间:{duration}秒"
        
        # 默认返回
        content_str = str(content)
        if len(content_str) > 100:
            return content_str[:100] + "..."
        return content_str
        
    def _extract_weather_from_result(self, content):
        """从天气结果中提取天气信息"""
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except:
                pass
                
        if isinstance(content, dict):
            if "forecasts" in content and content["forecasts"]:
                forecast = content["forecasts"][0]
                city = forecast.get("city", "未知城市") 
                if "casts" in forecast and forecast["casts"]:
                    cast = forecast["casts"][0]
                    return f"{city}: {cast.get('dayweather', '未知')}，温度{cast.get('daytemp', '未知')}°C"
        
        # 默认返回
        content_str = str(content)
        if len(content_str) > 100:
            return content_str[:100] + "..."
        return content_str
        
    def _extract_poi_from_result(self, content):
        """从POI搜索结果中提取信息"""
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except:
                pass
                
        if isinstance(content, dict):
            if "pois" in content and content["pois"]:
                pois = content["pois"][:3]  # 只取前3个
                names = [p.get("name", "未知") for p in pois]
                return f"找到: {', '.join(names)}"
        
        # 默认返回
        content_str = str(content)
        if len(content_str) > 100:
            return content_str[:100] + "..."
        return content_str

    async def cached_tool_call(self, tool_name, tool_args):
        """带缓存的工具调用，减少重复请求"""
        # 生成缓存键
        cache_key = f"{tool_name}:{json.dumps(tool_args, sort_keys=True)}"
        
        # 检查缓存
        now = time.time()
        if cache_key in self.tool_cache:
            cached_result, timestamp = self.tool_cache[cache_key]
            # 检查缓存是否过期
            if now - timestamp < self.cache_ttl:
                print(f"使用缓存结果: {tool_name}")
                return cached_result
        
        # 没有缓存或缓存过期，调用工具
        result = await self.session.call_tool(tool_name, tool_args)
        
        # 更新缓存
        self.tool_cache[cache_key] = (result, now)
        return result

    async def call_claude_with_retry(self, messages, tools=None, max_tokens=1500):
        """Call Claude API with retry logic for rate limiting"""
        # 压缩消息减少token使用
        compressed_messages = self.compress_messages(messages)
        if len(compressed_messages) < len(messages):
            print(f"消息历史已压缩: {len(messages)} -> {len(compressed_messages)}")
            
        # Enforce minimum delay between API calls
        now = time.time()
        time_since_last_call = now - self.last_api_call_time
        if time_since_last_call < self.min_delay_between_calls:
            delay = self.min_delay_between_calls - time_since_last_call
            print(f"Rate limiting: Waiting {delay:.2f}s before next API call...")
            await asyncio.sleep(delay)
        
        # Try API call with exponential backoff for rate limit errors
        retries = 0
        while retries <= self.max_retries:
            try:
                response = self.anthropic.messages.create(
                    model="claude-3-5-sonnet-20241022",
                    max_tokens=max_tokens,
                    system=self.system_prompt,
                    messages=compressed_messages,
                    tools=tools,
                    temperature=0.2
                )
                self.last_api_call_time = time.time()
                return response
            except RateLimitError as e:
                retries += 1
                if retries > self.max_retries:
                    raise e
                
                # Calculate backoff time (with jitter to prevent thundering herd)
                backoff_time = (self.backoff_factor ** retries) * (1 + random.random() * 0.1)
                print(f"Rate limit exceeded. Retrying in {backoff_time:.1f} seconds... (Attempt {retries}/{self.max_retries})")
                await asyncio.sleep(backoff_time)
            except APIError as e:
                # Handle other API errors
                if "rate_limit" in str(e).lower():
                    # Treat as rate limit error
                    retries += 1
                    if retries > self.max_retries:
                        raise e
                    backoff_time = (self.backoff_factor ** retries) * (1 + random.random() * 0.1) 
                    print(f"API error with rate limiting. Retrying in {backoff_time:.1f} seconds... (Attempt {retries}/{self.max_retries})")
                    await asyncio.sleep(backoff_time)
                else:
                    # Other API error, re-raise
                    raise e

    async def process_query(self, query: str) -> str:
        """Process a query using Claude and available tools with multi-turn reasoning"""
        # Reset info state for new query
        self.info_state = {
            "has_location_info": False,
            "has_route_info": False, 
            "has_weather_info": False,
            "has_poi_info": False
        }
        
        # Get available tools for Claude
        response = await self.session.list_tools()
        available_tools = [{ 
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.inputSchema
        } for tool in response.tools]

        # 获取记忆提示
        memory_prompt = self.get_memory_prompt(query)
        
        # 创建初始查询，包含记忆提示
        initial_query = query
        if memory_prompt:
            initial_query = f"{memory_prompt}\n\n您的问题: {query}"
            print("\n使用记忆增强的查询:")
            print(initial_query)
        
        # Initialize conversation history
        messages = [
            {
                "role": "user",
                "content": initial_query
            }
        ]

        # 用于收集所有工具调用结果
        all_tool_results = []

        # Output to collect all results and reasoning
        output_parts = []
        
        # Maximum number of tool calling iterations - reduced for efficiency
        max_iterations = 4  # 减少最大迭代次数
        min_delay_between_iterations = 3  # seconds - increased from 1
        current_iteration = 0
        reached_final_answer = False
        consecutive_text_responses = 0  # Track consecutive text responses without tool calls
        
        while current_iteration < max_iterations and not reached_final_answer:
            current_iteration += 1
            print(f"\nIteration {current_iteration}/{max_iterations}")
            
            try:
                # Token allocation strategy: optimize token usage per iteration
                if current_iteration == 1:
                    # First iteration - give more tokens for planning
                    tokens_for_iteration = 700
                    
                    # 在第一次迭代中，检查是否有足够的记忆上下文解决查询
                    if self.memory["query_count"] > 0:
                        # 如果已经有足够的记忆可能不需要额外查询
                        has_relevant_location = False
                        has_relevant_plan = False
                        
                        # 检查查询中是否包含记忆中已有的位置
                        for loc_name in self.memory["current_locations"].keys():
                            if loc_name in query:
                                has_relevant_location = True
                                break
                        
                        # 检查查询中是否涉及记忆中已有的路线
                        origin, destination = self._extract_route_endpoints(query)
                        if origin and destination:
                            route_key = f"{origin}-{destination}"
                            if route_key in self.memory["current_plans"]:
                                has_relevant_plan = True
                        
                        # 如果已有相关信息，给Claude更多的token来利用记忆
                        if has_relevant_location or has_relevant_plan:
                            print("检测到相关记忆，优化查询流程...")
                            tokens_for_iteration = 1000
                elif current_iteration < max_iterations - 1:
                    # Middle iterations - use minimum tokens needed
                    tokens_for_iteration = 500
                else:
                    # Last iteration - give more tokens for concluding
                    tokens_for_iteration = 900
                
                # Call Claude API with current conversation history and retry logic
                response = await self.call_claude_with_retry(
                    messages=messages,
                    tools=available_tools,
                    max_tokens=tokens_for_iteration
                )
                
                # Check if response contains any content
                if not response.content:
                    print("Received empty response from Claude")
                    break
                
                # The response we'll add to the conversation history
                assistant_message = {"role": "assistant", "content": []}
                
                # Track if this response contains any tool calls
                has_tool_calls = False
                tool_calls_to_process = []
                
                # Process each content block in the response
                for content in response.content:
                    # Handle text content
                    if content.type == 'text':
                        text = content.text.strip()
                        
                        # Check if this is likely a final answer (relatively long text with completion)
                        if len(text) > 100 and any(phrase in text.lower() for phrase in ["总结", "小结", "总的来说", "综上所述", "最后", "建议", "方案"]):
                            reached_final_answer = True
                            print("Detected final answer content in text")
                        
                        # Add to output if non-empty
                        if text:
                            # Format thinking sections
                            if text.startswith("思考:") or text.startswith("思考："):
                                thinking_text = f"\nThought for {len(text) // 20} seconds\n{text}"
                                output_parts.append(thinking_text)
                                print(thinking_text)
                            else:
                                output_parts.append(text)
                                print(text)
                            
                            # Add text to the assistant's message content
                            assistant_message["content"].append({"type": "text", "text": text})
                    
                    # Handle tool calls
                    elif content.type == 'tool_use':
                        has_tool_calls = True
                        consecutive_text_responses = 0  # Reset counter when tool is called
                        
                        # Save tool call for processing
                        tool_calls_to_process.append({
                            "tool_id": content.id,
                            "tool_name": content.name,
                            "tool_args": content.input
                        })
                        
                        # Add tool call to the assistant's message content
                        assistant_message["content"].append({
                            "type": "tool_use",
                            "id": content.id,
                            "name": content.name,
                            "input": content.input
                        })
                
                # Add assistant message to conversation history if it has content
                if assistant_message["content"]:
                    messages.append(assistant_message)
                
                # Process any tool calls and add their results to the conversation
                for tool_call in tool_calls_to_process:
                    tool_id = tool_call["tool_id"]
                    tool_name = tool_call["tool_name"]
                    tool_args = tool_call["tool_args"]
                    
                    # Format and log the tool call
                    tool_call_message = f"\n▼ Called MCP tool  {tool_name} ▼"
                    params_formatted = json.dumps(tool_args, ensure_ascii=False, indent=2)
                    tool_params = f"Parameters:\n{params_formatted}"
                    
                    print(tool_call_message)
                    print(tool_params)
                    
                    output_parts.append(tool_call_message)
                    output_parts.append(tool_params)
                    
                    # Execute the tool call
                    start_time = time.time()
                    try:
                        # 使用带缓存的工具调用
                        result = await self.cached_tool_call(tool_name, tool_args)
                        result_content = result.content
                        
                        # Process the result content to ensure it's serializable
                        raw_result_content = self.process_tool_result(result_content)
                        
                        # 添加到所有工具调用结果列表中，用于更新记忆
                        all_tool_results.append(raw_result_content)
                        
                        # 进一步精简工具结果，仅保留关键信息 - 这个版本会存入历史
                        simplified_result = self.process_tool_result(result_content)
                        
                        # Update info state based on tool type
                        if tool_name in ["maps_geo", "maps_regeocode"]:
                            self.info_state["has_location_info"] = True
                        elif "direction" in tool_name:
                            self.info_state["has_route_info"] = True
                        elif tool_name == "maps_weather":
                            self.info_state["has_weather_info"] = True
                        elif "search" in tool_name:
                            self.info_state["has_poi_info"] = True
                        
                        # Format result for display - 使用完整结果显示给用户
                        if isinstance(raw_result_content, dict):
                            result_text = json.dumps(raw_result_content, ensure_ascii=False, indent=2)
                        else:
                            result_text = str(raw_result_content)
                        
                        # More aggressive truncation for intermediate results
                        if len(result_text) > 600:  # Reduced from 800
                            result_text = result_text[:600] + "... [truncated]"
                        
                        elapsed = time.time() - start_time
                        print(f"Result (took {elapsed:.2f}s):")
                        print(result_text)
                        
                        tool_result = f"Result:\n{result_text}"
                        output_parts.append(tool_result)
                        
                        # Add tool result to conversation history
                        # IMPORTANT: We must make sure each tool_use has a corresponding tool_result
                        # AND the content must be a string, not an object (Claude API requirement)
                        messages.append({
                            "role": "user", 
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tool_id,
                                    "content": json.dumps(simplified_result) if isinstance(simplified_result, (dict, list)) else str(simplified_result)
                                }
                            ]
                        })
                    
                    except Exception as e:
                        error_message = f"Error calling tool {tool_name}: {str(e)}"
                        print(error_message)
                        output_parts.append(f"Error: {error_message}")
                        
                        # Add error result to conversation history
                        messages.append({
                            "role": "user", 
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tool_id,
                                    "content": f"Error: {error_message}"
                                }
                            ]
                        })
                
                # Track consecutive text responses without tool calls
                if not has_tool_calls:
                    consecutive_text_responses += 1
                    if consecutive_text_responses >= 2:
                        # If Claude gives two consecutive responses without tool calls, assume we're done
                        reached_final_answer = True
                        print("Detected two consecutive text-only responses, assuming reasoning chain is complete")
                
                # Early termination based on information state
                if current_iteration >= 2:
                    # Check if we've gathered enough key information
                    has_enough_info = False
                    
                    # For travel-related queries, check if we have location and route info
                    if ("travel" in query.lower() or "route" in query.lower() or 
                        "旅行" in query or "路线" in query or "怎么走" in query):
                        if self.info_state["has_location_info"] and self.info_state["has_route_info"]:
                            has_enough_info = True
                            print("已获取足够的位置和路线信息，可以生成回答")
                    
                    # For weather-related queries
                    elif "weather" in query.lower() or "天气" in query:
                        if self.info_state["has_location_info"] and self.info_state["has_weather_info"]:
                            has_enough_info = True
                            print("已获取足够的位置和天气信息，可以生成回答")
                    
                    # For POI search
                    elif "找" in query or "search" in query.lower() or "查询" in query:
                        if self.info_state["has_poi_info"]:
                            has_enough_info = True
                            print("已获取足够的POI信息，可以生成回答")
                    
                    if has_enough_info:
                        reached_final_answer = True
                
                # If we have a final answer, break the loop
                if reached_final_answer:
                    break
                
                # Add a longer delay between iterations to help with rate limiting
                if current_iteration < max_iterations:
                    wait_time = min_delay_between_iterations * (current_iteration / 2)
                    print(f"Waiting {wait_time:.1f}s before next iteration...")
                    await asyncio.sleep(wait_time)
                
            except Exception as e:
                print(f"Error in iteration {current_iteration}: {str(e)}")
                import traceback
                traceback.print_exc()
                break
        
        # 生成最终答案 - 使用Claude而非本地函数
        print("\n生成最终综合答案...")
        final_answer = ""
        
        # 确保等待一下，以遵守API速率限制
        await asyncio.sleep(3)
        
        try:
            # 准备生成最终答案的提示
            memory_context = self.get_memory_context() # 获取记忆上下文
            route_context = self.get_route_context(query) # 获取路线上下文
            
            # 确保会话历史有效 - 检查工具调用和结果的匹配
            valid_messages = self.validate_and_fix_messages(messages)
            
            # 添加一个明确的总结请求
            final_prompt = f"""请基于已收集的信息，提供详细且有结构的最终答案。

{memory_context}

{route_context}

请提供一个美观、易读且全面的回答，内容需包含：
1. 查询主要信息的明确总结
2. 相关的时间、距离、费用详情(如适用)
3. 行程路线要点，主要道路和注意事项
4. 个性化的景点和餐饮推荐，以及特色体验建议
5. 考虑天气、交通状况和季节特点的实用旅行建议

格式要求:
- 使用emoji增强可读性
- 使用分隔线或标题区分不同内容块
- 为重要信息添加简单强调
- 确保整体组织清晰，便于用户快速获取关键信息

使回答既专业又亲切，像一位经验丰富的旅行顾问给出的建议。请确保回答是完整、准确且有帮助的。"""

            valid_messages.append({
                "role": "user",
                "content": final_prompt
            })
            
            # 给Claude较多的token来生成完整回答
            final_response = await self.call_claude_with_retry(
                messages=valid_messages,
                tools=[],  # 不需要工具调用能力
                max_tokens=1500
            )
            
            if final_response.content and final_response.content[0].type == 'text':
                final_answer = final_response.content[0].text.strip()
                
                # 替换或添加到输出中
                if output_parts and any(part.startswith("\nThought for") for part in output_parts[-3:]):
                    # 找到最后一个思考部分并替换它之后的所有内容
                    for i in range(len(output_parts)-1, -1, -1):
                        if output_parts[i].startswith("\nThought for"):
                            output_parts = output_parts[:i+1]
                            break
                
                # 添加最终答案
                output_parts.append("\n" + "="*50)
                output_parts.append("最终回答:")
                output_parts.append(final_answer)
            
        except Exception as e:
            # 如果Claude生成最终答案失败，回退到本地生成的摘要
            print(f"生成最终答案时出错: {str(e)}")
            print("使用本地生成的摘要作为备选...")
            
            local_summary = self.generate_local_summary(query, messages)
            output_parts.append("\n" + "="*50)
            output_parts.append("摘要 (本地生成):")
            output_parts.append(local_summary)
            final_answer = local_summary
        
        # 更新会话记忆
        self.update_memory(query, all_tool_results, final_answer)
        
        # 返回最终结果
        return "\n".join(output_parts)

    def validate_and_fix_messages(self, messages):
        """Validate and fix messages to ensure each tool_use has a matching tool_result"""
        valid_messages = []
        pending_tool_uses = {}  # Map of tool_id to its index in valid_messages
        
        for message in messages:
            role = message["role"]
            content = message["content"]
            
            # Text messages are always valid
            if isinstance(content, str):
                valid_messages.append(message)
                continue
                
            if role == "assistant":
                # Create a new message with validated content
                new_content = []
                new_message = {"role": "assistant", "content": new_content}
                
                # Process each content block
                for item in content:
                    if item["type"] == "text":
                        new_content.append(item)
                    elif item["type"] == "tool_use":
                        new_content.append(item)
                        # Remember this tool use needs a result
                        if len(valid_messages) > 0:  # We need to have at least one message before adding a tool use
                            pending_tool_uses[item["id"]] = len(valid_messages)
                
                # Only add if there's actual content
                if new_content:
                    valid_messages.append(new_message)
                    
            elif role == "user":
                # For user messages with tool results, check if they match pending tool uses
                if isinstance(content, list) and content and content[0]["type"] == "tool_result":
                    tool_result = content[0]
                    tool_use_id = tool_result["tool_use_id"]
                    
                    # Ensure tool_result content is a string
                    if isinstance(tool_result["content"], (dict, list)):
                        # Create a new message with stringified content
                        fixed_content = [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "content": json.dumps(tool_result["content"])
                            }
                        ]
                        fixed_message = {"role": "user", "content": fixed_content}
                        
                        # If this matches a pending tool use, add the fixed message
                        if tool_use_id in pending_tool_uses:
                            valid_messages.append(fixed_message)
                            # Remove from pending since we've handled it
                            del pending_tool_uses[tool_use_id]
                    else:
                        # If this matches a pending tool use, add it as is
                        if tool_use_id in pending_tool_uses:
                            valid_messages.append(message)
                            # Remove from pending since we've handled it
                            del pending_tool_uses[tool_use_id]
                else:
                    # Regular user message
                    valid_messages.append(message)
        
        # If any tool uses remain without results, remove them from the messages
        if pending_tool_uses:
            print(f"Warning: Found {len(pending_tool_uses)} tool uses without matching results.")
            
            # Create a new list with problem messages fixed or removed
            fixed_messages = []
            for i, message in enumerate(valid_messages):
                # Check if this index is in our problem list
                if i in pending_tool_uses.values():
                    # This message has tool uses without results
                    if isinstance(message["content"], list):
                        # Create a new message with only text content
                        new_content = [item for item in message["content"] if item["type"] == "text"]
                        if new_content:
                            fixed_messages.append({"role": message["role"], "content": new_content})
                else:
                    # This message is fine
                    fixed_messages.append(message)
            
            return fixed_messages
        
        return valid_messages

    def process_tool_result(self, result_content):
        """处理工具结果，提取关键信息并删除无用数据"""
        # 首先处理TextContent对象
        if hasattr(result_content, 'type') and hasattr(result_content, 'text'):
            text_content = result_content.text
            try:
                if text_content.strip().startswith('{') and text_content.strip().endswith('}'):
                    result_content = json.loads(text_content)
                else:
                    return text_content
            except:
                return text_content
                
        # 处理TextContent列表
        if isinstance(result_content, list) and len(result_content) > 0 and hasattr(result_content[0], 'type'):
            processed_items = []
            for item in result_content:
                if hasattr(item, 'text'):
                    processed_items.append(item.text)
                else:
                    processed_items.append(str(item))
            
            if len(processed_items) == 1 and processed_items[0].strip().startswith('{'):
                try:
                    result_content = json.loads(processed_items[0])
                except:
                    return processed_items[0]
            else:
                return processed_items[0] if len(processed_items) == 1 else processed_items
                
        # 如果现在是字典格式，开始精简数据
        if isinstance(result_content, dict):
            # 精简地理编码结果
            if "geocodes" in result_content:
                # 只保留第一个结果和关键字段
                if result_content["geocodes"] and len(result_content["geocodes"]) > 0:
                    geocode = result_content["geocodes"][0]
                    return {
                        "location": geocode.get("location", ""),
                        "formatted_address": geocode.get("formatted_address", ""),
                        "city": geocode.get("city", ""),
                        "district": geocode.get("district", "")
                    }
            
            # 精简逆地理编码结果
            if "regeocode" in result_content:
                regeo = result_content["regeocode"]
                return {
                    "formatted_address": regeo.get("formatted_address", ""),
                    "city": regeo.get("addressComponent", {}).get("city", ""),
                    "district": regeo.get("addressComponent", {}).get("district", "")
                }
                
            # 精简路线规划结果
            if "route" in result_content and "paths" in result_content["route"]:
                paths = result_content["route"]["paths"]
                if paths and len(paths) > 0:
                    path = paths[0]
                    # 只返回路线的关键信息
                    return {
                        "distance": path.get("distance", "0"),  # 路线总距离
                        "duration": path.get("duration", "0"),  # 预计时间(秒)
                        "tolls": path.get("tolls", "0"),        # 过路费
                        "strategy": path.get("strategy", "")    # 路线策略
                    }
            
            # 精简天气结果
            if "forecasts" in result_content:
                forecasts = result_content["forecasts"]
                if forecasts and len(forecasts) > 0:
                    forecast = forecasts[0]
                    casts = forecast.get("casts", [])
                    simplified_casts = []
                    # 只保留3天的天气预报
                    for i, cast in enumerate(casts[:3]):
                        simplified_casts.append({
                            "date": cast.get("date", ""),
                            "dayweather": cast.get("dayweather", ""),
                            "daytemp": cast.get("daytemp", ""),
                            "nighttemp": cast.get("nighttemp", "")
                        })
                    return {
                        "city": forecast.get("city", ""),
                        "casts": simplified_casts
                    }
            
            # 精简POI搜索结果
            if "pois" in result_content:
                pois = result_content["pois"]
                simplified_pois = []
                # 只保留前3个结果的关键信息
                for i, poi in enumerate(pois[:3]):
                    simplified_pois.append({
                        "name": poi.get("name", ""),
                        "address": poi.get("address", ""),
                        "location": poi.get("location", ""),
                        "type": poi.get("type", "")
                    })
                return {"pois": simplified_pois}
                
        # 返回原始内容（如果没有特殊处理）
        return result_content

    def generate_local_summary(self, query, messages):
        """当API调用失败时本地生成简单的总结"""
        summary_parts = ["以下是基于已收集信息的总结：\n"]
        
        # 提取所有工具调用的关键信息
        location_info = {}
        weather_info = {}
        route_info = {}
        poi_info = {}
        
        # 尝试从查询中提取起点和终点
        origin, destination = self._extract_route_endpoints(query)
        
        # 检查每条消息中的工具结果
        for msg in messages:
            if msg["role"] == "user" and isinstance(msg["content"], list) and msg["content"]:
                item = msg["content"][0]
                if item["type"] == "tool_result":
                    try:
                        # 尝试解析内容
                        content = item["content"]
                        if isinstance(content, str):
                            try:
                                content = json.loads(content)
                            except:
                                pass
                        
                        # 根据内容类型归类
                        if isinstance(content, dict):
                            # 位置信息
                            if "formatted_address" in content or "location" in content:
                                location_info = content
                            # 天气信息
                            elif "casts" in content:
                                weather_info = content
                            # 路线信息
                            elif "distance" in content and "duration" in content:
                                route_info = content
                            # POI信息
                            elif "pois" in content:
                                poi_info = content
                    except:
                        continue
        
        # 生成位置信息摘要
        if location_info:
            address = location_info.get("formatted_address", "")
            location = location_info.get("location", "")
            city = location_info.get("city", "")
            if address or location:
                summary_parts.append(f"📍 位置信息: {address} {location}")
                
        # 生成POI信息摘要
        if poi_info and "pois" in poi_info:
            pois = poi_info["pois"]
            if pois:
                poi_names = [p.get("name", "") for p in pois if p.get("name")]
                if poi_names:
                    summary_parts.append(f"🏢 找到的地点: {', '.join(poi_names)}")
                    if len(pois) > 0 and "address" in pois[0]:
                        summary_parts.append(f"   地址: {pois[0]['address']}")
                    if len(pois) > 0 and "location" in pois[0]:
                        summary_parts.append(f"   坐标: {pois[0]['location']}")
        
        # 生成天气信息摘要
        if weather_info and "casts" in weather_info:
            casts = weather_info["casts"]
            city = weather_info.get("city", "")
            if casts and len(casts) > 0:
                forecast = casts[0]
                date = forecast.get("date", "今天")
                day_weather = forecast.get("dayweather", "")
                day_temp = forecast.get("daytemp", "")
                night_temp = forecast.get("nighttemp", "")
                
                summary_parts.append(f"🌤️ 天气信息: {city} {date} {day_weather}")
                if day_temp or night_temp:
                    summary_parts.append(f"   温度: {day_temp}°C - {night_temp}°C")
        
        # 生成路线信息摘要
        if route_info:
            distance = route_info.get("distance", "")
            duration = route_info.get("duration", "")
            tolls = route_info.get("tolls", "")
            
            if distance:
                # 转换距离为公里
                distance_km = float(distance) / 1000 if distance.isdigit() else distance
                summary_parts.append(f"🚗 路线信息: 距离约 {distance_km} 公里")
            
            if duration:
                # 转换时间为小时和分钟
                if duration.isdigit():
                    duration_mins = int(duration) // 60
                    duration_hours = duration_mins // 60
                    duration_mins = duration_mins % 60
                    if duration_hours > 0:
                        summary_parts.append(f"   预计行驶时间: {duration_hours}小时{duration_mins}分钟")
                    else:
                        summary_parts.append(f"   预计行驶时间: {duration_mins}分钟")
                else:
                    summary_parts.append(f"   预计行驶时间: {duration}")
            
            if tolls:
                summary_parts.append(f"   过路费: 约{tolls}元")
                
            # 使用路线模板提供额外信息
            if origin and destination:
                # 尝试使用已有的路线模板
                if hasattr(self, 'route_template') and self.route_template:
                    summary_parts.append(f"\n🛣️ {origin}到{destination}路线详情:")
                    
                    # 添加高速路线信息
                    if self.route_template["高速路线"]:
                        highway_routes = ' → '.join(self.route_template["高速路线"])
                        summary_parts.append(f"   主要道路: {highway_routes}")
                    
                    # 添加收费站信息
                    if self.route_template["收费站"]:
                        toll_stations = '、'.join(self.route_template["收费站"])
                        summary_parts.append(f"   主要收费站: {toll_stations}")
                    
                    # 添加服务区信息
                    if self.route_template["服务区"]:
                        service_areas = '、'.join(self.route_template["服务区"])
                        summary_parts.append(f"   推荐服务区: {service_areas}")
        
        # 添加目的地景点和美食推荐
        if origin and destination:
            if hasattr(self, 'route_template') and self.route_template:
                # 添加景点信息
                if self.route_template["景点"]:
                    attractions = '、'.join(self.route_template["景点"])
                    summary_parts.append(f"\n🏞️ {destination}附近景点推荐: {attractions}")
                
                # 添加美食信息
                if self.route_template["美食"]:
                    foods = '、'.join(self.route_template["美食"])
                    summary_parts.append(f"🍲 {destination}特色美食: {foods}")
                    
        # 添加记忆中的相关信息
        related_locations = []
        for loc_name, loc_info in self.memory["current_locations"].items():
            # 如果查询中包含位置名称，或者是起点终点中的一个
            if loc_name in query or (origin and loc_name == origin) or (destination and loc_name == destination):
                related_locations.append(f"{loc_name}: {loc_info['address']}")
                
        if related_locations and not location_info:  # 只有在当前查询没返回位置信息时才添加
            summary_parts.append("\n📌 您之前查询过的相关位置:")
            for loc in related_locations:
                summary_parts.append(f"   - {loc}")
                
        # 添加地区特定的建议
        if origin and destination:
            summary_parts.append("\n💡 出行建议:")
            
            # 根据路线距离提供不同建议
            if route_info and "distance" in route_info:
                distance_num = float(route_info["distance"]) / 1000 if route_info["distance"].isdigit() else 0
                
                if distance_num > 300:
                    summary_parts.append("1. 长途驾驶建议每隔2小时休息一次，避免疲劳驾驶")
                    summary_parts.append("2. 出发前检查车况，确保轮胎、机油和冷却液等正常")
                    summary_parts.append("3. 准备充足的饮用水和零食，以及常用药品")
                elif distance_num > 100:
                    summary_parts.append("1. 中等距离行程，建议提前规划好休息点")
                    summary_parts.append("2. 途中可以在服务区短暂休息，补充能量")
                else:
                    summary_parts.append("1. 短途行程，建议避开早晚高峰期出行")
                    summary_parts.append("2. 提前查看目的地的停车场情况")
            
            # 根据目的地添加特定建议
            if "珠海" in destination:
                summary_parts.append("3. 珠海沿海地区风景优美，可以安排海滨游览")
                summary_parts.append("4. 珠海与澳门相邻，如有需要可考虑前往澳门游玩")
            elif "杭州" in destination:
                summary_parts.append("3. 杭州西湖景区游客较多，建议避开周末和节假日")
                summary_parts.append("4. 可以品尝杭帮菜，如西湖醋鱼、龙井虾仁等特色美食")
            elif "北京" in destination:
                summary_parts.append("3. 北京景点分布较广，建议合理规划行程")
                summary_parts.append("4. 故宫、长城等热门景点最好提前在线预约")
                
            # 根据天气添加建议
            if weather_info and "casts" in weather_info and weather_info["casts"]:
                day_weather = weather_info["casts"][0].get("dayweather", "")
                if "雨" in day_weather:
                    summary_parts.append("5. 目的地天气可能有雨，请携带雨具")
                elif "晴" in day_weather and ("夏" in query or "热" in day_weather):
                    summary_parts.append("5. 天气晴朗炎热，注意防晒补水")
        
        return "\n".join(summary_parts)

    async def chat_loop(self):
        """运行交互式对话循环"""
        # 美化的欢迎界面
        welcome_text = """
╭──────────────────────────────────────────────────╮
│                                                  │
│     🌟 旅行规划助手 - Travel Planner 🌟          │
│                                                  │
│  基于高德地图API和Claude的智能旅行规划工具       │
│                                                  │
│  • 输入您的旅行问题，获取专业规划和建议          │
│  • 支持路线规划、天气查询、景点搜索等功能        │
│  • 具有对话记忆功能，能够理解上下文              │
│                                                  │
│  特殊命令:                                       │
│   - memory: 查看当前记忆状态                     │
│   - reset memory: 重置记忆                       │
│   - quit: 退出程序                               │
│                                                  │
╰──────────────────────────────────────────────────╯
"""
        print(welcome_text)
        
        # 初始提示
        print("💬 您可以问我任何旅行相关的问题，如：")
        print(" • 深圳到珠海怎么走")
        print(" • 珠海明天天气怎么样")
        print(" • 杭州有哪些著名景点")
        print(" • 我想去上海旅游三天，请帮我规划行程")
        
        while True:
            try:
                # 使用彩色提示
                query = input("\n🔍 请输入您的问题: ").strip()
                
                if not query:
                    continue
                    
                if query.lower() == 'quit':
                    print("\n👋 感谢使用旅行规划助手，祝您旅途愉快！")
                    break
                
                if query.lower() == 'memory':
                    # 显示当前记忆状态
                    memory_prompt = self.get_memory_prompt("查看记忆")
                    if memory_prompt:
                        print("\n📚 当前系统记忆:")
                        print("─" * 50)
                        print(memory_prompt)
                        print("─" * 50)
                    else:
                        print("\n📭 当前系统记忆为空，尚未进行有效对话")
                    continue
                    
                if query.lower() == 'reset memory':
                    # 重置记忆
                    self.memory = {
                        "current_locations": {},
                        "current_pois": [],
                        "current_plans": {},
                        "last_query": "",
                        "query_count": 0,
                        "conversation_history": []
                    }
                    print("\n🔄 已重置系统记忆")
                    continue
                
                # 处理正常查询
                print("\n⏳ 正在处理您的问题，这可能需要一点时间...")
                
                # 添加进度指示
                processing_chars = "|/-\\"
                start_time = time.time()
                
                # 创建一个任务来处理查询
                task = asyncio.create_task(self.process_query(query))
                
                # 显示进度指示器，直到任务完成
                i = 0
                while not task.done():
                    elapsed = time.time() - start_time
                    print(f"\r⏳ 处理中 {processing_chars[i % len(processing_chars)]} ({elapsed:.1f}秒)", end="")
                    i += 1
                    await asyncio.sleep(0.2)
                
                # 获取结果
                response = await task
                
                # 清除进度指示
                print("\r" + " " * 40 + "\r", end="")
                
                # 仅显示最终回答部分，而不是整个处理过程
                print("\n" + "─"*50)
                
                # 从响应中提取最终回答部分
                if "最终回答:" in response:
                    answer_start = response.find("最终回答:")
                    final_answer = response[answer_start:]
                    print(final_answer)
                else:
                    print(response)
                    
                print("─"*50)
                    
            except Exception as e:
                import traceback
                print(f"\n❌ 出错了: {str(e)}")
                print(traceback.format_exc())
    
    async def cleanup(self):
        """Clean up resources"""
        await self.exit_stack.aclose()

    def update_memory(self, query, results, final_answer=None):
        """更新会话记忆"""
        # 更新查询计数和上一次查询
        self.memory["query_count"] += 1
        self.memory["last_query"] = query
        
        # 添加简化的对话历史
        if final_answer:
            self.memory["conversation_history"].append({
                "query": query,
                "answer": final_answer[:200] + "..." if len(final_answer) > 200 else final_answer
            })
            # 只保留最近5轮对话
            if len(self.memory["conversation_history"]) > 5:
                self.memory["conversation_history"] = self.memory["conversation_history"][-5:]
        
        # 处理工具结果，提取位置信息
        for result in results:
            # 提取位置信息
            if isinstance(result, dict):
                # 地理编码结果
                if "formatted_address" in result and "location" in result:
                    address = result["formatted_address"]
                    location = result["location"]
                    city = result.get("city", "")
                    
                    # 尝试从地址或查询中提取位置名称
                    location_name = self._extract_location_name(query, address)
                    if location_name:
                        self.memory["current_locations"][location_name] = {
                            "address": address,
                            "location": location,
                            "city": city
                        }
                
                # POI结果
                if "pois" in result and isinstance(result["pois"], list):
                    for poi in result["pois"]:
                        if "name" in poi and "location" in poi:
                            self.memory["current_pois"].append({
                                "name": poi["name"],
                                "location": poi["location"],
                                "address": poi.get("address", ""),
                                "type": poi.get("type", "")
                            })
                            # 只保留最近10个POI
                            if len(self.memory["current_pois"]) > 10:
                                self.memory["current_pois"] = self.memory["current_pois"][-10:]
                
                # 路线规划结果
                if "distance" in result and "duration" in result:
                    # 尝试提取起点和终点
                    origin, destination = self._extract_route_endpoints(query)
                    if origin and destination:
                        self.memory["current_plans"][f"{origin}-{destination}"] = {
                            "distance": result["distance"],
                            "duration": result["duration"],
                            "tolls": result.get("tolls", "0")
                        }
                        
                        # 尝试为路线添加额外细节（如果是新路线）
                        if f"{origin}-{destination}" not in self.memory["current_plans"]:
                            self._enrich_route_details(origin, destination)
    
    def _extract_location_name(self, query, address):
        """从查询和地址中提取位置名称"""
        # 常见城市名称
        cities = ["北京", "上海", "广州", "深圳", "杭州", "南京", "重庆", "武汉", "西安", "成都", 
                  "苏州", "天津", "郑州", "长沙", "东莞", "宁波", "佛山", "合肥", "青岛", "厦门",
                  "福州", "济南", "珠海", "中山", "惠州", "香港", "澳门"]
        
        # 首先尝试从查询中提取城市名称
        for city in cities:
            if city in query:
                return city
                
        # 如果查询中没有城市名称，尝试从地址中提取
        for city in cities:
            if city in address:
                return city
                
        # 尝试提取POI名称
        poi_pattern = r"([\u4e00-\u9fa5]{2,6}(?:公园|大厦|广场|中心|大学|学校|医院|商场|酒店|景区|景点|火车站|飞机场|机场))"
        poi_matches = re.findall(poi_pattern, query)
        if poi_matches:
            return poi_matches[0]
            
        return None
        
    def _extract_route_endpoints(self, query):
        """从查询中提取路线的起点和终点"""
        # 常见的路线查询模式
        patterns = [
            r"([\u4e00-\u9fa5]+)到([\u4e00-\u9fa5]+)怎么走",
            r"([\u4e00-\u9fa5]+)到([\u4e00-\u9fa5]+)的路线",
            r"从([\u4e00-\u9fa5]+)去([\u4e00-\u9fa5]+)",
            r"从([\u4e00-\u9fa5]+)到([\u4e00-\u9fa5]+)"
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, query)
            if matches and isinstance(matches[0], tuple) and len(matches[0]) == 2:
                return matches[0][0], matches[0][1]
        
        # 尝试从内存中提取最近的位置
        locations = list(self.memory["current_locations"].keys())
        if len(locations) >= 2:
            return locations[-2], locations[-1]
            
        return None, None
        
    def _enrich_route_details(self, origin, destination):
        """增强路线详情"""
        # 根据不同的起点和终点，添加预设的路线详情
        
        # 深圳到珠海
        if (origin == "深圳" and destination == "珠海") or (origin == "珠海" and destination == "深圳"):
            self.route_template = {
                "高速路线": ["广深沿江高速(S3)", "虎门大桥", "广澳高速(G4W)", "港珠澳大桥"],
                "收费站": ["南沙收费站", "香洲收费站"],
                "服务区": ["虎门服务区", "珠海服务区"],
                "景点": ["珠海渔女像", "情侣路", "圆明新园"],
                "美食": ["斗门蚝", "金湾蟹", "珠海渔家菜"]
            }
            return
            
        # 广州到珠海
        if (origin == "广州" and destination == "珠海") or (origin == "珠海" and destination == "广州"):
            self.route_template = {
                "高速路线": ["广州环城高速", "广州南沙港快速", "南沙大桥", "高栏港高速"],
                "收费站": ["南沙收费站", "平沙收费站"],
                "服务区": ["中山服务区", "珠海服务区"],
                "景点": ["圆明新园", "长隆海洋王国", "日月贝"],
                "美食": ["蚝烙", "冰烧海参", "猪肚鸡"]
            }
            return
            
        # 香港到珠海
        if (origin == "香港" and destination == "珠海") or (origin == "珠海" and destination == "香港"):
            self.route_template = {
                "高速路线": ["深港西部通道", "广深沿江高速", "虎门大桥", "港珠澳大桥"],
                "收费站": ["香港收费站", "珠海收费站"],
                "服务区": ["屯门服务区", "珠海服务区"],
                "景点": ["香港迪士尼", "维多利亚港", "珠海长隆海洋王国"],
                "美食": ["港式茶餐厅", "珠海海鲜", "妈阁面"]
            }
            return
            
        # 上海到杭州
        if (origin == "上海" and destination == "杭州") or (origin == "杭州" and destination == "上海"):
            self.route_template = {
                "高速路线": ["沪杭高速(G60)", "杭州绕城高速"],
                "收费站": ["松江收费站", "杭州收费站"],
                "服务区": ["嘉兴服务区", "余杭服务区"],
                "景点": ["西湖", "灵隐寺", "西溪湿地"],
                "美食": ["杭州小笼包", "西湖醋鱼", "东坡肉"]
            }
            return
            
        # 北京到天津
        if (origin == "北京" and destination == "天津") or (origin == "天津" and destination == "北京"):
            self.route_template = {
                "高速路线": ["京津高速", "京津塘高速"],
                "收费站": ["武清收费站", "天津收费站"],
                "服务区": ["武清服务区", "杨村服务区"],
                "景点": ["天津之眼", "意式风情区", "五大道"],
                "美食": ["狗不理包子", "煎饼果子", "天津麻花"]
            }
            return
            
        # 默认模板
        self.route_template = {
            "高速路线": ["可能经过的高速路线"],
            "收费站": ["可能经过的收费站"],
            "服务区": ["途经的服务区"],
            "景点": ["目的地周边景点"],
            "美食": ["目的地特色美食"]
        }
    
    def get_memory_prompt(self, query):
        """根据当前查询生成记忆提示"""
        if self.memory["query_count"] == 0:
            return ""  # 第一次查询，没有记忆
            
        memory_parts = ["根据我们之前的对话，我知道以下信息:"]
        
        # 添加当前已知的位置信息
        if self.memory["current_locations"]:
            locations_str = []
            for name, info in self.memory["current_locations"].items():
                locations_str.append(f"{name}: {info['address']} ({info['location']})")
            
            if locations_str:
                memory_parts.append("位置信息:")
                memory_parts.extend([f"  - {loc}" for loc in locations_str])
        
        # 添加当前已知的POI信息
        if self.memory["current_pois"]:
            poi_str = []
            for poi in self.memory["current_pois"]:
                poi_str.append(f"{poi['name']}: {poi['address']}")
            
            if poi_str:
                memory_parts.append("地点信息:")
                memory_parts.extend([f"  - {p}" for p in poi_str])
        
        # 添加当前已知的行程计划
        if self.memory["current_plans"]:
            plan_str = []
            for route, info in self.memory["current_plans"].items():
                distance_km = float(info["distance"]) / 1000 if info["distance"].isdigit() else info["distance"]
                duration_min = int(info["duration"]) // 60 if info["duration"].isdigit() else info["duration"]
                plan_str.append(f"{route}: 距离约{distance_km}公里, 时间约{duration_min}分钟")
            
            if plan_str:
                memory_parts.append("路线信息:")
                memory_parts.extend([f"  - {p}" for p in plan_str])
        
        # 添加对话历史摘要
        if self.memory["conversation_history"]:
            memory_parts.append("我们之前讨论过:")
            for i, hist in enumerate(self.memory["conversation_history"][-3:]):  # 只取最近3轮
                memory_parts.append(f"  - 您问: {hist['query']}")
                memory_parts.append(f"    我答: {hist['answer'][:100]}...")
        
        return "\n".join(memory_parts)

    def get_memory_context(self):
        """获取记忆上下文，以供最终回答使用"""
        if self.memory["query_count"] == 0:
            return ""
            
        context_parts = []
        
        # 添加位置信息
        if self.memory["current_locations"]:
            locations = []
            for name, info in self.memory["current_locations"].items():
                locations.append(f"{name}（地址：{info['address']}，坐标：{info['location']}）")
            if locations:
                context_parts.append("记忆中的位置信息:\n" + "\n".join(locations))
        
        # 添加POI信息
        if self.memory["current_pois"] and len(self.memory["current_pois"]) > 0:
            pois = []
            for poi in self.memory["current_pois"][-3:]:  # 只使用最近3个POI
                pois.append(f"{poi['name']}（地址：{poi['address']}，类型：{poi['type']}）")
            if pois:
                context_parts.append("记忆中的POI信息:\n" + "\n".join(pois))
        
        # 添加路线规划
        if self.memory["current_plans"]:
            plans = []
            for route, info in self.memory["current_plans"].items():
                distance_km = float(info["distance"]) / 1000 if info["distance"].isdigit() else info["distance"]
                duration_min = int(info["duration"]) // 60 if info["duration"].isdigit() else info["duration"]
                plans.append(f"{route}（距离：约{distance_km}公里，时间：约{duration_min}分钟）")
            if plans:
                context_parts.append("记忆中的路线信息:\n" + "\n".join(plans))
        
        # 如果没有记忆信息，返回空
        if not context_parts:
            return ""
            
        return "参考历史信息：\n" + "\n\n".join(context_parts)
    
    def get_route_context(self, query):
        """获取路线上下文信息，如适用"""
        # 尝试从查询中提取起点和终点
        origin, destination = self._extract_route_endpoints(query)
        
        if not origin or not destination:
            return ""  # 如果无法提取起点终点，不提供路线上下文
        
        # 在这里，根据提取的起点和终点，自动选择合适的路线模板
        self._enrich_route_details(origin, destination)
        
        # 如果有路线模板，提供详细信息
        if hasattr(self, 'route_template') and self.route_template:
            context_parts = [f"关于{origin}到{destination}的路线信息："]
            
            # 高速路线
            if self.route_template["高速路线"] and self.route_template["高速路线"][0] != "可能经过的高速路线":
                highways = "、".join(self.route_template["高速路线"])
                context_parts.append(f"主要道路: {highways}")
                
            # 收费站
            if self.route_template["收费站"] and self.route_template["收费站"][0] != "可能经过的收费站":
                toll_stations = "、".join(self.route_template["收费站"])
                context_parts.append(f"收费站: {toll_stations}")
                
            # 服务区
            if self.route_template["服务区"] and self.route_template["服务区"][0] != "途经的服务区":
                service_areas = "、".join(self.route_template["服务区"])
                context_parts.append(f"服务区: {service_areas}")
                
            # 景点
            if self.route_template["景点"] and self.route_template["景点"][0] != "目的地周边景点":
                attractions = "、".join(self.route_template["景点"])
                context_parts.append(f"{destination}周边景点: {attractions}")
                
            # 美食
            if self.route_template["美食"] and self.route_template["美食"][0] != "目的地特色美食":
                foods = "、".join(self.route_template["美食"])
                context_parts.append(f"{destination}特色美食: {foods}")
                
            return "\n".join(context_parts)
            
        return ""

async def main():
    if len(sys.argv) < 2:
        print("Usage: python client.py <path_to_server_script>")
        sys.exit(1)
        
    client = MCPClient()
    try:
        server_script = sys.argv[1]
        print(f"Connecting to MCP server: {server_script}")
        await client.connect_to_server(server_script)
        await client.chat_loop()
    finally:
        await client.cleanup()

if __name__ == "__main__":
    asyncio.run(main()) 