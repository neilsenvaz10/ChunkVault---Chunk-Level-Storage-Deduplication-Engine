"""Render the repository's end-to-end explainer as a captioned animated GIF."""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


W, H = 1280, 720
BG = (10, 17, 31)
PANEL = (20, 31, 51)
WHITE = (235, 243, 255)
MUTED = (157, 177, 204)
CYAN = (68, 211, 232)
BLUE = (91, 143, 255)
GREEN = (77, 218, 161)
AMBER = (255, 190, 92)
PURPLE = (177, 139, 255)
OUT = Path(__file__).resolve().parents[1] / "docs" / "ChunkVault_End_to_End.gif"


def font(size, bold=False):
    paths = [
        Path("C:/Windows/Fonts/seguisb.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
    ]
    for path in paths:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


F_TITLE, F_BODY, F_SMALL, F_KICK = font(48, True), font(27), font(20), font(18, True)


def rounded(draw, box, fill, outline=None, radius=18, width=2):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def centered(draw, xy, text, f, fill=WHITE):
    draw.text(xy, text, font=f, fill=fill, anchor="mm")


def wrapped(draw, x, y, text, f, fill=MUTED, max_width=1100, leading=8):
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = (line + " " + word).strip()
        if draw.textbbox((0, 0), trial, font=f)[2] <= max_width:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    for line in lines:
        draw.text((x, y), line, font=f, fill=fill)
        y += f.size + leading
    return y


def arrow(draw, a, b, color=CYAN, width=5):
    draw.line((a, b), fill=color, width=width)
    import math
    angle = math.atan2(b[1] - a[1], b[0] - a[0])
    size = 16
    p1 = (b[0] - size * math.cos(angle - .55), b[1] - size * math.sin(angle - .55))
    p2 = (b[0] - size * math.cos(angle + .55), b[1] - size * math.sin(angle + .55))
    draw.polygon((b, p1, p2), fill=color)


def base(scene, frame, total, section, title, subtitle):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.ellipse((1020, -360, 1510, 130), fill=(15, 34, 56))
    d.text((62, 42), "CHUNKVAULT   /   END-TO-END", font=F_KICK, fill=CYAN)
    d.text((62, 83), title, font=F_TITLE, fill=WHITE)
    d.text((64, 150), subtitle, font=F_BODY, fill=MUTED)
    d.text((W - 65, 48), f"{scene:02d}  /  08", font=F_SMALL, fill=MUTED, anchor="ra")
    d.line((62, 195, W - 62, 195), fill=(41, 59, 83), width=2)
    d.text((64, 672), section, font=F_SMALL, fill=(123, 148, 178))
    d.rounded_rectangle((62, 704, W - 62, 712), radius=4, fill=(32, 48, 70))
    d.rounded_rectangle((62, 704, 62 + int((W - 124) * frame / total), 712), radius=4, fill=CYAN)
    return im, d


def chip(d, box, title, desc, color):
    rounded(d, box, PANEL, color, 18, 2)
    x1, y1, x2, y2 = box
    centered(d, ((x1+x2)//2, y1+31), title, F_KICK, color)
    centered(d, ((x1+x2)//2, y1+72), desc, F_SMALL, WHITE)


def scene_content(i, d, t):
    if i == 0:
        rounded(d, (120, 260, 1160, 590), PANEL, (42, 64, 91), 28)
        centered(d, (640, 330), "Store files once, reuse their repeated content", font(38, True), WHITE)
        centered(d, (640, 405), "ChunkVault is a local, content-addressed deduplication engine.", F_BODY, MUTED)
        for j, color in enumerate((BLUE, CYAN, PURPLE, GREEN)):
            rounded(d, (310+j*170, 475, 435+j*170, 535), (27, 45, 70), color, 12, 3)
            centered(d, (372+j*170, 505), f"file {j+1}", F_SMALL, WHITE)
    elif i == 1:
        chip(d, (95, 285, 300, 410), "React UI", "select + upload", BLUE)
        chip(d, (390, 285, 595, 410), "FastAPI", "validate request", CYAN)
        chip(d, (685, 285, 890, 410), "Uploader", "orchestrate flow", PURPLE)
        chip(d, (980, 235, 1185, 355), "SQLite", "metadata", GREEN)
        chip(d, (980, 420, 1185, 540), "Chunk store", "file bytes", AMBER)
        arrow(d, (300, 345), (390, 345)); arrow(d, (595, 345), (685, 345))
        arrow(d, (890, 320), (980, 295)); arrow(d, (890, 375), (980, 480), AMBER)
        centered(d, (640, 505), "One FastAPI process  ·  sequential pipeline  ·  local machine", F_BODY, MUTED)
    elif i == 2:
        rounded(d, (105, 265, 1175, 585), PANEL, (42, 64, 91), 22)
        centered(d, (640, 315), "Incoming file", F_KICK, CYAN)
        # Input byte ribbon and variable chunk boundaries.
        for x, w, c, label in [(180,145,BLUE,"chunk 1"),(325,210,CYAN,"chunk 2"),(535,135,PURPLE,"chunk 3"),(670,230,GREEN,"chunk 4"),(900,150,AMBER,"chunk 5")]:
            rounded(d, (x, 360, x+w, 425), (28, 43, 65), c, 9, 3)
            centered(d, (x+w//2, 392), label, F_SMALL, WHITE)
        centered(d, (640, 475), "Rabin-style rolling fingerprint finds content-defined boundaries", F_BODY, WHITE)
        centered(d, (640, 530), "A small insertion shifts nearby boundaries; later chunks can resynchronize.", F_SMALL, MUTED)
    elif i == 3:
        chip(d, (110, 310, 380, 455), "Chunk bytes", "variable-sized blocks", BLUE)
        chip(d, (505, 310, 775, 455), "SHA-256", "stable chunk identity", CYAN)
        chip(d, (900, 270, 1170, 400), "Known hash", "reuse existing bytes", GREEN)
        chip(d, (900, 445, 1170, 575), "New hash", "publish new bytes", AMBER)
        arrow(d, (380, 382), (505, 382)); arrow(d, (775, 360), (900, 335)); arrow(d, (775, 410), (900, 510), AMBER)
        centered(d, (640, 535), "Rolling fingerprint chooses boundaries; cryptographic hash names the content.", F_SMALL, MUTED)
    elif i == 4:
        chip(d, (105, 285, 390, 415), "1 · Stage", "stream + whole-file hash", BLUE)
        chip(d, (495, 285, 780, 415), "2 · Publish", "fsync + atomic rename", CYAN)
        chip(d, (885, 285, 1170, 415), "3 · Commit", "manifest + complete", GREEN)
        arrow(d, (390, 350), (495, 350)); arrow(d, (780, 350), (885, 350))
        rounded(d, (155, 465, 1125, 585), (29, 33, 48), (105, 76, 56), 18)
        centered(d, (640, 500), "Consistency protocol", F_KICK, AMBER)
        centered(d, (640, 545), "Bytes are durable before SQLite commits the file-to-chunk manifest.", F_BODY, WHITE)
    elif i == 5:
        rounded(d, (100, 270, 625, 570), PANEL, (42, 64, 91), 20)
        centered(d, (362, 310), "SQLite metadata", F_KICK, CYAN)
        for k, txt in enumerate(("files  →  filename, status, file hash", "chunks  →  hash, size, ref_count", "file_chunks  →  ordered manifest", "operations  →  upload / restore state")):
            d.text((145, 365+k*46), txt, font=F_SMALL, fill=WHITE)
        rounded(d, (690, 270, 1180, 570), PANEL, (42, 64, 91), 20)
        centered(d, (935, 310), "Local chunk store", F_KICK, AMBER)
        for k, txt in enumerate(("chunks/ab/<full SHA-256>", "one payload per unique hash", "atomic publish of staged bytes")):
            centered(d, (935, 380+k*55), txt, F_SMALL, WHITE)
        arrow(d, (625, 420), (690, 420), PURPLE)
        centered(d, (640, 625), "The hash is the link between metadata and stored bytes.", F_BODY, MUTED)
    elif i == 6:
        chip(d, (115, 295, 375, 435), "Manifest", "ordered chunk hashes", BLUE)
        chip(d, (510, 295, 770, 435), "Verify", "each chunk SHA-256", CYAN)
        chip(d, (905, 295, 1165, 435), "Reassemble", "stream original bytes", GREEN)
        arrow(d, (375, 365), (510, 365)); arrow(d, (770, 365), (905, 365))
        rounded(d, (205, 495, 1075, 590), (25, 47, 48), GREEN, 16, 2)
        centered(d, (640, 525), "Then verify total size and whole-file SHA-256", F_KICK, GREEN)
        centered(d, (640, 562), "Missing, reordered, or corrupted content fails the restore.", F_SMALL, WHITE)
    elif i == 7:
        chip(d, (110, 280, 370, 420), "Delete", "remove file manifest", BLUE)
        chip(d, (510, 280, 770, 420), "Ref counts", "shared chunks remain", CYAN)
        chip(d, (910, 280, 1170, 420), "Audit", "detect store drift", PURPLE)
        arrow(d, (370, 350), (510, 350)); arrow(d, (770, 350), (910, 350))
        rounded(d, (140, 475, 1140, 595), PANEL, (42, 64, 91), 18)
        centered(d, (640, 510), "Recovery and operating scope", F_KICK, AMBER)
        centered(d, (640, 552), "Reconcile crash leftovers · one writer · one machine · no distributed mode", F_SMALL, WHITE)


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    titles = [
        ("Why ChunkVault?", "Near-duplicate files can share the same stored chunks."),
        ("The request path", "The browser asks the API; the backend coordinates the storage work."),
        ("Content-defined chunking", "Rabin-style CDC splits the byte stream around content changes."),
        ("Hash and deduplicate", "Each chunk hash tells the system whether its payload is already stored."),
        ("Safe upload commit", "Stage first, publish bytes, then commit metadata in one SQLite transaction."),
        ("Two storage layers", "SQLite describes files and chunk relationships; the filesystem holds bytes."),
        ("Verified restore", "Read the manifest in order, validate every chunk, then validate the full file."),
        ("Lifecycle and limits", "Reference counts protect shared data; audits detect inconsistencies."),
    ]
    frames = []
    per_scene = 18
    total = len(titles) * per_scene
    for i, (title, subtitle) in enumerate(titles):
        for local in range(per_scene):
            global_frame = i * per_scene + local
            im, d = base(i + 1, global_frame + 1, total, "CHUNKVAULT  ·  LOCAL SINGLE-MACHINE PROTOTYPE", title, subtitle)
            scene_content(i, d, local)
            # Short, high-contrast caption pinned below the visual diagram.
            caption_y = 628
            captions = [
                "Deduplication saves repeated payloads while keeping file-level identities.",
                "React → FastAPI → upload orchestrator → metadata + local chunk store.",
                "Content-defined boundaries keep later chunks reusable after an insertion.",
                "Equal content produces an equal SHA-256 address and one stored payload.",
                "If the upload fails before commit, cleanup runs; audits find crash leftovers.",
                "Metadata and bytes are separate, joined by each chunk's SHA-256 hash.",
                "The final whole-file digest confirms the restored bytes match the upload.",
                "This prototype is sequential and single-process; it does not claim distribution.",
            ][i]
            # A translucent-looking solid strip ensures caption readability.
            rounded(d, (70, caption_y-8, 1210, caption_y+31), (15, 26, 43), None, 10, 0)
            centered(d, (640, caption_y+11), captions, F_SMALL, WHITE)
            frames.append(im)
    # Use one shared palette across all frames so captions and accent colors stay stable.
    sample = Image.new("RGB", (128 * 12, 72 * 12), BG)
    for j, frame in enumerate(frames):
        sample.paste(frame.resize((128, 72)), ((j % 12) * 128, (j // 12) * 72))
    palette = sample.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    frames = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    frames[0].save(OUT, save_all=True, append_images=frames[1:], duration=160, loop=0, optimize=True, disposal=2)
    print(f"Wrote {OUT} ({OUT.stat().st_size:,} bytes, {total} frames)")


if __name__ == "__main__":
    main()
