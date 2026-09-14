# Onward 品牌 PWA 图标：蓝底 + 白色双菱形 logo（按 Onward Employment 名片重绘）
from PIL import Image, ImageDraw, ImageFont, ImageOps

BLUE = (58, 63, 193)          # 名片蓝
WHITE = (255, 255, 255)

def rounded(size, radius_ratio=0.0):
    """渐变无、纯色蓝底圆角画布"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * radius_ratio), fill=BLUE + (255,))
    return img, d

def draw_logo(d, s):
    """双菱形 logo：左菱形描边 + 右菱形描边(内含实心小菱形)，白色粗线条"""
    w = s
    a = int(0.20 * s)        # 菱形半对角
    sw = int(0.048 * s)      # 描边宽度
    ly, rx = 0.50 * s, 0.545 * s
    lc = (0.435 * s, ly)     # 左菱形中心
    rc = (rx, ly)            # 右菱形中心（略偏右下重叠）

    def diamond_pts(c, half):
        return [(c[0], c[1] - half), (c[0] + half, c[1]), (c[0], c[1] + half), (c[0] - half, c[1])]

    # 左菱形描边
    d.line(diamond_pts(lc, a) + [diamond_pts(lc, a)[0]], fill=WHITE, width=sw, joint="curve")
    # 右菱形描边
    d.line(diamond_pts(rc, a) + [diamond_pts(rc, a)[0]], fill=WHITE, width=sw, joint="curve")
    # 右菱形中心实心小菱形
    d.polygon(diamond_pts(rc, int(0.055 * s)), fill=WHITE)

def make_icon(size, path):
    img, d = rounded(size, radius_ratio=0.22)
    # 放大到 4x 绘制再缩回，抗锯齿
    big = size * 4
    bi, bd = rounded(big, radius_ratio=0.22)
    draw_logo(bd, big)
    # 圆角蒙版
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, big - 1, big - 1], radius=int(big * 0.22), fill=255)
    bi.putalpha(mask)
    out = bi.resize((size, size), Image.LANCZOS)
    out.save(path)
    print("saved", path, size)

base = r"C:/Users/calebcys/WorkBuddy/2026-09-12-14-55-55/video-translator/static"
make_icon(192, base + "/icon-192.png")
make_icon(512, base + "/icon-512.png")
make_icon(180, base + "/apple-touch-icon.png")
