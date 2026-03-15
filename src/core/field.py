from .constants import GRID_WIDTH, GRID_HEIGHT, VISIBLE_HEIGHT, PuyoColor
from .puyo import Puyo

class Field:
    def __init__(self):
        self.width = GRID_WIDTH
        self.height = GRID_HEIGHT
        # grid[y][x]. y=0 is BOTTOM. y=13 is TOP.
        self.grid = [[Puyo(PuyoColor.EMPTY) for _ in range(self.width)] for _ in range(self.height)]

    def get_puyo(self, x, y):
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.grid[y][x]
        return Puyo(PuyoColor.WALL)

    def place_puyo(self, x, y, puyo):
        if 0 <= x < self.width and 0 <= y < self.height:
            self.grid[y][x] = puyo
            return True
        return False

    def drop_puyo(self):
        """
        Apply gravity. Puyo falls from Higher Y to Lower Y.
        Index 13 (Top-most) does NOT fall?
        User said "14段目(Ind 13)... 落下してこない".
        So we process 0..12.
        """
        moved = False
        for x in range(self.width):
            # Collect puyos in 0..12
            col_puyos = []
            for y in range(GRID_HEIGHT - 1): # 0..12
                puyo = self.grid[y][x]
                if not puyo.is_empty():
                    col_puyos.append(puyo)
            
            # Reconstruct column
            # We want them at the BOTTOM (0, 1, 2...)
            
            # Check if this changes anything
            # Construct new state
            new_col = []
            for p in col_puyos:
                new_col.append(p)
            while len(new_col) < (GRID_HEIGHT - 1):
                new_col.append(Puyo(PuyoColor.EMPTY))
            
            # new_col has puyos at start (Low indices).
            
            # Compare and update
            for y in range(GRID_HEIGHT - 1):
                if self.grid[y][x].color != new_col[y].color:
                    moved = True
                    self.grid[y][x] = new_col[y]
                    
        return moved


    def check_vanish(self):
        """
        Check for connected puyos (4 or more).
        Returns a set of coordinates to vanish.
        Only visible rows (index 0..11) are removable.
        Hidden rows (13th/14th) are excluded.
        """
        groups = self.get_vanish_groups()
        vanish_group = set()
        for group in groups:
            vanish_group.update(group)
        return vanish_group

    def get_vanish_groups(self):
        """
        Returns list of connected groups (size >= 4) to vanish.
        Only visible rows (index 0..11) are removable.
        Hidden rows (13th/14th) are excluded.
        """
        visited = set()
        vanish_groups = []
        
        def get_connected(start_x, start_y, color):
            stack = [(start_x, start_y)]
            group = set()
            while stack:
                cx, cy = stack.pop()
                if (cx, cy) in group:
                    continue
                group.add((cx, cy))
                
                for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < self.width and 0 <= ny < VISIBLE_HEIGHT:
                        target = self.grid[ny][nx]
                        if not target.is_empty() and target.color == color and target.color != PuyoColor.OJAMA:
                            if (nx, ny) not in group:
                                stack.append((nx, ny))
            return group

        for y in range(VISIBLE_HEIGHT):
            for x in range(self.width):
                if (x, y) in visited:
                    continue
                
                puyo = self.grid[y][x]
                if puyo.is_empty() or puyo.color == PuyoColor.OJAMA or puyo.color == PuyoColor.WALL:
                    continue
                
                group = get_connected(x, y, puyo.color)
                visited.update(group)
                
                if len(group) >= 4:
                    vanish_groups.append(group)
                    
        return vanish_groups

    def remove_puyos(self, coords):
        ojama_to_clear = set()
        for (x, y) in coords:
            for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                nx, ny = x + dx, y + dy
                if 0 <= nx < self.width and 0 <= ny < VISIBLE_HEIGHT:
                    if self.grid[ny][nx].is_color_puyo() == False and self.grid[ny][nx].color == PuyoColor.OJAMA:
                        ojama_to_clear.add((nx, ny))
        
        all_clear = coords.union(ojama_to_clear)
        for (x, y) in all_clear:
            self.grid[y][x] = Puyo(PuyoColor.EMPTY)
            
        return len(coords) > 0
