/**
 * 五子棋游戏核心逻辑
 * Gomoku Game Core Logic
 * 
 * 负责游戏的核心功能：
 * - 游戏状态管理
 * - 落子逻辑
 * - 获胜判定算法
 * - 悔棋功能
 */

/**
 * 游戏状态枚举
 */
const GameState = {
    IDLE: 'idle',           // 空闲状态
    PLAYING: 'playing',     // 进行中
    BLACK_WIN: 'black_win', // 黑方获胜
    WHITE_WIN: 'white_win', // 白方获胜
    DRAW: 'draw'            // 平局
};

/**
 * 玩家颜色枚举
 */
const PlayerColor = {
    BLACK: 'black',
    WHITE: 'white'
};

/**
 * 游戏核心类
 */
class GomokuGame {
    /**
     * 构造函数
     * @param {number} boardSize - 棋盘大小，默认 15
     */
    constructor(boardSize = 15) {
        this.boardSize = boardSize;
        this.reset();
    }
    
    /**
     * 重置游戏状态
     */
    reset() {
        // 初始化棋盘（0: 空，1: 黑，2: 白）
        this.board = this.createEmptyBoard();
        
        // 当前玩家（黑方先手）
        this.currentPlayer = PlayerColor.BLACK;
        
        // 游戏状态
        this.state = GameState.PLAYING;
        
        // 移动历史（用于悔棋）
        this.moveHistory = [];
        
        // 最后落子位置
        this.lastMove = null;
        
        // 获胜位置
        this.winningPositions = null;
    }
    
    /**
     * 创建空棋盘
     * @returns {number[][]} - 二维数组表示的棋盘
     */
    createEmptyBoard() {
        return Array(this.boardSize).fill(null)
            .map(() => Array(this.boardSize).fill(0));
    }
    
    /**
     * 获取当前游戏状态
     * @returns {string} - 游戏状态
     */
    getState() {
        return this.state;
    }
    
    /**
     * 获取当前玩家
     * @returns {string} - 当前玩家颜色
     */
    getCurrentPlayer() {
        return this.currentPlayer;
    }
    
    /**
     * 获取步数
     * @returns {number} - 当前步数
     */
    getMoveCount() {
        return this.moveHistory.length;
    }
    
    /**
     * 检查位置是否有效
     * @param {number} row - 行坐标
     * @param {number} col - 列坐标
     * @returns {boolean} - 位置是否有效
     */
    isValidPosition(row, col) {
        return row >= 0 && row < this.boardSize &&
               col >= 0 && col < this.boardSize;
    }
    
    /**
     * 检查位置是否为空
     * @param {number} row - 行坐标
     * @param {number} col - 列坐标
     * @returns {boolean} - 位置是否为空
     */
    isEmpty(row, col) {
        return this.isValidPosition(row, col) && this.board[row][col] === 0;
    }
    
    /**
     * 落子
     * @param {number} row - 行坐标
     * @param {number} col - 列坐标
     * @returns {Object} - 落子结果 {success, message, state, winningPositions}
     */
    placeStone(row, col) {
        // 检查游戏是否进行中
        if (this.state !== GameState.PLAYING) {
            return {
                success: false,
                message: '游戏已结束',
                state: this.state
            };
        }
        
        // 检查位置是否有效
        if (!this.isValidPosition(row, col)) {
            return {
                success: false,
                message: '位置无效',
                state: this.state
            };
        }
        
        // 检查位置是否为空
        if (!this.isEmpty(row, col)) {
            return {
                success: false,
                message: '该位置已有棋子',
                state: this.state
            };
        }
        
        // 获取当前玩家对应的数值
        const playerValue = this.currentPlayer === PlayerColor.BLACK ? 1 : 2;
        
        // 落子
        this.board[row][col] = playerValue;
        
        // 记录移动历史
        this.moveHistory.push({
            row,
            col,
            player: this.currentPlayer
        });
        
        // 更新最后落子位置
        this.lastMove = { row, col };
        
        // 检查是否获胜
        const winResult = this.checkWin(row, col, playerValue);
        
        if (winResult.won) {
            this.winningPositions = winResult.positions;
            this.state = this.currentPlayer === PlayerColor.BLACK 
                ? GameState.BLACK_WIN 
                : GameState.WHITE_WIN;
            
            return {
                success: true,
                message: `${this.currentPlayer === PlayerColor.BLACK ? '黑方' : '白方'}获胜!`,
                state: this.state,
                winningPositions: winResult.positions
            };
        }
        
        // 检查是否平局
        if (this.isDraw()) {
            this.state = GameState.DRAW;
            return {
                success: true,
                message: '平局!',
                state: this.state
            };
        }
        
        // 切换玩家
        this.currentPlayer = this.currentPlayer === PlayerColor.BLACK 
            ? PlayerColor.WHITE 
            : PlayerColor.BLACK;
        
        return {
            success: true,
            message: '',
            state: this.state
        };
    }
    
    /**
     * 检查是否获胜
     * @param {number} row - 落子行坐标
     * @param {number} col - 落子列坐标
     * @param {number} playerValue - 玩家值（1 或 2）
     * @returns {Object} - {won: boolean, positions: Array}
     */
    checkWin(row, col, playerValue) {
        // 四个方向：水平、垂直、左斜、右斜
        const directions = [
            [0, 1],   // 水平
            [1, 0],   // 垂直
            [1, 1],   // 左斜（\）
            [1, -1]   // 右斜（/）
        ];
        
        for (const [dr, dc] of directions) {
            const positions = this.checkDirection(row, col, dr, dc, playerValue);
            
            if (positions.length >= 5) {
                return { won: true, positions };
            }
        }
        
        return { won: false, positions: [] };
    }
    
    /**
     * 检查指定方向是否有五子连珠
     * @param {number} row - 起始行坐标
     * @param {number} col - 起始列坐标
     * @param {number} dr - 行方向增量
     * @param {number} dc - 列方向增量
     * @param {number} playerValue - 玩家值
     * @returns {Array} - 连续棋子的位置数组
     */
    checkDirection(row, col, dr, dc, playerValue) {
        const positions = [{ row, col }];
        
        // 向正方向检查
        let r = row + dr;
        let c = col + dc;
        while (this.isValidPosition(r, c) && this.board[r][c] === playerValue) {
            positions.push({ row: r, col: c });
            r += dr;
            c += dc;
        }
        
        // 向反方向检查
        r = row - dr;
        c = col - dc;
        while (this.isValidPosition(r, c) && this.board[r][c] === playerValue) {
            positions.push({ row: r, col: c });
            r -= dr;
            c -= dc;
        }
        
        return positions;
    }
    
    /**
     * 检查是否平局（棋盘已满）
     * @returns {boolean} - 是否平局
     */
    isDraw() {
        for (let row = 0; row < this.boardSize; row++) {
            for (let col = 0; col < this.boardSize; col++) {
                if (this.board[row][col] === 0) {
                    return false;
                }
            }
        }
        return true;
    }
    
    /**
     * 悔棋
     * @returns {Object} - 悔棋结果 {success, message, lastMove}
     */
    undo() {
        if (this.moveHistory.length === 0) {
            return {
                success: false,
                message: '没有可悔棋的步数'
            };
        }
        
        // 获取最后一步
        const lastMove = this.moveHistory.pop();
        
        // 清除该位置的棋子
        this.board[lastMove.row][lastMove.col] = 0;
        
        // 更新最后落子位置
        if (this.moveHistory.length > 0) {
            const previousMove = this.moveHistory[this.moveHistory.length - 1];
            this.lastMove = { row: previousMove.row, col: previousMove.col };
        } else {
            this.lastMove = null;
        }
        
        // 切换回上一步的玩家
        this.currentPlayer = lastMove.player;
        
        // 重置游戏状态为进行中
        this.state = GameState.PLAYING;
        this.winningPositions = null;
        
        return {
            success: true,
            message: '',
            lastMove
        };
    }
    
    /**
     * 获取最后落子位置
     * @returns {Object|null} - 最后落子位置 {row, col} 或 null
     */
    getLastMove() {
        return this.lastMove;
    }
    
    /**
     * 获取获胜位置
     * @returns {Array|null} - 获胜位置数组或 null
     */
    getWinningPositions() {
        return this.winningPositions;
    }
    
    /**
     * 获取棋盘数据
     * @returns {number[][]} - 棋盘二维数组
     */
    getBoard() {
        return this.board.map(row => [...row]);
    }
    
    /**
     * 获取移动历史
     * @returns {Array} - 移动历史数组
     */
    getMoveHistory() {
        return this.moveHistory.map(move => ({ ...move }));
    }
    
    /**
     * 检查是否可以悔棋
     * @returns {boolean} - 是否可以悔棋
     */
    canUndo() {
        return this.moveHistory.length > 0;
    }
}

// 导出类
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { GomokuGame, GameState, PlayerColor };
}
