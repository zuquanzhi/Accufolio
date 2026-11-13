"""Install the release APK in a disposable emulator and validate onboarding."""
import pathlib,re,subprocess,sys,time,xml.etree.ElementTree as ET

def adb(*args):return subprocess.check_output(['adb',*args],text=True)
def screen():
    adb('shell','uiautomator','dump','/sdcard/accufolio-ui.xml')
    return ET.fromstring(adb('shell','cat','/sdcard/accufolio-ui.xml'))
def tap(node):
    x1,y1,x2,y2=map(int,re.findall(r'\d+',node.attrib['bounds']))
    adb('shell','input','tap',str((x1+x2)//2),str((y1+y2)//2))

apk=sys.argv[1]
subprocess.run(['adb','install','-r',apk],check=True)
adb('shell','am','start','-W','-n','io.accufolio.app/.MainActivity')
time.sleep(3)
ui=screen()
texts=[n.attrib.get('text','') for n in ui.iter('node')]
assert 'Accufolio' in texts and '连接服务器' in texts,texts
entry=next(n for n in ui.iter('node') if n.attrib.get('class')=='android.widget.EditText')
tap(entry)
adb('shell','input','text','http://example.com')
adb('shell','input','keyevent','4')
ui=screen(); tap(next(n for n in ui.iter('node') if n.attrib.get('text')=='连接服务器'))
time.sleep(1)
ui=screen();texts=[n.attrib.get('text','') for n in ui.iter('node')]
assert any('请输入完整 HTTPS' in t for t in texts),texts
assert adb('shell','pidof','io.accufolio.app').strip()
pathlib.Path('work').mkdir(exist_ok=True)
with open('work/android-onboarding.png','wb') as f:subprocess.run(['adb','exec-out','screencap','-p'],stdout=f,check=True)
print('Signed APK installation, launch, onboarding and cleartext URL rejection passed.')
