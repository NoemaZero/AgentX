/**
 * 五子棋游戏主入口
 * Gomoku Game Main Entry
 * 
 * 负责：
 * - 初始化游戏
 * - 绑定 UI 事件
 * - 协调游戏逻辑和渲染
 * - 人机对战 AI 集成
 */

/**
 * 游戏控制器类
 */
class GameController {
    /**
     * 构造函数
     */
    constructor() {
        // DOM 元素引用
        this.boardElement = document.getElementById('board');
        this.overlayElement = document.getElementById('boardOverlay');
        this.currentPlayerIndicator = document.getElementById('currentPlayerIndicator');
        this.statusText = document.getElementById('statusText');
        this.moveCountElement = document.getElementById('moveCount');
        this.undoBtn = document.getElementById('undoBtn');
        this.restartBtn = document.getElementById('restartBtn');
        this.winModal = document.getElementById('winModal');
        this.winMessage = document.getElementById('winMessage');
        this.playAgainBtn = document.getElementById('playAgainBtn');
        
        // AI 相关 DOM 元素
        this.gameModeSelect = document.getElementById('gameMode');
        this.difficultySelect = document.getElementById('difficulty');
        this.aiStatusElement = document.getElementById('aiStatus');
        this.aiStatusText = document.getElementById('aiStatusText');
        this.aiSettingElement = document.getElementById('aiSetting');
        
        // 初始化游戏和渲染器
        this.game = new GomokuGame(15);
        this.renderer = new BoardRenderer(this.boardElement, this.overlayElement, 15);
        
        // AI 相关属性
        this.gameMode = 'pve'; // 'pve' = 人机对战，'pvp' = 人人对战
        this.ai = new GomokuAI(Difficulty.MEDIUM, 15);
        this.aiColor = 'white'; // AI 执白棋
        this.isAIThinking = false; // AI 是否正在思考
        
        // 绑定事件
        this.bindEvents();
        
        // 初始化 UI
        this.updateUI();
        this.updateModeUI();
    }
    
    /**
     * 绑定所有事件
     */
    bindEvents() {
        // 棋盘点击事件
        this.boardElement.addEventListener('click', (e) => {
            // AI 思考时禁止玩家操作
            if (this.isAIThinking) return;
            
            const position = this.renderer.handleClick(e);
            if (position) {
                this.handleMove(position.row, position.col);
            }
        });
        
        // 悔棋按钮
        this.undoBtn.addEventListener('click', () => this.handleUndo());
        
        // 重新开始按钮
        this.restartBtn.addEventListener('click', () => this.handleRestart());
        
        // 再来一局按钮
        this.playAgainBtn.addEventListener('click', () => this.handlePlayAgain());
        
        // 游戏模式切换
        this.gameModeSelect.addEventListener('change', (e) => {
            this.gameMode = e.target.value;
            this.updateModeUI();
            this.resetGame();
        });
        
        // 难度选择
        this.difficultySelect.addEventListener('change', (e) => {
            this.ai.setDifficulty(e.target.value);
            this.showTip(`AI 难度已设置为：${this.ai.getDifficultyName()}`);
        });
        
        // 窗口大小变化时重新渲染网格
        let resizeTimeout;
        window.addEventListener('resize', () => {
            clearTimeout(resizeTimeout);
            resizeTimeout = setTimeout(() => {
                this.renderer.refreshGrid();
            }, 150);
        });
    }
    
    /**
     * 更新模式相关 UI
     */
    updateModeUI() {
        if (this.gameMode === 'pve') {
            // 显示 AI 相关控件
            this.aiSettingElement.style.display = 'flex';
            this.aiStatusElement.style.display = 'flex';
            
            // 更新玩家指示器文本
            const nameElement = this.currentPlayerIndicator.querySelector('.player-name');
            if (this.game.getCurrentPlayer() === 'black') {
                nameElement.textContent = '你 (黑方)';
            } else {
                nameElement.textContent = 'AI(白方)';
            }
        } else {
            // 隐藏 AI 相关控件
            this.aiSettingElement.style.display = 'none';
            this.aiStatusElement.style.display = 'none';
            
            // 更新玩家指示器文本
            const nameElement = this.currentPlayerIndicator.querySelector('.player-name');
            if (this.game.getCurrentPlayer() === 'black') {
                nameElement.textContent = '黑方';
            } else {
                nameElement.textContent = '白方';
            }
        }
    }
    
    /**
     * 处理落子
     * @param {number} row - 行坐标
     * @param {number} col - 列坐标
     */
    async handleMove(row, col) {
        try {
            // 执行落子逻辑
            const result = this.game.placeStone(row, col);
            
            if (result.success) {
                // 获取落子前的玩家（因为 placeStone 已经切换了玩家）
                const currentPlayer = this.game.getCurrentPlayer();
                const previousPlayer = currentPlayer === 'black' ? 'white' : 'black';
                
                // 渲染棋子
                this.renderer.placeStone(row, col, previousPlayer);
                
                // 更新 UI
                this.updateUI();
                
                // 检查游戏结束
                if (result.state !== 'playing') {
                    this.handleGameEnd(result);
                    return;
                }
                
                // 人机模式下，触发 AI 落子
                if (this.gameMode === 'pve' && currentPlayer === this.aiColor) {
                    this.triggerAIMove();
                }
            } else {
                // 显示错误提示
                this.showTip(result.message);
            }
        } catch (error) {
            console.error('落子出错:', error);
            this.showError('落子失败，请重试');
        }
    }
    
    /**
     * 触发 AI 落子
     */
    async triggerAIMove() {
        this.isAIThinking = true;
        this.showAIStatus('AI 思考中...');
        this.boardElement.style.cursor = 'wait';
        
        // 使用 setTimeout 让 UI 先更新
        setTimeout(() => {
            try {
                // 获取 AI 最佳落子位置
                const board = this.game.getBoard();
                const aiColorNum = this.aiColor === 'black' ? 1 : 2;
                const bestMove = this.ai.getBestMove(board, aiColorNum);
                
                // 禁用 AI 状态显示
                this.hideAIStatus();
                
                // 执行 AI 落子
                this.handleMove(bestMove.row, bestMove.col);
                
                // 恢复棋盘光标
                this.boardElement.style.cursor = 'pointer';
                this.isAIThinking = false;
            } catch (error) {
                console.error('AI 落子出错:', error);
                this.hideAIStatus();
                this.boardElement.style.cursor = 'pointer';
                this.isAIThinking = false;
            }
        }, 300); // 延迟 300ms 让 AI 看起来在"思考"
    }
    
    /**
     * 显示 AI 状态
     * @param {string} text - 状态文本
     */
    showAIStatus(text) {
        if (this.aiStatusText) {
            this.aiStatusText.textContent = text;
        }
        this.aiStatusElement.style.display = 'flex';
    }
    
    /**
     * 隐藏 AI 状态
     */
    hideAIStatus() {
        if (this.gameMode === 'pve' && this.game.getState() === 'playing') {
            this.aiStatusElement.style.display = 'flex';
            if (this.aiStatusText) {
                this.aiStatusText.textContent = 'AI 就绪';
            }
        } else {
            this.aiStatusElement.style.display = 'none';
        }
    }
    
    /**
     * 处理悔棋
     */
    handleUndo() {
        try {
            // 人机模式下，悔棋两步（玩家一步 + AI 一步）
            const undoCount = this.gameMode === 'pve' ? 2 : 1;
            
            for (let i = 0; i < undoCount; i++) {
                const result = this.game.undo();
                
                if (result.success && result.lastMove) {
                    // 移除最后一步的棋子
                    this.renderer.removeStone(result.lastMove.row, result.lastMove.col);
                } else {
                    break;
                }
            }
            
            // 更新 UI
            this.updateUI();
        } catch (error) {
            console.error('悔棋出错:', error);
            this.showError('悔棋失败，请重试');
        }
    }
    
    /**
     * 处理重新开始
     */
    handleRestart() {
        try {
            this.resetGame();
        } catch (error) {
            console.error('重新开始出错:', error);
            this.showError('重新开始失败，请重试');
        }
    }
    
    /**
     * 处理再来一局
     */
    handlePlayAgain() {
        try {
            this.hideWinModal();
            this.resetGame();
        } catch (error) {
            console.error('再来一局出错:', error);
            this.showError('重新开始失败，请重试');
        }
    }
    
    /**
     * 重置游戏
     */
    resetGame() {
        this.game.reset();
        this.renderer.clearStones();
        this.renderer.clearWinningLine();
        this.updateUI();
        this.updateModeUI();
        
        // 重置 AI 状态
        this.isAIThinking = false;
        this.boardElement.style.cursor = 'pointer';
        
        if (this.gameMode === 'pve') {
            this.showAIStatus('AI 就绪');
        }
    }
    
    /**
     * 处理游戏结束
     * @param {Object} result - 游戏结果
     */
    handleGameEnd(result) {
        // 标记最后落子
        const lastMove = this.game.getLastMove();
        if (lastMove) {
            this.renderer.markLastMove(lastMove.row, lastMove.col);
        }
        
        // 高亮获胜连线
        const winningPositions = this.game.getWinningPositions();
        if (winningPositions) {
            this.renderer.highlightWinningLine(winningPositions);
        }
        
        // 隐藏 AI 状态
        this.hideAIStatus();
        
        // 显示获胜弹窗
        setTimeout(() => {
            this.showWinModal(result.state);
        }, 500);
    }
    
    /**
     * 更新 UI
     */
    updateUI() {
        // 更新当前玩家指示器
        const currentPlayer = this.game.getCurrentPlayer();
        this.updatePlayerIndicator(currentPlayer);
        
        // 更新游戏状态文本
        this.updateStatusText();
        
        // 更新步数
        this.moveCountElement.textContent = this.game.getMoveCount();
        
        // 更新悔棋按钮状态
        this.undoBtn.disabled = !this.game.canUndo();
        
        // 标记最后落子
        const lastMove = this.game.getLastMove();
        if (lastMove) {
            this.renderer.markLastMove(lastMove.row, lastMove.col);
        }
        
        // 更新 AI 状态文本
        if (this.gameMode === 'pve' && this.game.getState() === 'playing') {
            if (currentPlayer === this.aiColor) {
                this.showAIStatus('AI 思考中...');
            } else {
                this.showAIStatus('AI 就绪');
            }
        }
    }
    
    /**
     * 更新玩家指示器
     * @param {string} player - 玩家颜色
     */
    updatePlayerIndicator(player) {
        const stone = this.currentPlayerIndicator.querySelector('.stone');
        const name = this.currentPlayerIndicator.querySelector('.player-name');
        
        // 更新棋子颜色
        stone.className = `stone ${player}`;
        
        // 更新玩家名称
        if (this.gameMode === 'pve') {
            if (player === 'black') {
                name.textContent = '你 (黑方)';
            } else {
                name.textContent = 'AI(白方)';
            }
        } else {
            name.textContent = player === 'black' ? '黑方' : '白方';
        }
    }
    
    /**
     * 更新状态文本
     */
    updateStatusText() {
        const state = this.game.getState();
        
        switch (state) {
            case 'playing':
                this.statusText.textContent = '进行中';
                this.statusText.style.color = 'var(--highlight-color)';
                break;
            case 'black_win':
                this.statusText.textContent = this.gameMode === 'pve' ? '你获胜了!' : '黑方获胜';
                this.statusText.style.color = 'var(--stone-black)';
                break;
            case 'white_win':
                this.statusText.textContent = this.gameMode === 'pve' ? 'AI 获胜了!' : '白方获胜';
                this.statusText.style.color = 'var(--stone-white)';
                break;
            case 'draw':
                this.statusText.textContent = '平局';
                this.statusText.style.color = 'var(--text-secondary)';
                break;
            default:
                this.statusText.textContent = '未知状态';
        }
    }
    
    /**
     * 显示获胜弹窗
     * @param {string} state - 游戏结束状态
     */
    showWinModal(state) {
        let message = '';
        
        switch (state) {
            case 'black_win':
                message = this.gameMode === 'pve' 
                    ? '🏆 恭喜你获胜了!' 
                    : '🏆 黑方获胜!';
                break;
            case 'white_win':
                message = this.gameMode === 'pve' 
                    ? '🤖 AI 获胜了，再接再厉!' 
                    : '🏆 白方获胜!';
                break;
            case 'draw':
                message = '🤝 平局!';
                break;
            default:
                message = '游戏结束';
        }
        
        this.winMessage.textContent = message;
        this.winModal.classList.add('show');
    }
    
    /**
     * 隐藏获胜弹窗
     */
    hideWinModal() {
        this.winModal.classList.remove('show');
    }
    
    /**
     * 显示提示消息
     * @param {string} message - 提示消息
     */
    showTip(message) {
        // 创建一个临时的提示元素
        const tip = document.createElement('div');
        tip.className = 'toast-tip';
        tip.textContent = message;
        tip.style.cssText = `
            position: fixed;
            top: 20px;
            left: 50%;
            transform: translateX(-50%);
            padding: 12px 24px;
            background: rgba(0, 0, 0, 0.8);
            color: white;
            border-radius: 8px;
            font-size: 0.9rem;
            z-index: 1000;
            animation: fadeInOut 2s ease;
        `;
        
        document.body.appendChild(tip);
        
        // 2 秒后移除
        setTimeout(() => {
            tip.remove();
        }, 2000);
    }
    
    /**
     * 显示错误提示
     * @param {string} message - 错误消息
     */
    showError(message) {
        this.showTip('❌ ' + message);
    }
}

/**
 * 初始化游戏
 * 等待 DOM 加载完成后执行
 */
document.addEventListener('DOMContentLoaded', () => {
    try {
        // 创建游戏控制器实例
        window.gameController = new GameController();
        
        console.log('五子棋游戏初始化成功');
        console.log('默认模式：人机对战');
        console.log('默认难度：中等');
    } catch (error) {
        console.error('游戏初始化失败:', error);
        alert('游戏初始化失败，请刷新页面重试');
    }
});

// 添加淡入淡出动画
const style = document.createElement('style');
style.textContent = `
    @keyframes fadeInOut {
        0% {
            opacity: 0;
            transform: translateX(-50%) translateY(-20px);
        }
        15% {
            opacity: 1;
            transform: translateX(-50%) translateY(0);
        }
        85% {
            opacity: 1;
            transform: translateX(-50%) translateY(0);
        }
        100% {
            opacity: 0;
            transform: translateX(-50%) translateY(-20px);
        }
    }
`;
document.head.appendChild(style);
