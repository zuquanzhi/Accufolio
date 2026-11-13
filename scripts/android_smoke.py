"""Install the APK in a disposable emulator and validate onboarding and recovery."""
import pathlib,re,subprocess,sys,time,xml.etree.ElementTree as ET

def adb(*args):return subprocess.check_output(['adb',*args],text=True)
def screen():
    adb('shell','uiautomator','dump','/sdcard/accufolio-ui.xml')
    return ET.fromstring(adb('shell','cat','/sdcard/accufolio-ui.xml'))
def bounds(node):return list(map(int,re.findall(r'\d+',node.attrib['bounds'])))
def tap(node):
    x1,y1,x2,y2=bounds(node)
    adb('shell','input','tap',str((x1+x2)//2),str((y1+y2)//2))
def named(ui,text):return next(n for n in ui.iter('node') if n.attrib.get('text')==text)
def capture(name):
    with open('work/'+name,'wb') as f:subprocess.run(['adb','exec-out','screencap','-p'],stdout=f,check=True)
def texts(ui):return [n.attrib.get('text','') for n in ui.iter('node')]

apk=sys.argv[1]
subprocess.run(['adb','install','-r',apk],check=True)
adb('shell','pm','clear','io.accufolio.app')
adb('shell','am','start','-W','-n','io.accufolio.app/.MainActivity')
time.sleep(3)
ui=screen()
assert 'Accufolio' in texts(ui) and '连接' in texts(ui),texts(ui)
pathlib.Path('work').mkdir(exist_ok=True)
capture('android-welcome.png')
# Welcome settings is reachable near the bottom; update is inside its sheet.
size=list(map(int,re.findall(r'\d+',adb('shell','wm','size'))))[-2:]
settings=named(ui,'应用设置');assert bounds(settings)[1]>size[1]*.6
tap(settings);ui=screen();assert '检查更新' in texts(ui) and '完成' in texts(ui)
assert bounds(named(ui,'完成'))[1]>size[1]*.65
tap(named(ui,'完成'));ui=screen()
entry=next(n for n in ui.iter('node') if n.attrib.get('class')=='android.widget.EditText')
tap(entry);adb('shell','input','text','http://example.com');adb('shell','input','keyevent','4')
ui=screen();tap(named(ui,'连接'));time.sleep(.5)
ui=screen();assert any('请输入完整 HTTPS' in t for t in texts(ui)),texts(ui)
capture('android-onboarding.png')
# A non-existent HTTPS host must retain a native recovery path, even without web UI.
entry=next(n for n in ui.iter('node') if n.attrib.get('class')=='android.widget.EditText')
tap(entry);adb('shell','input','keyevent','123');adb('shell','input','keyevent',*(['67']*40))
adb('shell','input','text','https://accufolio.invalid');adb('shell','input','keyevent','4')
ui=screen();tap(named(ui,'连接'))
for _ in range(20):
    time.sleep(.5);ui=screen()
    if any('暂时无法连接' in t for t in texts(ui)):break
assert any('暂时无法连接' in t for t in texts(ui)),texts(ui)
assert not any(t in ['刷新','服务器','更新'] for t in texts(ui)),texts(ui)
recovery=named(ui,'连接设置');assert bounds(recovery)[1]>size[1]*.65
tap(recovery);ui=screen()
for text in ['连接地址','重新载入','检查更新','完成']:assert text in texts(ui),texts(ui)
assert bounds(named(ui,'完成'))[1]>size[1]*.65
capture('android-settings.png')
# Inspecting settings does not navigate away; reload has an explicit confirmation.
tap(named(ui,'重新载入'));ui=screen();assert '未保存的输入会被清空，已保存的记录不受影响。' in texts(ui)
tap(named(ui,'取消'));ui=screen();assert '连接设置' in texts(ui)
assert adb('shell','pidof','io.accufolio.app').strip()
print('APK onboarding, HTTPS validation, connection recovery and bottom settings checks passed.')
