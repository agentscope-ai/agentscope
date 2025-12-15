import os
import glob
import shutil

ASSET_DIR = r"examples\agent\tiny_town\assets"
PATTERNS = {
    "grass_tile_*.png": "grass_tile.png",
    "house_sprite_*.png": "house_sprite.png",
    "shop_sprite_*.png": "shop_sprite.png",
    "villager_sprite_*.png": "villager_sprite.png"
}

def clean_assets():
    print(f"Cleaning assets in {ASSET_DIR}...")
    if not os.path.exists(ASSET_DIR):
        print("Asset dir not found!")
        return

    for pattern, target in PATTERNS.items():
        search_path = os.path.join(ASSET_DIR, pattern)
        matches = glob.glob(search_path)
        
        target_path = os.path.join(ASSET_DIR, target)
        
        # If target already exists, maybe we are done? 
        # But if we have duplicates, we should prefer the timestamped one?
        # Let's just take the first match.
        
        if matches:
            src = matches[0]
            print(f"Renaming {src} -> {target_path}")
            if os.path.exists(target_path):
                os.remove(target_path) # Overwrite old one
            os.rename(src, target_path)
        else:
            print(f"No match for {pattern}")

if __name__ == "__main__":
    clean_assets()
