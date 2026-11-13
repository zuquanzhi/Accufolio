"""Create a device-frame poster from real captures (requires Pillow and Windows fonts)."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter
root=Path(__file__).resolve().parents[1]; out=root/'assets/previews'
w,h=1800,1430
img=Image.new('RGB',(w,h)); px=img.load()
for y in range(h):
 for x in range(w):
  glow=max(0,1-(((x-1180)/1500)**2+((y-500)/1050)**2)**.5)
  px[x,y]=(int(11+12*glow),int(13+15*glow),int(17+23*glow))
d=ImageDraw.Draw(img)
font='C:/Windows/Fonts/segoeui.ttf'; bold='C:/Windows/Fonts/segoeuib.ttf'
d.text((145,100),'ACCUFOLIO',font=ImageFont.truetype(bold,25),fill='#b1bbca',stroke_width=0)
d.text((145,145),'Agent-driven asset allocation.',font=ImageFont.truetype(bold,64),fill='#f2f4f6')
d.text((148,230),'Named accounts. Clear investments. Managed by your agent.',font=ImageFont.truetype(font,25),fill='#9da8b5')
shadow=Image.new('RGBA',(w,h));sd=ImageDraw.Draw(shadow);sd.rounded_rectangle((112,310,1450,1282),radius=28,fill=(0,0,0,185));sd.rounded_rectangle((1370,495,1710,1250),radius=55,fill=(0,0,0,230));img=Image.alpha_composite(img.convert('RGBA'),shadow.filter(ImageFilter.GaussianBlur(36)));d=ImageDraw.Draw(img)
# Laptop-like browser frame around an unmodified application screenshot.
x,y=120,305; sw=1300; sh=round(sw*Image.open(out/'poster-web-overview.png').height/Image.open(out/'poster-web-overview.png').width); d.rounded_rectangle((x,y,x+sw+20,y+sh+66),radius=20,fill='#30353d',outline='#636a74',width=2);d.rounded_rectangle((x+9,y+9,x+sw+11,y+sh+54),radius=12,fill='#0f1217');d.rectangle((x+10,y+40,x+sw+10,y+40+sh),fill='#101217');
for i,c in enumerate(['#e07975','#ddbd72','#78bd88']):d.ellipse((x+24+i*19,y+20,x+34+i*19,y+30),fill=c)
screen=Image.open(out/'poster-web-overview.png').convert('RGB').resize((sw,sh),Image.Resampling.LANCZOS);img.paste(screen,(x+10,y+40));d=ImageDraw.Draw(img);d.rounded_rectangle((x-40,y+sh+66,x+sw+60,y+sh+82),radius=6,fill='#5e6570');d.rounded_rectangle((x+510,y+sh+66,x+810,y+sh+72),radius=3,fill='#404650')
# Phone-like frame, using the actual responsive browser capture.
px,py=1382,504; pw=282; ph=round(pw*844/390);d.rounded_rectangle((px-12,py-13,px+pw+12,py+ph+13),radius=41,fill='#07090c',outline='#8c949e',width=3)
phone=Image.open(out/'mobile-investments.png').convert('RGBA').resize((pw,ph),Image.Resampling.LANCZOS);mask=Image.new('L',(pw,ph));ImageDraw.Draw(mask).rounded_rectangle((0,0,pw,ph),radius=30,fill=255);img.paste(phone,(px,py),mask);d=ImageDraw.Draw(img);d.rounded_rectangle((px+96,py+9,px+186,py+24),radius=8,fill='#07090c')
d.text((145,1360),'ACTUAL INTERFACE  /  SYNTHETIC DATA',font=ImageFont.truetype(bold,17),fill='#8190a1')
img.convert('RGB').save(out/'product-poster.jpg',quality=91,optimize=True)

# Five actual route views, useful for reviewing navigation without a promotional frame.
page_images=[('mobile-overview.png','/assets'),('mobile-investments.png','/investments'),('mobile-trades.png','/records'),('mobile-discover.png','/discover'),('mobile-settings.png','/settings')]
if all((out/name).exists() for name,_ in page_images):
    overview=Image.new('RGB',(2130,960),'#edf0f7')
    draw=ImageDraw.Draw(overview)
    for index,(name,route) in enumerate(page_images):
        left=30+index*420
        draw.text((left+12,24),route,font=ImageFont.truetype(font,25),fill='#232c41')
        overview.paste(Image.open(out/name).convert('RGB'),(left,76))
    overview.save(out/'mobile-pages.jpg',quality=92,optimize=True)
