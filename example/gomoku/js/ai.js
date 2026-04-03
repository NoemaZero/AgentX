/**
 * 五子棋 AI 模块
 * 使用启发式评估函数进行决策
 * 
 * 算法说明：
 * - 基于模式匹配的启发式评估
 * - 遍历所有空位，计算攻防分数
 * - 支持多难度级别
 */

// ==================== 难度配置 ====================

/**
 * 难度级别枚举
 */
const Difficulty = Object.freeze({
    EASY: 'easy',
    MEDIUM: 'medium',
    HARD: 'hard',
    EXPERT: 'expert'
});

/**
 * 难度参数配置
 * - searchDepth: 搜索深度（当前版本为 1，预留扩展）
 * - attackWeight: 进攻权重
 * - defendWeight: 防守权重
 * - randomness: 随机性（增加 AI 的不确定性）
 */
const DifficultyConfig = Object.freeze({
    [Difficulty.EASY]: {
        searchDepth: 1,
        attackWeight: 0.3,
        defendWeight: 0.7,
        randomness: 0.3,
        name: '简单'
    },
    [Difficulty.MEDIUM]: {
        searchDepth: 1,
        attackWeight: 0.5,
        defendWeight: 0.5,
        randomness: 0.15,
        name: '中等'
    },
    [Difficulty.HARD]: {
        searchDepth: 2,
        attackWeight: 0.7,
        defendWeight: 0.3,
        randomness: 0.05,
        name: '困难'
    },
    [Difficulty.EXPERT]: {
        searchDepth: 2,
        attackWeight: 0.85,
        defendWeight: 0.15,
        randomness: 0,
        name: '专家'
    }
});

/**
 * 棋型评分表
 * 进攻分数：AI 自己下这个位置能获得的分数
 * 防守分数：阻止对手在这个位置下棋的分数
 */
const PatternScores = Object.freeze({
    // 进攻分数
    ATTACK: {
        FIVE: 100000,      // 成五（直接获胜）
        LIVE_FOUR: 10000,  // 活四（两端都空，下一步必胜）
        DEAD_FOUR: 1000,   // 冲四（一端被封）
        LIVE_THREE: 1000,  // 活三（两端都空）
        DEAD_THREE: 100,   // 眠三（一端被封）
        LIVE_TWO: 100,     // 活二（两端都空）
        DEAD_TWO: 10       // 眠二（一端被封）
    },
    // 防守分数
    DEFEND: {
        FIVE: 100000,
        LIVE_FOUR: 10000,  // 必须防守，否则对手必胜
        DEAD_FOUR: 1000,
        LIVE_THREE: 500,   // 优先防守
        DEAD_THREE: 100,
        LIVE_TWO: 50,
        DEAD_TWO: 10
    }
});

// ==================== AI 核心类 ====================

/**
 * 五子棋 AI 类
 */
class GomokuAI {
    /**
     * 构造函数
     * @param {string} difficulty - 难度级别
     * @param {number} boardSize - 棋盘大小
     */
    constructor(difficulty = Difficulty.MEDIUM, boardSize = 15) {
        this.difficulty = difficulty;
        this.config = DifficultyConfig[difficulty];
        this.boardSize = boardSize;
        this.thinkingTime = 0; // 记录思考时间
    }

    /**
     * 设置难度级别
     * @param {string} level - 难度级别
     */
    setDifficulty(level) {
        if (DifficultyConfig[level]) {
            this.difficulty = level;
            this.config = DifficultyConfig[level];
        } else {
            console.warn(`Invalid difficulty level: ${level}`);
        }
    }

    /**
     * 获取当前难度
     * @returns {string} 难度级别
     */
    getDifficulty() {
        return this.difficulty;
    }

    /**
     * 获取难度名称
     * @returns {string} 难度名称
     */
    getDifficultyName() {
        return this.config.name;
    }

    /**
     * 获取最佳落子位置
     * @param {number[][]} board - 棋盘状态
     * @param {number} aiColor - AI 的棋子颜色（1=黑，2=白）
     * @returns {{row: number, col: number}} 最佳落子位置
     */
    getBestMove(board, aiColor) {
        const startTime = performance.now();
        
        try {
            // 如果是第一步，下天元（棋盘中心）
            const moveCount = this.countStones(board);
            if (moveCount === 0) {
                return { row: 7, col: 7 };
            }

            // 如果是第二步，下在天元附近
            if (moveCount === 1) {
                return this.getSecondMove(board);
            }

            // 获取候选位置（优化：只考虑有棋子的周围位置）
            const candidatePositions = this.getCandidatePositions(board);
            
            if (candidatePositions.length === 0) {
                return { row: 7, col: 7 };
            }

            // 评估每个位置
            let bestScore = -Infinity;
            let bestMoves = [];
            const opponentColor = aiColor === 1 ? 2 : 1;

            for (const pos of candidatePositions) {
                const score = this.evaluatePosition(board, pos.row, pos.col, aiColor, opponentColor);
                
                if (score > bestScore) {
                    bestScore = score;
                    bestMoves = [pos];
                } else if (score === bestScore) {
                    bestMoves.push(pos);
                }
            }

            // 添加随机性（让 AI 不那么机械）
            if (bestMoves.length > 1 && Math.random() < this.config.randomness) {
                const randomIndex = Math.floor(Math.random() * bestMoves.length);
                this.thinkingTime = performance.now() - startTime;
                return bestMoves[randomIndex];
            }

            this.thinkingTime = performance.now() - startTime;
            return bestMoves[0] || { row: 7, col: 7 };
        } catch (error) {
            console.error('AI 计算出错:', error);
            this.thinkingTime = performance.now() - startTime;
            return { row: 7, col: 7 };
        }
    }

    /**
     * 获取第二步落子（在天元附近）
     * @param {number[][]} board - 棋盘状态
     * @returns {{row: number, col: number}} 落子位置
     */
    getSecondMove(board) {
        // 找到天元位置
        const center = 7;
        const neighbors = [
            { row: center - 1, col: center },
            { row: center + 1, col: center },
            { row: center, col: center - 1 },
            { row: center, col: center + 1 },
            { row: center - 1, col: center - 1 },
            { row: center - 1, col: center + 1 },
            { row: center + 1, col: center - 1 },
            { row: center + 1, col: center + 1 }
        ];

        // 过滤掉已有棋子的位置
        const available = neighbors.filter(pos => board[pos.row][pos.col] === 0);
        
        if (available.length > 0) {
            const randomIndex = Math.floor(Math.random() * available.length);
            return available[randomIndex];
        }

        return { row: center, col: center };
    }

    /**
     * 获取候选位置（优化性能）
     * 只考虑已有棋子周围 2 格范围内的空位
     * @param {number[][]} board - 棋盘状态
     * @returns {Array<{row: number, col: number}>} 候选位置列表
     */
    getCandidatePositions(board) {
        const candidates = new Set();
        const range = 2; // 搜索范围

        for (let r = 0; r < this.boardSize; r++) {
            for (let c = 0; c < this.boardSize; c++) {
                if (board[r][c] !== 0) {
                    // 添加周围空位
                    for (let dr = -range; dr <= range; dr++) {
                        for (let dc = -range; dc <= range; dc++) {
                            if (dr === 0 && dc === 0) continue;
                            const nr = r + dr;
                            const nc = c + dc;
                            if (this.isValid(nr, nc) && board[nr][nc] === 0) {
                                candidates.add(`${nr},${nc}`);
                            }
                        }
                    }
                }
            }
        }

        // 转换为对象数组
        return Array.from(candidates).map(str => {
            const [row, col] = str.split(',').map(Number);
            return { row, col };
        });
    }

    /**
     * 评估位置的综合分数
     * @param {number[][]} board - 棋盘状态
     * @param {number} row - 行
     * @param {number} col - 列
     * @param {number} aiColor - AI 颜色
     * @param {number} opponentColor - 对手颜色
     * @returns {number} 综合分数
     */
    evaluatePosition(board, row, col, aiColor, opponentColor) {
        // 计算进攻分数
        const attackScore = this.evaluateLine(board, row, col, aiColor);
        
        // 计算防守分数
        const defendScore = this.evaluateLine(board, row, col, opponentColor);
        
        // 综合评分
        return attackScore * this.config.attackWeight + 
               defendScore * this.config.defendWeight;
    }

    /**
     * 评估单条线的棋型分数
     * @param {number[][]} board - 棋盘状态
     * @param {number} row - 行
     * @param {number} col - 列
     * @param {number} color - 棋子颜色
     * @returns {number} 分数
     */
    evaluateLine(board, row, col, color) {
        let totalScore = 0;
        // 四个方向：水平、垂直、主对角线、副对角线
        const directions = [
            [0, 1],   // 水平
            [1, 0],   // 垂直
            [1, 1],   // 主对角线（左上到右下）
            [1, -1]   // 副对角线（右上到左下）
        ];

        for (const [dr, dc] of directions) {
            totalScore += this.evaluateDirection(board, row, col, dr, dc, color);
        }

        return totalScore;
    }

    /**
     * 评估单个方向的棋型
     * @param {number[][]} board - 棋盘状态
     * @param {number} row - 行
     * @param {number} col - 列
     * @param {number} dr - 行方向增量
     * @param {number} dc - 列方向增量
     * @param {number} color - 棋子颜色
     * @returns {number} 分数
     */
    evaluateDirection(board, row, col, dr, dc, color) {
        const result = this.scanDirection(board, row, col, dr, dc, color);
        return this.scorePattern(result, color);
    }

    /**
     * 扫描单个方向的棋子分布
     * @param {number[][]} board - 棋盘状态
     * @param {number} row - 行
     * @param {number} col - 列
     * @param {number} dr - 行方向增量
     * @param {number} dc - 列方向增量
     * @param {number} color - 棋子颜色
     * @returns {{count: number, openEnds: number, blockedFront: boolean, blockedBack: boolean}} 扫描结果
     */
    scanDirection(board, row, col, dr, dc, color) {
        let count = 1; // 包含当前落子
        let openEnds = 0;

        // 正向扫描
        let r = row + dr, c = col + dc;
        let blockedFront = false;
        while (this.isValid(r, c) && board[r][c] === color) {
            count++;
            r += dr;
            c += dc;
        }
        if (!this.isValid(r, c)) {
            blockedFront = true;
        } else if (board[r][c] !== 0) {
            blockedFront = true;
        } else {
            openEnds++;
        }

        // 反向扫描
        r = row - dr;
        c = col - dc;
        let blockedBack = false;
        while (this.isValid(r, c) && board[r][c] === color) {
            count++;
            r -= dr;
            c -= dc;
        }
        if (!this.isValid(r, c)) {
            blockedBack = true;
        } else if (board[r][c] !== 0) {
            blockedBack = true;
        } else {
            openEnds++;
        }

        return { count, openEnds, blockedFront, blockedBack };
    }

    /**
     * 根据棋型评分
     * @param {{count: number, openEnds: number}} result - 扫描结果
     * @param {number} color - 棋子颜色
     * @returns {number} 分数
     */
    scorePattern(result, color) {
        const { count, openEnds } = result;
        const scores = color === 1 ? PatternScores.ATTACK : PatternScores.DEFEND;

        if (count >= 5) return scores.FIVE;
        if (count === 4) {
            return openEnds === 2 ? scores.LIVE_FOUR : scores.DEAD_FOUR;
        }
        if (count === 3) {
            return openEnds === 2 ? scores.LIVE_THREE : scores.DEAD_THREE;
        }
        if (count === 2) {
            return openEnds === 2 ? scores.LIVE_TWO : scores.DEAD_TWO;
        }
        return 0;
    }

    /**
     * 检查坐标是否有效
     * @param {number} row - 行
     * @param {number} col - 列
     * @returns {boolean} 是否有效
     */
    isValid(row, col) {
        return row >= 0 && row < this.boardSize && 
               col >= 0 && col < this.boardSize;
    }

    /**
     * 统计棋盘上的棋子数量
     * @param {number[][]} board - 棋盘状态
     * @returns {number} 棋子数量
     */
    countStones(board) {
        let count = 0;
        for (let r = 0; r < this.boardSize; r++) {
            for (let c = 0; c < this.boardSize; c++) {
                if (board[r][c] !== 0) count++;
            }
        }
        return count;
    }

    /**
     * 获取思考时间（用于显示）
     * @returns {number} 思考时间（毫秒）
     */
    getThinkingTime() {
        return this.thinkingTime;
    }
}

// ==================== 导出 ====================

// 供外部使用
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { GomokuAI, Difficulty, DifficultyConfig };
}
