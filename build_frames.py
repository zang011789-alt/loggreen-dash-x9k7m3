"""
Frame 51 / 51_2 / 51_3 슬라이싱 + HTML 생성
"""
import os, shutil
from PIL import Image

SITE = r"C:\Users\zang0\Desktop\my-site"
VIDEO_SRC = r"D:\오프에고테스트용"

# 비디오 슬롯 위치 (현재 frame51.html 기준)
VIDEO_SLOTS = [
    (8555,  9470),   # slot 1
    (10264, 11179),  # slot 2
    (12075, 12990),  # slot 3
    (13840, 14755),  # slot 4
    (15604, 16519),  # slot 5
    (17366, 18281),  # slot 6
    (19202, 20117),  # slot 7
    (21303, 22218),  # slot 8
]
TOTAL_H = 24150
PAGE_W  = 1225

# 각 프레임별 설정
FRAMES = [
    {
        "png":     r"D:\Frame 51.png",
        "out_dir": "frame51_slices",
        "html":    "frame51.html",
        "videos":  ["1_1번gif.mp4","2번gif.mp4","3_1번gif.mp4","4번gif.mp4",
                    "5번gif.mp4","6번gif.mp4","7번gif.mp4","8번gif.mp4"],
    },
    {
        "png":     r"D:\Frame 51_2.png",
        "out_dir": "frame51_2_slices",
        "html":    "frame51_2.html",
        "videos":  ["1_2번gif.mp4","2번gif.mp4","3-2번gif.mp4","4번gif.mp4",
                    "5번gif.mp4","6번gif.mp4","7번gif.mp4","8번gif.mp4"],
    },
    {
        "png":     r"D:\Frame 51_3.png",
        "out_dir": "frame51_3_slices",
        "html":    "frame51_3.html",
        "videos":  ["1_2번gif.mp4","2번gif.mp4","3_3번gif.mp4","4번gif.mp4",
                    "5번gif.mp4","6번gif.mp4","7번gif.mp4","8번gif.mp4"],
    },
]

# 1) 영상 복사
print("=== 영상 복사 ===")
for f in os.listdir(VIDEO_SRC):
    if f.endswith(".mp4"):
        src = os.path.join(VIDEO_SRC, f)
        dst = os.path.join(SITE, f)
        shutil.copy2(src, dst)
        print(f"  복사: {f}")

# 정적 슬라이스 구간 계산
def get_static_ranges():
    ranges = []
    prev = 0
    for (vs, ve) in VIDEO_SLOTS:
        if prev < vs:
            ranges.append((prev, vs))
        prev = ve
    if prev < TOTAL_H:
        ranges.append((prev, TOTAL_H))
    return ranges

STATIC_RANGES = get_static_ranges()

# 2) 각 프레임 슬라이싱 + HTML 생성
for frame in FRAMES:
    print(f"\n=== {frame['html']} 처리 ===")
    img = Image.open(frame["png"])
    iw, ih = img.size
    print(f"  이미지 크기: {iw}x{ih}")

    out_dir = os.path.join(SITE, frame["out_dir"])
    os.makedirs(out_dir, exist_ok=True)

    # 슬라이싱
    slices = []
    for i, (y0, y1) in enumerate(STATIC_RANGES):
        # 원본 PNG가 24150px 기준이 아닐 경우 스케일 보정
        scale = ih / TOTAL_H
        crop = img.crop((0, round(y0*scale), iw, round(y1*scale)))
        # 1225px 너비로 리사이즈
        new_h = round((y1 - y0) * (iw / PAGE_W) * (PAGE_W / iw))
        crop = crop.resize((PAGE_W, y1 - y0), Image.LANCZOS)
        if crop.mode == "RGBA":
            bg = Image.new("RGB", crop.size, (0, 0, 0))
            bg.paste(crop, mask=crop.split()[3])
            crop = bg
        else:
            crop = crop.convert("RGB")
        path = os.path.join(out_dir, f"s{i}.jpg")
        crop.save(path, "JPEG", quality=92)
        slices.append((y0, y1 - y0, f"{frame['out_dir']}/s{i}.jpg"))
        print(f"  s{i}.jpg 저장 ({y0}~{y1})")

    # HTML 생성
    def make_section(top, h, src_or_vid, is_video=False):
        if is_video:
            return f'''
  <!-- 영상 슬롯 -->
  <div class="abs" style="top:{top}px;width:1225px;height:{h}px;">
    <video autoplay loop muted playsinline><source src="{src_or_vid}" type="video/mp4"></video>
  </div>'''
        else:
            return f'''
  <!-- 정적 -->
  <div class="abs" style="top:{top}px;width:1225px;height:{h}px;">
    <img class="slice" src="{src_or_vid}">
  </div>'''

    sections = ""
    si = 0
    for (y0, y1) in STATIC_RANGES:
        sections += make_section(y0, y1-y0, f"{frame['out_dir']}/s{si}.jpg")
        si += 1
        # 이 정적 구간 다음에 오는 비디오 슬롯 추가
        for vi, (vs, ve) in enumerate(VIDEO_SLOTS):
            if vs == y1:
                sections += make_section(vs, ve-vs, frame["videos"][vi], is_video=True)
                break

    html = f'''<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=1225">
<title>OFFEGO</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#111; display:flex; justify-content:center; }}
.page {{
  position:relative;
  width:1225px;
  height:{TOTAL_H}px;
  overflow:hidden;
}}
.abs {{ position:absolute; left:0; }}
img.slice {{ display:block; width:100%; height:100%; object-fit:cover; }}
video {{ display:block; width:100%; height:100%; object-fit:cover; }}
</style>
</head>
<body>
<!-- 상단 고정 헤더 -->
<div id="sticky-header" style="
  position:fixed;
  top:0;
  left:50%;
  transform:translateX(-50%);
  width:1225px;
  height:280px;
  overflow:hidden;
  z-index:9999;
">
  <img src="{frame['out_dir']}/s0.jpg" style="display:block;width:1225px;">
</div>

<div class="page">{sections}
</div>
</body>
</html>'''

    html_path = os.path.join(SITE, frame["html"])
    with open(html_path, "w", encoding="utf-8") as f2:
        f2.write(html)
    print(f"  {frame['html']} 생성 완료")

print("\n=== 전체 완료 ===")
