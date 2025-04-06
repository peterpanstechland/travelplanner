#!/usr/bin/env python
"""
旅行规划助手 Web服务器
-------------------
提供Web界面和API服务，支持查询处理和WebSocket实时通信

版本: 1.0.0
许可: MIT License
"""

# 标准库导入
import asyncio
import os
import sys
import json
import signal
import logging
import subprocess
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional, Set

# 第三方库导入
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pydantic import BaseModel

# 本地导入
from client import MCPClient

# 基本配置
VERSION = "1.0.0"
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 定义应用生命周期管理
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理，处理启动和关闭事件"""
    # 启动事件：初始化MCP客户端
    global client_instance
    
    try:
        # 解析命令行参数
        server_script = "amap_mcp_server.py"
        if len(sys.argv) > 1:
            server_script = sys.argv[1]
            
        logger.info(f"正在初始化MCP客户端，连接到服务器: {server_script}")
        
        # 先检查服务器脚本是否存在
        if not os.path.exists(server_script):
            logger.warning(f"找不到服务器脚本 {server_script}，将以有限功能模式启动")
            yield
            return
        
        # 检查环境变量
        try:
            from dotenv import load_dotenv
            load_dotenv()
            api_key = os.getenv("CLAUDE_API_KEY")
            if not api_key:
                logger.warning("未找到CLAUDE_API_KEY环境变量，将以有限功能模式启动")
                yield
                return
        except ImportError:
            logger.warning("未找到dotenv库，无法检查环境变量")
        
        # 使用asyncio.wait_for添加超时处理和最多3次重试
        max_retries = 3
        retry_count = 0
        last_error = None
        
        while retry_count < max_retries:
            try:
                logger.info(f"尝试初始化MCP客户端 (尝试 {retry_count + 1}/{max_retries})...")
                client_instance = MCPClient()
                # 给连接设置20秒超时
                await asyncio.wait_for(
                    client_instance.connect_to_server(server_script),
                    timeout=20.0
                )
                logger.info("MCP客户端初始化成功")
                break  # 成功初始化，跳出循环
            except asyncio.TimeoutError:
                logger.error(f"MCP客户端初始化超时 (尝试 {retry_count + 1}/{max_retries})")
                last_error = "初始化超时"
                if client_instance:
                    # 尝试清理已部分初始化的客户端
                    try:
                        await client_instance.cleanup()
                    except:
                        pass
                client_instance = None
            except Exception as e:
                logger.error(f"初始化客户端时出错 (尝试 {retry_count + 1}/{max_retries}): {str(e)}")
                last_error = str(e)
                import traceback
                traceback.print_exc()
                if client_instance:
                    try:
                        await client_instance.cleanup()
                    except:
                        pass
                client_instance = None
            
            retry_count += 1
            if retry_count < max_retries:
                # 等待3秒后重试
                logger.info("等待3秒后重试...")
                await asyncio.sleep(3)
        
        if client_instance is None:
            logger.warning(f"在{max_retries}次尝试后无法初始化MCP客户端: {last_error}")
            logger.warning("服务将以有限功能模式启动，部分API可能不可用")
    except Exception as e:
        logger.error(f"初始化客户端时出错: {str(e)}")
        logger.warning("服务将以有限功能模式启动，部分API可能不可用")
        import traceback
        traceback.print_exc()
        client_instance = None
    
    logger.info("FastAPI服务器启动完成")
    yield
    
    # 关闭事件：清理MCP客户端资源
    if client_instance:
        logger.info("正在关闭MCP客户端...")
        try:
            await asyncio.wait_for(client_instance.cleanup(), timeout=10.0)
            logger.info("MCP客户端已关闭")
        except asyncio.TimeoutError:
            logger.error("关闭MCP客户端超时")
        except Exception as e:
            logger.error(f"关闭客户端时出错: {str(e)}")
            
    logger.info("FastAPI服务器关闭完成")

# FastAPI应用实例
app = FastAPI(
    title="旅行规划助手",
    description="基于高德地图API和Claude API的旅行规划助手",
    version=VERSION,
    lifespan=lifespan
)

# 添加CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 在生产环境中应该限制为特定域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件目录
app.mount("/static", StaticFiles(directory="static"), name="static")

# 全局变量
active_queries: Dict[str, Dict[str, Any]] = {}  # 存储活动查询的状态
connected_clients: Set[WebSocket] = set()  # 活动的WebSocket连接
client_instance: Optional[MCPClient] = None  # MCP客户端实例

# 请求模型
class QueryRequest(BaseModel):
    query: str

class QueryResponse(BaseModel):
    query_id: str
    status: str
    message: str
    
class QueryResult(BaseModel):
    query_id: str
    final_answer: str
    processing_time: float
    
class MemoryResponse(BaseModel):
    memory: Dict

# 生成唯一的查询ID
def generate_query_id():
    return f"query_{datetime.now().strftime('%Y%m%d%H%M%S')}_{os.urandom(4).hex()}"
    
# 异步处理查询任务
async def process_query_task(query_id: str, query: str):
    global client_instance
    start_time = datetime.now()
    
    try:
        # 检查客户端是否可用
        if client_instance is None:
            active_queries[query_id]["status"] = "failed"
            active_queries[query_id]["error"] = "服务未就绪，请稍后再试"
            return
            
        # 更新查询状态
        active_queries[query_id]["status"] = "processing"
        
        # 将查询保存到活动查询对象中，以便前端可以验证
        active_queries[query_id]["query"] = query
        
        # 处理查询
        logger.info(f"处理查询 {query_id}: {query}")
        response = await client_instance.process_query(query)
        
        # 解析最终回答
        final_answer = response
        if "最终回答:" in response:
            answer_start = response.find("最终回答:")
            final_answer = response[answer_start:]
        
        # 计算处理时间
        end_time = datetime.now()
        processing_time = (end_time - start_time).total_seconds()
        
        # 更新查询状态为完成
        active_queries[query_id]["status"] = "completed"
        active_queries[query_id]["result"] = {
            "final_answer": final_answer,
            "processing_time": processing_time
        }
        
        logger.info(f"查询 {query_id} 处理完成，耗时 {processing_time:.2f} 秒")
    except Exception as e:
        # 记录错误信息
        active_queries[query_id]["status"] = "failed"
        active_queries[query_id]["error"] = str(e)
        logger.error(f"处理查询 {query_id} 时出错: {str(e)}")
        import traceback
        traceback.print_exc()

# 健康检查端点
@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "服务正常运行"}

# 查询提交端点
@app.post("/query", response_model=QueryResponse)
async def submit_query(request: QueryRequest, background_tasks: BackgroundTasks):
    global client_instance
    
    if client_instance is None:
        raise HTTPException(status_code=503, detail="服务未就绪，请稍后再试")
    
    # 生成查询ID
    query_id = generate_query_id()
    
    # 初始化查询状态
    active_queries[query_id] = {
        "query": request.query,
        "status": "queued",
        "time_submitted": datetime.now().isoformat(),
    }
    
    # 在后台处理查询
    background_tasks.add_task(process_query_task, query_id, request.query)
    
    return {
        "query_id": query_id,
        "status": "queued",
        "message": "查询已提交，请使用查询ID检查结果"
    }

# 查询状态检查端点
@app.get("/query/{query_id}/status")
async def check_query_status(query_id: str):
    if query_id not in active_queries:
        raise HTTPException(status_code=404, detail=f"未找到查询ID: {query_id}")
    
    query_info = active_queries[query_id].copy()
    status = query_info["status"]
    
    response = {
        "query_id": query_id,
        "status": status,
        "query": query_info["query"],
        "time_submitted": query_info["time_submitted"]
    }
    
    # 如果查询已完成，包含结果
    if status == "completed" and "result" in query_info:
        response["result"] = query_info["result"]
    
    # 如果查询失败，包含错误信息
    if status == "failed" and "error" in query_info:
        response["error"] = query_info["error"]
    
    return response

# 查询结果获取端点
@app.get("/query/{query_id}/result", response_model=Optional[QueryResult])
async def get_query_result(query_id: str):
    if query_id not in active_queries:
        raise HTTPException(status_code=404, detail=f"未找到查询ID: {query_id}")
    
    query_info = active_queries[query_id]
    
    if query_info["status"] != "completed":
        return {
            "query_id": query_id,
            "status": query_info["status"],
            "message": f"查询尚未完成，当前状态: {query_info['status']}"
        }
    
    result = query_info["result"]
    return {
        "query_id": query_id,
        "final_answer": result["final_answer"],
        "processing_time": result["processing_time"]
    }

# 获取记忆状态端点
@app.get("/memory", response_model=MemoryResponse)
async def get_memory():
    global client_instance
    
    if client_instance is None:
        raise HTTPException(status_code=500, detail="客户端未初始化")
    
    # 返回当前记忆状态
    return {"memory": client_instance.memory}

# 重置记忆端点
@app.post("/memory/reset")
async def reset_memory():
    global client_instance
    
    if client_instance is None:
        raise HTTPException(status_code=500, detail="客户端未初始化")
    
    # 重置记忆
    client_instance.memory = {
        "current_locations": {},
        "current_pois": [],
        "current_plans": {},
        "last_query": "",
        "query_count": 0,
        "conversation_history": []
    }
    
    return {"status": "success", "message": "记忆已重置"}

# WebSocket端点，用于实时获取查询处理进度
@app.websocket("/ws/query/{query_id}")
async def websocket_query_status(websocket: WebSocket, query_id: str):
    await websocket.accept()
    
    try:
        if query_id not in active_queries:
            await websocket.send_json({"error": f"未找到查询ID: {query_id}"})
            await websocket.close()
            return
        
        # 发送初始状态，包含查询文本
        await websocket.send_json({
            "query_id": query_id,
            "status": active_queries[query_id]["status"],
            "query": active_queries[query_id].get("query", ""),  # 添加查询文本
            "time": datetime.now().isoformat()
        })
        
        # 持续监控查询状态并发送更新
        watch_count = 0
        max_watch_time = 300  # 5分钟最大监视时间
        
        while watch_count < max_watch_time:
            if query_id not in active_queries:
                await websocket.send_json({"error": "查询已不存在"})
                break
                
            status = active_queries[query_id]["status"]
            update = {
                "query_id": query_id,
                "status": status,
                "query": active_queries[query_id].get("query", ""),  # 添加查询文本
                "time": datetime.now().isoformat()
            }
            
            # 如果查询已完成或失败，添加相关信息
            if status == "completed" and "result" in active_queries[query_id]:
                update["result"] = active_queries[query_id]["result"]
                await websocket.send_json(update)
                break
            elif status == "failed" and "error" in active_queries[query_id]:
                update["error"] = active_queries[query_id]["error"]
                await websocket.send_json(update)
                break
            
            # 发送状态更新
            await websocket.send_json(update)
            await asyncio.sleep(1)  # 每秒更新一次
            watch_count += 1
            
    except Exception as e:
        logger.error(f"WebSocket错误: {str(e)}")
    finally:
        try:
            await websocket.close()
        except:
            pass

# 根路径返回index.html
@app.get("/")
async def read_root():
    return FileResponse('static/index.html')

# 主函数和启动方式
if __name__ == "__main__":
    # 添加信号处理程序，以更优雅地处理中断
    def handle_exit(signum, frame):
        logger.info(f"\n接收到信号 {signum}，正在关闭服务器...")
        sys.exit(0)
    
    # 注册SIGINT和SIGTERM信号处理程序
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)
    
    # 直接启动uvicorn服务器，生命周期管理已经由lifespan处理
    import uvicorn
    logger.info("启动Web服务器，访问 http://localhost:8000 使用旅行规划助手")
    
    # 使用更健壮的配置启动服务器
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
        timeout_keep_alive=60,  # 保持连接超时时间
        limit_concurrency=100,  # 限制并发连接数
        timeout_graceful_shutdown=10  # 优雅关闭超时
    ) 