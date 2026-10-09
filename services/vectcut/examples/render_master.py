import subprocess
import os

ASSETS = r"c:\Users\Hemanshi Makwana\OneDrive\Documents\new\assets"
FONTS = os.path.join(ASSETS, "fonts")
CINZEL = os.path.join(FONTS, "Cinzel-Bold.ttf").replace("\\", "/").replace(":", "\\:")
PLAYFAIR = os.path.join(FONTS, "PlayfairDisplay-Bold.ttf").replace("\\", "/").replace(":", "\\:")
HINDI_FONT = "C\\:/Windows/Fonts/Nirmala.ttc"

# Write text files for complex Unicode characters to guarantee zero CLI encoding issues
f_hindi = os.path.join(FONTS, "s4_hindi.txt")
with open(f_hindi, "w", encoding="utf-8") as f:
    f.write("हेमांशी")
F_HINDI_ESC = f_hindi.replace("\\", "/").replace(":", "\\:")

c1 = os.path.join(ASSETS, "clip_1_hema.mp4")
c2 = os.path.join(ASSETS, "clip_2_anshi.mp4")
c3 = os.path.join(ASSETS, "clip_3_hemanshi.mp4")
c4 = os.path.join(ASSETS, "clip_4_resolution.mp4")

music = os.path.join(ASSETS, "music_processed.mp3")
vo = os.path.join(ASSETS, "voiceover_hindi_processed.mp3")
chime = os.path.join(ASSETS, "sfx_chime.mp3")
whoosh = os.path.join(ASSETS, "sfx_whoosh.mp3")

out_file = r"c:\Users\Hemanshi Makwana\OneDrive\Documents\new\Hemanshi_Master.mp4"

filter_complex = (
    # Normalize SAR to 1:1 and concatenate (exact 25.000s: 6.0 + 6.0 + 7.0 + 6.0)
    "[0:v]setsar=1[v0];[1:v]setsar=1[v1_in];[2:v]setsar=1[v2_in];[3:v]setsar=1[v3_in];"
    "[v0][v1_in][v2_in][v3_in]concat=n=4:v=1:a=0[vbase];"

    # 1. Scene 1 Text: Introductory Title "THE ESSENCE OF A NAME" (0:01.0 - 0:05.2) - Scaled to 68pt
    f"[vbase]drawtext=fontfile='{CINZEL}':text='THE ESSENCE OF A NAME':fontcolor=0xFFD700:fontsize=68:x=(w-text_w)/2:y=(h-text_h)/2:"
    "shadowcolor=black@0.95:shadowx=4:shadowy=4:"
    "box=1:boxcolor=black@0.25:boxborderw=18:"
    "alpha='if(lt(t,1.0),0,if(lt(t,1.8),(t-1.0)/0.8,if(lt(t,4.5),1,if(lt(t,5.3),(5.3-t)/0.8,0))))'[v1];"

    # 2. Scene 2 Text: Repositioned in upper clouds (y=h*0.20) with generous 125px vertical spacing
    # Subtitle scaled to 58pt, Main to 82pt
    f"[v1]drawtext=fontfile='{CINZEL}':text='HEMA  |  ANSHI':fontcolor=0xFFD700:fontsize=82:x=(w-text_w)/2:y=h*0.20:"
    "shadowcolor=black@0.95:shadowx=4:shadowy=4:"
    "box=1:boxcolor=black@0.40:boxborderw=16:"
    "alpha='if(lt(t,6.4),0,if(lt(t,7.2),(t-6.4)/0.8,if(lt(t,11.0),1,if(lt(t,11.8),(11.8-t)/0.8,0))))'[v2];"

    f"[v2]drawtext=fontfile='{PLAYFAIR}':text='Pure Gold  |  A Sacred Fragment':fontcolor=0xFFF5E1:fontsize=56:x=(w-text_w)/2:y=h*0.20+125:"
    "shadowcolor=black@0.95:shadowx=3:shadowy=3:"
    "box=1:boxcolor=black@0.40:boxborderw=16:"
    "alpha='if(lt(t,6.9),0,if(lt(t,7.6),(t-6.9)/0.7,if(lt(t,11.0),1,if(lt(t,11.8),(11.8-t)/0.8,0))))'[v3];"

    # 3. Scene 3 Text: Lower-third overlay on Hemanshi's photo (0:12.6 - 0:18.8)
    # Perfectly centered horizontally, generous 120px vertical space, subtitle scaled to 58pt
    f"[v3]drawtext=fontfile='{CINZEL}':text='HEMANSHI':fontcolor=0xFFD700:fontsize=88:x=(w-text_w)/2:y=h*0.77:"
    "shadowcolor=black@0.95:shadowx=4:shadowy=4:"
    "box=1:boxcolor=black@0.35:boxborderw=16:"
    "alpha='if(lt(t,12.6),0,if(lt(t,13.4),(t-12.6)/0.8,if(lt(t,18.0),1,if(lt(t,18.8),(18.8-t)/0.8,0))))'[v4];"

    f"[v4]drawtext=fontfile='{PLAYFAIR}':text='A Sacred Fragment of Gold':fontcolor=0xFFFFFF:fontsize=56:x=(w-text_w)/2:y=h*0.77+120:"
    "shadowcolor=black@0.95:shadowx=3:shadowy=3:"
    "box=1:boxcolor=black@0.35:boxborderw=16:"
    "alpha='if(lt(t,13.1),0,if(lt(t,13.9),(t-13.1)/0.8,if(lt(t,18.0),1,if(lt(t,18.8),(18.8-t)/0.8,0))))'[v5];"

    # 4. Scene 4 Text: Scaled Outro Typography with balanced, proportional vertical spacing
    # English: 135pt, Hindi: 100pt, Subtitle: 62pt (boosted from 48pt!)
    f"[v5]drawtext=fontfile='{CINZEL}':text='HEMANSHI':fontcolor=0xFFD700:fontsize=135:x=(w-text_w)/2:y=(h-text_h)/2-140:"
    "shadowcolor=black@0.95:shadowx=5:shadowy=5:"
    "alpha='if(lt(t,19.3),0,if(lt(t,20.0),(t-19.3)/0.7,if(lt(t,24.2),1,if(lt(t,24.9),(24.9-t)/0.7,0))))'[v6];"

    f"[v6]drawtext=fontfile='{HINDI_FONT}':textfile='{F_HINDI_ESC}':fontcolor=0xFFFFFF:fontsize=100:x=(w-text_w)/2:y=(h-text_h)/2-10:"
    "shadowcolor=black@0.95:shadowx=4:shadowy=4:"
    "alpha='if(lt(t,19.6),0,if(lt(t,20.3),(t-19.6)/0.7,if(lt(t,24.2),1,if(lt(t,24.9),(24.9-t)/0.7,0))))'[v7];"

    f"[v7]drawtext=fontfile='{PLAYFAIR}':text='Precious  -  Radiant  -  Pure':fontcolor=0xFFF5E1:fontsize=62:x=(w-text_w)/2:y=(h-text_h)/2+130:"
    "shadowcolor=black@0.95:shadowx=4:shadowy=4:"
    "alpha='if(lt(t,20.0),0,if(lt(t,20.7),(t-20.0)/0.7,if(lt(t,24.2),1,if(lt(t,24.9),(24.9-t)/0.7,0))))'[vout];"

    # 5. Audio mix
    "[4:a]volume=eval=frame:volume='if(between(t,0.5,5.5)+between(t,6.4,11.9)+between(t,12.3,18.9)+between(t,19.4,23.2),-13dB,-5.5dB)'[amusic];"
    "[5:a]volume=1.25[avo];"
    "[6:a]adelay=400|400,volume=0.45[achime];"
    "[7:a]adelay=5800|5800,volume=0.4[awhoosh];"
    "[amusic][avo][achime][awhoosh]amix=inputs=4:duration=first:dropout_transition=2[aout]"
)

cmd = [
    "ffmpeg", "-y",
    "-i", c1,
    "-i", c2,
    "-i", c3,
    "-i", c4,
    "-i", music,
    "-i", vo,
    "-i", chime,
    "-i", whoosh,
    "-filter_complex", filter_complex,
    "-map", "[vout]",
    "-map", "[aout]",
    "-c:v", "libx264",
    "-preset", "slow",
    "-crf", "17",
    "-pix_fmt", "yuv420p",
    "-c:a", "aac",
    "-b:a", "320k",
    "-t", "25.0",
    out_file
]

print("Rendering Polished Master Video...")
subprocess.run(cmd, check=True)
print("Master Video rendered successfully to:", out_file)
print("File size:", os.path.getsize(out_file), "bytes")
