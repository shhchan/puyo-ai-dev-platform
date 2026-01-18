import pygame
from ..core.constants import GRID_WIDTH, GRID_HEIGHT, PUYO_SIZE, PuyoColor, VISIBLE_HEIGHT

class Renderer:
    def __init__(self, screen):
        self.screen = screen
        self.font = pygame.font.SysFont("Arial", 18)
        self.colors = {
            PuyoColor.RED: (255, 0, 0),
            PuyoColor.BLUE: (0, 0, 255),
            PuyoColor.GREEN: (0, 255, 0),
            PuyoColor.YELLOW: (255, 255, 0),
            PuyoColor.PURPLE: (128, 0, 128),
            PuyoColor.OJAMA: (200, 200, 200),
            PuyoColor.WALL: (100, 100, 100),
            PuyoColor.EMPTY: (0, 0, 0)
        }
        self.offset_x = 50
        self.offset_y = 50

    def draw(self, game_state):
        self.screen.fill((50, 50, 50)) # BG
        
        # Draw Field
        # Note: GRID_HEIGHT is 14. VISIBLE_HEIGHT is 12.
        # We usually only draw 0..11, or maybe 12 (ghost) for feedback?
        # Let's draw 0..12 (13 rows total, including hiden?)
        # Standard puyo: 12 visible rows. 13th is hidden. 
        # But for dev, let's see 13th? No, stick to specification.
        # But wait, y=0 is TOP. y=11 is BOTTOM visible.
        # y=12 is hidden row?
        # Re-reading: 
        # "可視領域は幅6x高さ12" -> visible 0-11.
        # "13段目(インデックス12): 隠し列" -> y=12? No, usually 1-indexed count.
        # If height=14. Index 0 to 13.
        # Standard: 13th row is index 0? or index 12?
        # Usually y=0 is top on screen.
        # Puyo usually spawns at top (hidden) and falls down.
        # If I draw y=0 at top of screen, and y=11 at bottom.
        # Then hidden row (index 12) is BELOW 11?? That sounds like y is going UP?
        # NO. If gravity goes to higher Y. Then y=0 is top, y=13 is bottom.
        # So hidden row is usually TOP. 
        # Let's check typical grid. 
        # User said: "14段目(インデックス13): 画面外。ここに置かれたぷよは落下しません。"
        # This implies Ind 13 is the specific "do not drop" zone.
        # "見えていない13段目があります" -> Index 12.
        # "見えているフィールドの高さは確かに12" -> Index 0-11? Or 1-12?
        # If gravity pulls DOWN.
        # Then bottom is MAX Y.
        # If hidden row is AT TOP usually (spawn area).
        # Then indices 0,1 might be hidden? 
        # But user said "13段目(インデックス12)" is hidden. 
        # If 1-indexed 1st row is bottom? No.
        # "12 visible rows".
        # Let's assume indices 0-11 are NORMAL VISIBLE rows.
        # Index 12 is HIDDEN (13th row).
        # Index 13 is GHOST/OUT (14th row).
        # This implies 0 is TOP visible, 11 is BOTTOM visible.
        # 12 is BELOW bottom? That means hidden row is at bottom??
        # Usually Puyo hidden row is at TOP (spawn).
        # User text: "14段目にぷよを設置した場合は...落下してこない" -> Sounds like top?
        # If I stack puyo, they go UP.
        # So "1st row" is bottom. "12th row" is top.
        # User said "13段目(インデックス12) ... 隠し列". This is above 12th row.
        # So indices are likely:
        # 0: bottom
        # ...
        # 11: top visible
        # 12: hidden (13th)
        # 13: ghost (14th)
        
        # BUT, my gravity logic in Field class assumed:
        # "Fall to higher y". -> y=0 top, y=13 bottom.
        # If so, y=13 is bottom-most.
        # Use wants "13段目 (Index 12)" to be hidden.
        # If I stick to Pygame coords (y=0 top), then 
        # Visually:
        # Screen Top
        # [Index ?] 
        # ...
        # [Index ?]
        # Screen Bottom
        
        # If user means standard Puyo rules:
        # Rows 1-12 are visible. 13 is hidden (above 12). 14 is ghost (above 13).
        # So indices 0-11 are visible?
        # If 0 is bottom, 11 is top.
        # Then 12 is above top. 13 is above 12.
        # Then Gravity increases Y (down)? No, gravity decreases Y (towards 0).
        
        # Let's look at my Field.drop_puyo:
        # "Place at bottom ... target_y = start_y + i"
        # I assumed larger Y is bottom. (Standard 2D array).
        # So y=0 is TOP. y=13 is BOTTOM.
        
        # Implementation Plan said: "13段目(インデックス12): 隠し列".
        # "14段目(インデックス13): 画面外...落下しない".
        # If this 14th row is "above" everything, it should be index 0 (if 0 is top).
        # If user says Index 13 is 14th row. That means 0-12 are 1-13th rows.
        # If 1-12 are visible. That means 13th is hidden.
        # If gravity is DOWN.
        # The "Hidden" row is usually at the TOP where puyos spawn.
        # So if gravity goes DOWN (increasing Y), then TOP (small Y) should be hidden?
        # Maybe Index 0 is hidden?
        
        # Let's assume the user meant logical rows 1..14.
        # And gave indices: Ind 12 is 13th. Ind 13 is 14th.
        # This aligns with 0-indexed array.
        # If 13th/14th are "Hidden/Ghost" (Spawn area), they should be at the TOP visually.
        # If my array is `grid[y][x]`.
        # If y=0 is TOP.
        # Then Ind 0, 1 should be the hidden ones?
        # But User specifically mapped "Index 12" to "13th row".
        # This implies the user counts from Bottom? Or maybe they want me to flip it.
        # Let's assume:
        # Index 0 = Bottom?
        # Start from bottom logic?
        
        # Let's look at standard puyo dev.
        # Often: y=0 is top (hidden), y=12 is bottom.
        # Total 13-14 rows.
        # If user wants Ind 12/13 to be the special ones.
        # I will treat Index 12, 13 as "Above Top" logically, but in array they are at end?
        # Or maybe "13th row" means "13th from bottom"?
        # Yes, usually "1st row" is floor.
        # So Index 0 = Floor?
        # My Field.drop_puyo Logic:
        # "Place at bottom (12)... target_y = start_y + i"
        # It treats LARGER Y as BOTTOM.
        # So y=12 is bottom in my code.
        # If Index 0 is bottom, then gravity should decrease Y.
        
        # Conclusion: My Field.drop_puyo logic (Large Y = Bottom) CONFLICTS with "Index 12/13 are top/hidden" unless I render upside down?
        # No, if Index 12 is hidden, and it's "above" Index 11 (top visible).
        # Then Index 12 > Index 11.
        # But visually "Above" means Smaller Y in screen coords.
        # So I have a mapping problem.
        # Let's stick to logical indices:
        # 0..11 : Visible Field.
        # 12 : Hidden Row (Spawn).
        # 13 : Ghost Row.
        # AND Visually: 13 is TOP. 0 is BOTTOM.
        # So Gravity goes: 13 -> 12 -> 11 ... -> 0.
        # So logic: Drop means Y decreases.
        
        # Let's RE-WRITE Field logic or Renderer logic?
        # Retaining Field logic (Large Y = Bottom) is standard for 2D arrays (y=0 top).
        # But here "Index 12" is 13th row (Hidden). Spawning happens there.
        # So Spawning happens at HIGH index. 
        # And gravity pulls to LOW index? (If 1st row is bottom).
        # YES.
        
        # ACTION: I need to FIX Field logic to match this coordinate system OR fix my understanding.
        # Let's assume: 
        # Index 0 = Bottom (Row 1).
        # Index 1 = Row 2.
        # ...
        # Index 11 = Row 12 (Top Visible).
        # Index 12 = Row 13 (Hidden).
        # Index 13 = Row 14 (Ghost).
        
        # Then Gravity: Moves Puyo from Y+1 to Y.
        # Drop logic: Scan from Bottom (0) to Top (13).
        # Find empty at Y. Find non-empty at Y+k. Move to Y.
        
        # BUT, my previous Field code was:
        # "Place at bottom of 0..12 ... target_y = start_y + i"
        # It pushed to HIGH Y.
        
        # DECISION: I will update Field logic to respect "Index 0 is Bottom".
        # Why? Because user said "Index 12 is 13th row (Hidden)".
        # Logic: 1st row is bottom. 
        
        # Wait, usually in programming y=0 is top.
        # If I use y=0 as TOP (High Visible), and y=11 as BOTTOM (Floor).
        # Then y=12 is UNDER FLOOR?
        # Then "Index 12 is 13th row". 13th row is usually TOP (Spawn).
        # So this contradicts y=0 is top.
        
        # Let's USE y=0 as TOP (Row 14/13/12...?). 
        # No that's confusing.
        
        # Let's USE y=0 as TOP of ARRAY.
        # But LOGICALLY:
        # Ind 0 : Row 1 (Bottom)? 
        # Ind 13: Row 14 (Top)?
        # OK, let's Check Field Logic I wrote.
        # "drop_puyo... for y in range(GRID_HEIGHT - 1)... place at bottom..."
        # I wrote it assuming Y=max is bottom.
        
        # I should flip Renderer to draw:
        # Screen Y grows Down.
        # Logical Y (0..11) mapped to Screen.
        # If Logical 0 is Bottom. Screen Y should be Large.
        # If Logical 11 is Top. Screen Y should be Small.
        # Logical 12 (Hidden). Screen Y even Smaller (negative or offscreen).
        
        # SO: I will treat the Grid as: Index 0 = Bottom.
        
        # Renderer Mapping:
        # world_y (0..13)
        # screen_y = BASE_Y - (world_y * PUYO_SIZE)
        
        # I need to Quick Update Field.py?
        # Yes, I should probably check Field.py again.
        # I wrote: 
        # "for y in range(GRID_HEIGHT - 2, -1, -1)..." (scan from bottom?)
        # "Place at bottom of 0..12... target_y = start_y + i" (High Y).
        # My Field.py assumes High Y is Bottom.
        # So Index 12 is Bottom-most in my code?
        # If so, Index 12 cannot be "Hidden/Spawn" (Top).
        
        # I must REWRITE Field.py to match "Index 12 is Top/Hidden".
        # OR "Index 0 is Top/Hidden".
        # If "Index 12 is 13th row". And 13th row is Top.
        # Then HIGH INDEX = TOP.
        # And LOW INDEX = BOTTOM.
        # Gravity: High -> Low.
        
        # I will rewrite Field logic in `field.py` using `replace_file_content` before running Main.
        # Or I can just handle mapping in Renderer if I accept Index 0 = Top, Index 12 = Bottom?
        # But User said "Index 12 is 13th row". 
        # If I map Index 12 to Bottom, then "13th row" is Bottom?
        # "13段目" usually means Top (Stack grows up to 13).
        # So "Index 12 is 13th" implies Index 12 is High up.
        
        # OK, I will rewrite Field to:
        # Index 0 = Bottom (Row 1).
        # Index 13 = Top (Row 14).
        # Gravity: Falls from High Index to Low Index.
        
        # Renderer:
        # y=0 (Bottom) -> Screen Bottom.
        # y=12 (Top) -> Screen Top.
        
        for y in range(GRID_HEIGHT):
            for x in range(GRID_WIDTH):
                puyo = game_state.field.get_puyo(x, y)
                if not puyo.is_empty():
                    color = self.colors.get(puyo.color, (255, 255, 255))
                    # Map x, y to screen
                    # x=0 -> Left
                    # y=0 -> Bottom
                    
                    sx = self.offset_x + x * PUYO_SIZE
                    # sy: VISIBLE_HEIGHT-1 is top visible row (Index 11).
                    # Index 0 is bottom.
                    # screen_y = OFFSET + (VISIBLE_HEIGHT - 1 - y) * PUYO_SIZE
                    
                    # Wait, if y can be 12 (Hidden).
                    # If y=12. 11-12 = -1. Offscreen Top. Correct.
                    
                    sy = self.offset_y + (VISIBLE_HEIGHT - 1 - y) * PUYO_SIZE
                    
                    pygame.draw.rect(self.screen, color, (sx, sy, PUYO_SIZE, PUYO_SIZE))
                    pygame.draw.rect(self.screen, (0,0,0), (sx, sy, PUYO_SIZE, PUYO_SIZE), 1)

        # Draw Control Puyo
        if game_state.current_puyo_1 and game_state.puyo_y is not None:
             # Axis
             # puyo_y is in Grid Coords (0=Bottom?)
             # Wait, GameState spawn:
             # "Spawn at 2, 11" (Top visible?) or 12??
             # I need to update GameState spawn logic too.
             
             p1 = game_state.current_puyo_1
             cx, cy = game_state.puyo_x, game_state.puyo_y
             color = self.colors.get(p1.color)
             sx = self.offset_x + cx * PUYO_SIZE
             sy = self.offset_y + (VISIBLE_HEIGHT - 1 - cy) * PUYO_SIZE
             pygame.draw.rect(self.screen, color, (sx, sy, PUYO_SIZE, PUYO_SIZE))
             
             # Sub
             ox, oy = game_state.get_sub_puyo_offset(game_state.puyo_rot)
             # Rotation Logic in GameState needs to match "Up is +Y" or "Up is -Y"?
             # If y=0 is Bottom. UP is +Y.
             # My GameState.get_sub_puyo_offset:
             # UP: (0, -1). This means DECREASE Y.
             # If Decrease Y means "Go Down" (towards 0), then UP direction places sub BELOW axis?
             # No, "UP" usually means visually up.
             # If Visually Up means Greater Y (Index), then UP should be (0, +1).
             
             # I need to fix GameState and Field together.
             
             tx, ty = cx + ox, cy + oy
             p2 = game_state.current_puyo_2
             color2 = self.colors.get(p2.color)
             sx2 = self.offset_x + tx * PUYO_SIZE
             sy2 = self.offset_y + (VISIBLE_HEIGHT - 1 - ty) * PUYO_SIZE
             pygame.draw.rect(self.screen, color2, (sx2, sy2, PUYO_SIZE, PUYO_SIZE))

        # Score
        score_surf = self.font.render(f"Score: {game_state.score}", True, (255, 255, 255))
        self.screen.blit(score_surf, (10, 10))
        
        pygame.display.flip()
