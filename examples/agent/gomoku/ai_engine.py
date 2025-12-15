# -*- coding: utf-8 -*-
"""Local Minimax AI Engine for Gomoku."""
import random

# Scores
WIN = 100000
LIVE_4 = 10000
DEAD_4 = 5000
LIVE_3 = 1000
DEAD_3 = 500
LIVE_2 = 200
DEAD_2 = 100

class MinimaxAI:
    def __init__(self, difficulty="normal"):
        self.difficulty = difficulty.lower()
        if self.difficulty == "easy":
            self.depth = 1
        elif self.difficulty == "hard":
            self.depth = 2 # Depth 3+ is very slow in Python without heavy optimization
        else: # normal
            self.depth = 2

    def get_move(self, grid):
        """Returns (r, c) for the best move."""
        # 1. Check strict immediate threats (0-depth) for instant reaction
        # This makes the AI feel "snappy" and prevents stupid losses even at low depth
        threat = self.find_immediate_threat(grid)
        if threat: return threat

        # 2. Minimax
        best_score = -float('inf')
        best_moves = []
        
        # Candidate generation: only consider spots near existing stones (1-2 cells radius)
        candidates = self.get_candidates(grid)
        if not candidates:
            # First move: center or near center
            center = len(grid) // 2
            return (center, center)

        # Allow randomization for variety in easy/normal
        for r, c in candidates:
            grid[r][c] = 'O'
            score = self.minimax(grid, self.depth - 1, False, -float('inf'), float('inf'))
            grid[r][c] = '.'
            
            if score > best_score:
                best_score = score
                best_moves = [(r, c)]
            elif score == best_score:
                best_moves.append((r, c))
                
        if best_moves:
            return random.choice(best_moves)
        
        # Fallback
        return candidates[0] if candidates else (7, 7)

    def find_immediate_threat(self, grid):
        """Check for instant win or forced block."""
        empty_spots = self.get_candidates(grid)
        
        # 1. Can AI Win Now?
        for r, c in empty_spots:
            grid[r][c] = 'O'
            if self.check_win(grid, r, c, 'O'):
                grid[r][c] = '.'
                return (r, c)
            grid[r][c] = '.'
            
        # 2. Must AI Block Player Win?
        for r, c in empty_spots:
            grid[r][c] = 'X'
            if self.check_win(grid, r, c, 'X'):
                grid[r][c] = '.'
                return (r, c)
            grid[r][c] = '.'
            
        return None

    def minimax(self, grid, depth, is_maximizing, alpha, beta):
        if depth == 0:
            return self.evaluate_board(grid)
            
        candidates = self.get_candidates(grid)
        if not candidates: return 0

        if is_maximizing:
            max_eval = -float('inf')
            for r, c in candidates:
                grid[r][c] = 'O'
                if self.check_win(grid, r, c, 'O'):
                    grid[r][c] = '.'
                    return WIN # Instant win in recursion
                eval_val = self.minimax(grid, depth - 1, False, alpha, beta)
                grid[r][c] = '.'
                max_eval = max(max_eval, eval_val)
                alpha = max(alpha, eval_val)
                if beta <= alpha: break
            return max_eval
        else:
            min_eval = float('inf')
            for r, c in candidates:
                grid[r][c] = 'X'
                if self.check_win(grid, r, c, 'X'):
                    grid[r][c] = '.'
                    return -WIN # Instant loss
                eval_val = self.minimax(grid, depth - 1, True, alpha, beta)
                grid[r][c] = '.'
                min_eval = min(min_eval, eval_val)
                beta = min(beta, eval_val)
                if beta <= alpha: break
            return min_eval

    def evaluate_board(self, grid):
        """
        Evaluate the board from 'O' perspective.
        Score = (AI Lines) - (Player Lines * Weights)
        """
        score = 0
        # Simple heuristic: scan lines and count consecutive stones
        # Full scan is expensive; we can optimize by only scanning affected areas, 
        # but for Python prototype, we scan.
        # To simplify: we use a simpler pattern matching or just count standard patterns.
        
        # Vertical, Horizontal, Diagonal
        lines = self.get_all_lines(grid)
        for line in lines:
            score += self.evaluate_line(line, 'O')
            score -= self.evaluate_line(line, 'X') * 1.2 # Defense is priority
            
        return score

    def evaluate_line(self, line, player):
        s_line = "".join(line)
        score = 0
        
        # Patterns
        p = player
        
        if f"{p}{p}{p}{p}{p}" in s_line: return WIN
        if f".{p}{p}{p}{p}." in s_line: score += LIVE_4
        if f"{p}{p}{p}{p}." in s_line or f".{p}{p}{p}{p}" in s_line: score += DEAD_4
        if f".{p}{p}{p}." in s_line: score += LIVE_3
        if f"{p}{p}{p}." in s_line or f".{p}{p}{p}" in s_line: score += DEAD_3
        if f".{p}{p}." in s_line: score += LIVE_2
        
        return score

    def get_all_lines(self, grid):
        lines = []
        size = len(grid)
        # Rows
        for r in range(size):
            lines.append(grid[r])
        # Cols
        for c in range(size):
            lines.append([grid[r][c] for r in range(size)])
        # Diagonals
        # Top-left to bottom-right
        for k in range(size * 2):
            diag = []
            for j in range(k + 1):
                i = k - j
                if 0 <= i < size and 0 <= j < size:
                    diag.append(grid[i][j])
            if len(diag) >= 5: lines.append(diag)
        # Top-right to bottom-left
        for k in range(size * 2):
            diag = []
            for j in range(k + 1):
                i = k - j
                if 0 <= i < size and 0 <= size - 1 - j < size:
                     diag.append(grid[i][size - 1 - j])
            if len(diag) >= 5: lines.append(diag)
        
        return lines

    def get_candidates(self, grid):
        """Return empty spots that are adjacent to existing stones."""
        size = len(grid)
        candidates = set()
        occupied = False
        
        for r in range(size):
            for c in range(size):
                if grid[r][c] != '.':
                    occupied = True
                    # Check neighbors
                    for dr in [-1, 0, 1]:
                        for dc in [-1, 0, 1]:
                            if dr == 0 and dc == 0: continue
                            nr, nc = r+dr, c+dc
                            if 0 <= nr < size and 0 <= nc < size and grid[nr][nc] == '.':
                                candidates.add((nr, nc))
        
        if not occupied:
            return [(size//2, size//2)]
            
        return list(candidates)

    def check_win(self, grid, r, c, player):
        size = len(grid)
        directions = [(0, 1), (1, 0), (1, 1), (1, -1)]
        for dr, dc in directions:
            count = 1
            for i in range(1, 5):
                nr, nc = r + dr*i, c + dc*i
                if 0 <= nr < size and 0 <= nc < size and grid[nr][nc] == player:
                    count += 1
                else: break
            for i in range(1, 5):
                nr, nc = r - dr*i, c - dc*i
                if 0 <= nr < size and 0 <= nc < size and grid[nr][nc] == player:
                    count += 1
                else: break
            if count >= 5: return True
        return False
