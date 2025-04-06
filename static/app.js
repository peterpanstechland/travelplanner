// 全局变量
const API_BASE_URL = window.location.origin; // 自动适应当前域名
let activeQueryId = null; // 当前活动查询ID
let websocket = null; // WebSocket连接
let serverLimitedMode = false; // 服务器受限模式标志

// DOM元素
const chatContainer = document.getElementById('chatContainer');
const messageInput = document.getElementById('messageInput');
const sendButton = document.getElementById('sendButton');
const statusPanel = document.getElementById('statusPanel');
const processingStatus = document.getElementById('processingStatus');
const processingText = document.getElementById('processingText');
const memoryPanel = document.getElementById('memoryPanel');
const resetMemoryBtn = document.getElementById('resetMemoryBtn');
const viewMemoryBtn = document.getElementById('viewMemoryBtn');
const memoryContent = document.getElementById('memoryContent');
const memoryModal = new bootstrap.Modal(document.getElementById('memoryModal'));

// 初始化
document.addEventListener('DOMContentLoaded', async () => {
    // 健康检查
    try {
        const response = await fetch(`${API_BASE_URL}/health`);
        
        if (response.ok) {
            console.log('API服务运行正常');
            
            // 检查记忆服务可用性
            try {
                const memoryResponse = await fetch(`${API_BASE_URL}/memory`);
                if (memoryResponse.status === 500 || memoryResponse.status === 503) {
                    // 服务器以有限功能模式运行
                    handleLimitedMode("MCP客户端未初始化，部分功能可能不可用");
                } else if (memoryResponse.ok) {
                    // 记忆服务可用，更新UI
                    const data = await memoryResponse.json();
                    updateMemoryPanel(data.memory);
                }
            } catch (error) {
                console.warn('记忆服务不可用:', error);
                handleLimitedMode("记忆服务不可用，部分功能受限");
            }
        } else {
            showError('API服务不可用');
        }
    } catch (error) {
        console.error('连接错误:', error);
        showError('无法连接到服务器');
    }
    
    // 事件监听器
    messageInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
    
    sendButton.addEventListener('click', sendMessage);
    resetMemoryBtn.addEventListener('click', resetMemory);
    viewMemoryBtn.addEventListener('click', viewFullMemory);
});

// 发送消息
async function sendMessage() {
    const query = messageInput.value.trim();
    if (!query || activeQueryId) return; // 如果没有内容或有活动查询，则不处理
    
    // 关闭任何现有的WebSocket连接
    closeWebSocketConnection();
    
    // 显示用户消息
    addMessage('user', query);
    messageInput.value = '';
    
    // 显示处理状态
    showProcessingStatus(true);
    
    try {
        // 发送查询到API
        const response = await fetch(`${API_BASE_URL}/query`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query })
        });
        
        if (response.ok) {
            const data = await response.json();
            activeQueryId = data.query_id;
            
            // 建立WebSocket连接来监听进度
            connectWebSocket(activeQueryId);
        } else {
            if (response.status === 503) {
                // 服务未就绪
                handleLimitedMode("服务未完全初始化，请重启服务器后再试");
                showProcessingStatus(false);
            } else {
                // 其他错误
                const errorData = await response.json();
                showError(`查询失败: ${errorData.detail || '未知错误'}`);
                showProcessingStatus(false);
            }
        }
    } catch (error) {
        console.error('发送消息错误:', error);
        showError('无法连接到服务器，请检查服务是否正常运行');
        showProcessingStatus(false);
    }
}

// 关闭WebSocket连接
function closeWebSocketConnection() {
    if (websocket) {
        console.log('关闭现有WebSocket连接');
        websocket.onclose = null; // 移除关闭事件处理程序以避免触发额外的日志
        websocket.close();
        websocket = null;
    }
    
    // 重置活动查询ID
    if (activeQueryId) {
        console.log('重置活动查询ID:', activeQueryId);
        activeQueryId = null;
    }
}

// 建立WebSocket连接
function connectWebSocket(queryId) {
    // 确保清理任何现有连接
    closeWebSocketConnection();
    
    console.log(`正在建立到查询 ${queryId} 的WebSocket连接`);
    
    // 记录当前的查询ID
    activeQueryId = queryId;
    
    // 获取当前网站的端口，使用location.port而不是硬编码8000
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const port = window.location.port || (window.location.protocol === 'https:' ? '443' : '80');
    
    const wsUrl = `${wsProtocol}//${window.location.hostname}:${port}/ws/query/${queryId}`;
    console.log('WebSocket URL:', wsUrl);
    
    websocket = new WebSocket(wsUrl);
    
    // 添加连接超时处理
    const connectionTimeout = setTimeout(() => {
        if (websocket && websocket.readyState !== WebSocket.OPEN) {
            console.error('WebSocket连接超时');
            websocket.close();
            websocket = null;
            showError('WebSocket连接超时，请刷新页面重试');
            showProcessingStatus(false);
            activeQueryId = null;
        }
    }, 10000); // 10秒超时
    
    websocket.onopen = () => {
        console.log('WebSocket连接已建立');
        clearTimeout(connectionTimeout);
    };
    
    websocket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        
        if (data.error) {
            showError(data.error);
            showProcessingStatus(false);
            activeQueryId = null;
            return;
        }
        
        // 验证这是当前活动的查询ID
        if (data.query_id !== activeQueryId) {
            console.error('收到非当前查询的WebSocket消息', data);
            return; // 忽略不匹配的消息
        }
        
        // 更新处理状态文本
        updateProcessingText(data.status);
        
        // 如果查询完成，获取结果
        if (data.status === 'completed' && data.result) {
            fetchQueryResult(queryId);
        } else if (data.status === 'failed') {
            showError('查询处理失败');
            showProcessingStatus(false);
            activeQueryId = null;
        }
    };
    
    websocket.onerror = (error) => {
        console.error('WebSocket错误:', error);
        showError('WebSocket连接错误');
        showProcessingStatus(false);
        activeQueryId = null;
    };
    
    websocket.onclose = () => {
        console.log('WebSocket连接已关闭');
    };
}

// 获取查询结果
async function fetchQueryResult(queryId) {
    try {
        // 先获取查询详情，确认这是当前的查询
        const statusResponse = await fetch(`${API_BASE_URL}/query/${queryId}/status`);
        if (statusResponse.ok) {
            const statusData = await statusResponse.json();
            
            // 获取用户最后发送的消息，确保匹配
            const lastUserMessage = getLastUserMessage();
            if (lastUserMessage && statusData.query !== lastUserMessage) {
                console.error('查询与最后发送的消息不匹配');
                console.log('查询:', statusData.query);
                console.log('最后消息:', lastUserMessage);
                showError('查询与最后发送的消息不匹配，可能存在混淆。请重试您的问题。');
                showProcessingStatus(false);
                activeQueryId = null;
                return;
            }
            
            // 继续获取查询结果
            const response = await fetch(`${API_BASE_URL}/query/${queryId}/result`);
            
            if (response.ok) {
                const data = await response.json();
                
                // 显示结果
                if (data.final_answer) {
                    addMessage('assistant', data.final_answer);
                    
                    // 更新记忆状态
                    fetchMemory();
                }
            } else {
                const error = await response.json();
                showError(`获取结果失败: ${error.detail}`);
            }
        } else {
            showError('无法验证查询状态');
        }
    } catch (error) {
        console.error('获取结果错误:', error);
        showError('获取结果时出错');
    } finally {
        showProcessingStatus(false);
        activeQueryId = null;
    }
}

// 获取最后一条用户消息
function getLastUserMessage() {
    const userMessages = chatContainer.querySelectorAll('.user-message .message-content');
    if (userMessages.length > 0) {
        // 获取最后一条消息的文本内容
        const lastMessage = userMessages[userMessages.length - 1];
        return lastMessage.textContent.trim();
    }
    return null;
}

// 获取记忆状态
async function fetchMemory() {
    try {
        const response = await fetch(`${API_BASE_URL}/memory`);
        
        if (response.ok) {
            const data = await response.json();
            updateMemoryPanel(data.memory);
        }
    } catch (error) {
        console.error('获取记忆状态错误:', error);
    }
}

// 重置记忆
async function resetMemory() {
    if (!confirm('确定要重置所有记忆吗？这将删除所有保存的位置、计划和对话历史。')) {
        return;
    }
    
    try {
        const response = await fetch(`${API_BASE_URL}/memory/reset`, {
            method: 'POST'
        });
        
        if (response.ok) {
            // 更新记忆面板
            memoryPanel.innerHTML = '<p>记忆已重置</p>';
            addMessage('system', '系统记忆已重置');
        } else {
            const error = await response.json();
            showError(`重置记忆失败: ${error.detail}`);
        }
    } catch (error) {
        console.error('重置记忆错误:', error);
        showError('重置记忆时出错');
    }
}

// 查看完整记忆
async function viewFullMemory() {
    try {
        const response = await fetch(`${API_BASE_URL}/memory`);
        
        if (response.ok) {
            const data = await response.json();
            memoryContent.textContent = JSON.stringify(data.memory, null, 2);
            memoryModal.show();
        }
    } catch (error) {
        console.error('获取完整记忆错误:', error);
        showError('获取完整记忆时出错');
    }
}

// 添加消息到聊天容器
function addMessage(role, content) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}-message`;
    
    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content markdown-content';
    
    // 使用marked库将Markdown渲染为HTML
    contentDiv.innerHTML = marked.parse(content);
    
    messageDiv.appendChild(contentDiv);
    chatContainer.appendChild(messageDiv);
    
    // 滚动到底部
    chatContainer.scrollTop = chatContainer.scrollHeight;
}

// 显示错误消息
function showError(message) {
    addMessage('system', `❌ 错误：${message}`);
}

// 更新处理状态显示
function showProcessingStatus(show) {
    if (show) {
        processingStatus.classList.remove('d-none');
    } else {
        processingStatus.classList.add('d-none');
    }
}

// 更新处理状态文本
function updateProcessingText(status) {
    let text = '正在处理您的问题...';
    
    switch (status) {
        case 'queued':
            text = '查询已排队，等待处理...';
            break;
        case 'processing':
            text = '正在分析并处理您的问题...';
            break;
        case 'completed':
            text = '处理完成，获取结果...';
            break;
        case 'failed':
            text = '处理失败';
            break;
    }
    
    processingText.textContent = text;
}

// 更新记忆面板
function updateMemoryPanel(memory) {
    // 清空面板
    memoryPanel.innerHTML = '';
    
    if (memory.query_count === 0) {
        memoryPanel.innerHTML = '<p>暂无记忆信息</p>';
        return;
    }
    
    // 添加位置信息
    if (Object.keys(memory.current_locations).length > 0) {
        const locationsDiv = document.createElement('div');
        locationsDiv.className = 'memory-item';
        
        const title = document.createElement('h6');
        title.textContent = '位置信息';
        locationsDiv.appendChild(title);
        
        const list = document.createElement('ul');
        list.className = 'list-unstyled small';
        
        for (const [name, info] of Object.entries(memory.current_locations)) {
            const item = document.createElement('li');
            item.textContent = `${name}: ${info.address}`;
            list.appendChild(item);
        }
        
        locationsDiv.appendChild(list);
        memoryPanel.appendChild(locationsDiv);
    }
    
    // 添加POI信息
    if (memory.current_pois.length > 0) {
        const poisDiv = document.createElement('div');
        poisDiv.className = 'memory-item';
        
        const title = document.createElement('h6');
        title.textContent = '兴趣点';
        poisDiv.appendChild(title);
        
        const list = document.createElement('ul');
        list.className = 'list-unstyled small';
        
        for (const poi of memory.current_pois.slice(0, 3)) {
            const item = document.createElement('li');
            item.textContent = `${poi.name}: ${poi.address}`;
            list.appendChild(item);
        }
        
        poisDiv.appendChild(list);
        memoryPanel.appendChild(poisDiv);
    }
    
    // 添加路线信息
    if (Object.keys(memory.current_plans).length > 0) {
        const plansDiv = document.createElement('div');
        plansDiv.className = 'memory-item';
        
        const title = document.createElement('h6');
        title.textContent = '路线规划';
        plansDiv.appendChild(title);
        
        const list = document.createElement('ul');
        list.className = 'list-unstyled small';
        
        for (const [route, info] of Object.entries(memory.current_plans)) {
            const item = document.createElement('li');
            const distanceKm = parseFloat(info.distance) / 1000;
            const durationMin = Math.floor(parseInt(info.duration) / 60);
            item.textContent = `${route}: ${distanceKm.toFixed(1)}公里，${durationMin}分钟`;
            list.appendChild(item);
        }
        
        plansDiv.appendChild(list);
        memoryPanel.appendChild(plansDiv);
    }
    
    // 添加查询计数
    const queriesDiv = document.createElement('div');
    queriesDiv.className = 'memory-item';
    queriesDiv.innerHTML = `<small class="text-muted">已处理 ${memory.query_count} 个查询</small>`;
    memoryPanel.appendChild(queriesDiv);
}

// 处理受限模式
function handleLimitedMode(message) {
    serverLimitedMode = true;
    
    // 显示警告消息
    const warningDiv = document.createElement('div');
    warningDiv.className = 'alert alert-warning';
    warningDiv.innerHTML = `<i class="bi bi-exclamation-triangle"></i> ${message}<br>
                           <small>您仍然可以尝试发送查询，但部分功能可能无法正常工作。</small>`;
    
    // 在状态面板显示警告
    statusPanel.innerHTML = '';
    statusPanel.appendChild(warningDiv);
    
    // 在对话框中也显示系统消息
    addMessage('system', `⚠️ ${message}\n\n您仍然可以尝试发送查询，但部分功能可能无法正常工作。如果持续出现问题，请重启服务器。`);
    
    // 禁用记忆相关功能
    resetMemoryBtn.classList.add('disabled');
    viewMemoryBtn.classList.add('disabled');
    memoryPanel.innerHTML = '<p class="text-muted">记忆服务不可用</p>';
} 