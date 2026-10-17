# generate_icon.py
# Draws a simple satellite-dish icon (packaging/icon.png, 512x512) using the
# same palette as static/style.css, since there's no existing project logo
# to reuse - the demo images in images/ are scan-result photos, not an app
# icon. build_deb.sh resizes this one master into the icon theme sizes a
# .desktop file needs.
#
# Usage: python3 packaging/generate_icon.py

import math
import os

from PIL import Image, ImageDraw

SIZE = 512
BG = (30, 30, 46, 255)          # --bg
DISH = (137, 180, 250, 255)     # --accent
DISH_SHADE = (108, 143, 209, 255)
ARM = (205, 214, 244, 255)      # --text
SIGNAL = (166, 227, 161, 255)   # --good

img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# Rounded-square background, matching the dark theme the web UI already uses.
pad = 8
draw.rounded_rectangle([pad, pad, SIZE - pad, SIZE - pad], radius=72, fill=BG)

cx, cy = SIZE * 0.45, SIZE * 0.62

# Dish bowl: a wide ellipse with a slightly smaller, offset ellipse cut out
# of it to suggest a concave parabolic surface viewed at an angle.
bowl_w, bowl_h = SIZE * 0.62, SIZE * 0.30
draw.ellipse(
    [cx - bowl_w / 2, cy - bowl_h / 2, cx + bowl_w / 2, cy + bowl_h / 2],
    fill=DISH,
)
inner_w, inner_h = bowl_w * 0.82, bowl_h * 0.55
inner_cy = cy - bowl_h * 0.12
draw.ellipse(
    [cx - inner_w / 2, inner_cy - inner_h / 2, cx + inner_w / 2, inner_cy + inner_h / 2],
    fill=DISH_SHADE,
)

# Feed arm + LNB, reaching up from the dish's center toward the upper right.
feed_x, feed_y = cx + bowl_w * 0.30, cy - bowl_h * 1.65
draw.line([cx, cy - bowl_h * 0.18, feed_x, feed_y], fill=ARM, width=14)
feed_r = SIZE * 0.035
draw.ellipse([feed_x - feed_r, feed_y - feed_r, feed_x + feed_r, feed_y + feed_r], fill=ARM)

# Signal arcs radiating from the feed, suggesting RF reception.
for i, r in enumerate((0.09, 0.16, 0.23)):
    radius = SIZE * r
    bbox = [feed_x - radius, feed_y - radius, feed_x + radius, feed_y + radius]
    draw.arc(bbox, start=-55, end=35, fill=SIGNAL, width=10)

out_path = os.path.join(os.path.dirname(__file__), "icon.png")
img.save(out_path)
print(f"Wrote {out_path}")
