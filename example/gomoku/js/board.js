/**
 * 棋盘渲染模块
 * Board Rendering Module
 * 
 * 负责棋盘的视觉渲染，包括：
 * - 棋盘网格绘制（精确的交叉点网格）
 * - 星位标记
 * - 棋子渲染
 * - 动画效果
 */

class BoardRenderer {
    /**
     * 构造函数
     * @param {HTMLElement} boardElement - 棋盘 DOM 元素
     * @param {HTMLElement} overlayElement - 覆盖层 DOM 元素
     * @param {number} size - 棋盘大小（15）
     */
    constructor(boardElement, overlayElement, size = 15) {
        this.boardElement = boardElement;
        this.overlayElement = overlayElement;
        this.size = size;
        
        // 星位坐标（15x15 棋盘的标准星位）
        this.starPoints = [
            [3, 3], [3, 11], [11, 3], [11, 11], [7, 7]
        ];
        
        // 初始化棋盘
        this.init();
    }
    
    /**
     * 初始化棋盘
     */
    init() {
        this.renderGrid();
        this.renderStarPoints();
        this.bindEvents();
    }
    
    /**
     * 绘制网格线
     * 使用 JavaScript 动态绘制，确保网格线和棋子位置完全对齐
     */
    renderGrid() {
        // 清除现有的网格线
        const existingGrid = this.boardElement.querySelector('.grid-lines');
        if (existingGrid) {
            existingGrid.remove();
        }
        
        // 创建网格线容器
        const gridContainer = document.createElement('div');
        gridContainer.className = 'grid-lines';
        
        const rect = this.boardElement.getBoundingClientRect();
        const cellSize = rect.width / this.size;
        const margin = cellSize / 2; // 边缘留白
        
        // 绘制水平线
        for (let i = 0; i < this.size; i++) {
            const line = document.createElement('div');
            line.className = 'grid-line grid-line-h';
            line.style.top = `${margin + i * cellSize}px`;
            line.style.left = `${margin}px`;
            line.style.right = `${margin}px`;
            gridContainer.appendChild(line);
        }
        
        // 绘制垂直线
        for (let i = 0; i < this.size; i++) {
            const line = document.createElement('div');
            line.className = 'grid-line grid-line-v';
            line.style.left = `${margin + i * cellSize}px`;
            line.style.top = `${margin}px`;
            line.style.bottom = `${margin}px`;
            gridContainer.appendChild(line);
        }
        
        this.boardElement.appendChild(gridContainer);
    }
    
    /**
     * 绑定事件
     */
    bindEvents() {
        this.boardElement.addEventListener('click', (e) => this.handleClick(e));
    }
    
    /**
     * 处理点击事件
     * @param {MouseEvent} e - 点击事件
     * @returns {Object|null} - 点击的坐标 {row, col} 或 null
     */
    handleClick(e) {
        const rect = this.boardElement.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        
        // 计算格子大小和边缘留白
        const cellSize = rect.width / this.size;
        const margin = cellSize / 2;
        
        // 计算点击的是哪个交叉点
        // 交叉点位置：margin + i * cellSize
        // 点击位置 x 对应的列：Math.round((x - margin) / cellSize)
        const col = Math.round((x - margin) / cellSize);
        const row = Math.round((y - margin) / cellSize);
        
        // 验证坐标是否在有效范围内
        if (row >= 0 && row < this.size && col >= 0 && col < this.size) {
            return { row, col };
        }
        
        return null;
    }
    
    /**
     * 渲染星位标记
     */
    renderStarPoints() {
        // 清除现有的星位
        const existingStars = this.boardElement.querySelectorAll('.star-point');
        existingStars.forEach(star => star.remove());
        
        // 获取棋盘尺寸
        const rect = this.boardElement.getBoundingClientRect();
        const cellSize = rect.width / this.size;
        const margin = cellSize / 2;
        
        // 渲染每个星位
        this.starPoints.forEach(([row, col]) => {
            const star = document.createElement('div');
            star.className = 'star-point';
            
            // 计算星位位置（居中于交叉点）
            const x = margin + col * cellSize;
            const y = margin + row * cellSize;
            
            star.style.left = `${x}px`;
            star.style.top = `${y}px`;
            
            this.boardElement.appendChild(star);
        });
    }
    
    /**
     * 放置棋子
     * @param {number} row - 行坐标
     * @param {number} col - 列坐标
     * @param {string} color - 棋子颜色 'black' 或 'white'
     * @param {boolean} animate - 是否显示动画
     * @returns {HTMLElement} - 棋子 DOM 元素
     */
    placeStone(row, col, color, animate = true) {
        // 验证坐标
        if (row < 0 || row >= this.size || col < 0 || col >= this.size) {
            throw new Error(`Invalid position: (${row}, ${col})`);
        }
        
        if (color !== 'black' && color !== 'white') {
            throw new Error(`Invalid color: ${color}`);
        }
        
        // 创建棋子元素
        const stone = document.createElement('div');
        stone.className = `stone ${color}`;
        if (animate) {
            stone.classList.add('placed');
        }
        
        // 设置位置 - 棋子落在交叉点上
        const rect = this.boardElement.getBoundingClientRect();
        const cellSize = rect.width / this.size;
        const margin = cellSize / 2;
        const x = margin + col * cellSize;
        const y = margin + row * cellSize;
        
        stone.style.left = `${x}px`;
        stone.style.top = `${y}px`;
        
        // 存储坐标信息
        stone.dataset.row = row;
        stone.dataset.col = col;
        
        this.boardElement.appendChild(stone);
        
        return stone;
    }
    
    /**
     * 标记最后落子
     * @param {number} row - 行坐标
     * @param {number} col - 列坐标
     */
    markLastMove(row, col) {
        // 清除之前的标记
        this.clearLastMove();
        
        // 找到对应的棋子并添加标记
        const stone = this.boardElement.querySelector(
            `.stone[data-row="${row}"][data-col="${col}"]`
        );
        
        if (stone) {
            stone.classList.add('last-move');
        }
    }
    
    /**
     * 清除最后落子标记
     */
    clearLastMove() {
        const lastMove = this.boardElement.querySelector('.stone.last-move');
        if (lastMove) {
            lastMove.classList.remove('last-move');
        }
    }
    
    /**
     * 高亮获胜连线
     * @param {Array} positions - 获胜位置的数组 [{row, col}, ...]
     */
    highlightWinningLine(positions) {
        // 清除之前的高亮
        this.clearWinningLine();
        
        // 为每个获胜位置添加高亮
        positions.forEach(({ row, col }) => {
            const highlight = document.createElement('div');
            highlight.className = 'win-highlight';
            
            const rect = this.boardElement.getBoundingClientRect();
            const cellSize = rect.width / this.size;
            const margin = cellSize / 2;
            const x = margin + col * cellSize;
            const y = margin + row * cellSize;
            
            highlight.style.left = `${x}px`;
            highlight.style.top = `${y}px`;
            
            this.overlayElement.appendChild(highlight);
        });
    }
    
    /**
     * 清除获胜连线高亮
     */
    clearWinningLine() {
        const highlights = this.overlayElement.querySelectorAll('.win-highlight');
        highlights.forEach(highlight => highlight.remove());
    }
    
    /**
     * 清除所有棋子
     */
    clearStones() {
        const stones = this.boardElement.querySelectorAll('.stone');
        stones.forEach(stone => stone.remove());
    }
    
    /**
     * 移除指定位置的棋子
     * @param {number} row - 行坐标
     * @param {number} col - 列坐标
     */
    removeStone(row, col) {
        const stone = this.boardElement.querySelector(
            `.stone[data-row="${row}"][data-col="${col}"]`
        );
        
        if (stone) {
            stone.remove();
        }
    }
    
    /**
     * 获取指定位置的棋子
     * @param {number} row - 行坐标
     * @param {number} col - 列坐标
     * @returns {HTMLElement|null} - 棋子 DOM 元素或 null
     */
    getStone(row, col) {
        return this.boardElement.querySelector(
            `.stone[data-row="${row}"][data-col="${col}"]`
        );
    }
    
    /**
     * 重新渲染网格和星位（用于窗口大小变化时）
     */
    refreshGrid() {
        this.renderGrid();
        this.renderStarPoints();
    }
}

// 导出类
if (typeof module !== 'undefined' && module.exports) {
    module.exports = BoardRenderer;
}
